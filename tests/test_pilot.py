import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget

from app.bootstrap import create_window
from app.services.paths import database_path
from app.services.records import Records
from app.ui.help import HelpDialog
from main import main


def test_removed_demo_argument_cannot_open_working_database(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["AutoService", "--demo"])
    assert main() == 2
    assert not database_path().exists()
    assert list(tmp_path.iterdir()) == []


def test_pilot_keyboard_help_and_selection_survive_navigation(application, tmp_path):
    window = create_window(tmp_path / "pilot.sqlite3")
    try:
        first = window.records.save_contact({"name": "Первый", "phone": "000"})
        second = window.records.save_contact({"name": "Второй", "phone": "111"})
        window.records.save_vehicle({"plate": "ПЕРВАЯ", "contact_id": first})
        window.records.save_vehicle({"plate": "ВТОРАЯ", "contact_id": second})
        window.show()
        window.activateWindow()
        application.processEvents()
        assert [window.navigation.item(i).text() for i in range(window.navigation.count())] == [
            "Автомобили",
            "Незавершённые ремонты",
            "Клиенты",
            "Звонки",
            "Записи",
            "Настройки",
        ]
        page = window.vehicles_page
        page.refresh()
        page.table.selectRow(1)
        selected = page.rows[1]["id"]
        window.navigation.setCurrentRow(1)
        window.navigation.setCurrentRow(0)
        assert page.rows[page.table.currentRow()]["id"] == selected
        QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        assert window.unfinished_repairs_page.search.hasFocus()
        QTest.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        assert window.customers_page.search.hasFocus()
        QTest.keyClick(window, Qt.Key.Key_5, Qt.KeyboardModifier.ControlModifier)
        assert window.navigation.currentItem().text() == "Записи"
        QTest.keyClick(window, Qt.Key.Key_6, Qt.KeyboardModifier.ControlModifier)
        assert window.navigation.currentItem().text() == "Настройки"
        before = Path(window.db_path).read_bytes()
        captured = {}

        def inspect_help():
            dialog = application.activeModalWidget()
            if isinstance(dialog, HelpDialog):
                captured["guide"] = dialog.guide.toPlainText()
                captured["has_tabs"] = bool(dialog.findChildren(QTabWidget))
                dialog.reject()

        QTimer.singleShot(100, inspect_help)
        watchdog = QTimer(window)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(
            lambda: application.activeModalWidget() and application.activeModalWidget().reject()
        )
        watchdog.start(2000)
        QTest.keyClick(window, Qt.Key.Key_F1)
        watchdog.stop()
        assert not captured["has_tabs"]
        guide = captured["guide"]
        assert "Добавить автомобиль" in guide
        assert "Завершить ремонт" in guide
        assert "демонстрац" not in guide.casefold()
        assert Path(window.db_path).read_bytes() == before
        assert len(Records(window.db_path).vehicles()) == 2
    finally:
        window.close()
