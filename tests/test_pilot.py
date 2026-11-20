from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest

from app.bootstrap import create_window
from app.services.paths import database_path
from app.services.records import Records
from app.ui.help import HelpDialog


def test_demo_is_separate_persistent_and_does_not_touch_working_database(
    application, tmp_path, monkeypatch
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    working = create_window()
    work_car = working.records.save_vehicle({"plate": "РАБОЧАЯ"})
    before = database_path().read_bytes()
    demo = create_window(demo=True)
    try:
        assert demo.db_path != working.db_path
        assert demo.records.vehicles() == []
        assert "демонстрационный режим" in demo.windowTitle()
        demo.records.save_vehicle({"plate": "ПРОБНАЯ"})
        assert database_path().read_bytes() == before
        assert working.records.vehicles()[0]["id"] == work_car
        demo.close()
        reopened = create_window(demo=True)
        assert reopened.records.vehicles()[0]["plate"] == "ПРОБНАЯ"
        reopened.close()
    finally:
        demo.close()
        working.close()


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
            "Клиенты",
            "Звонки",
            "Настройки",
            "Записи",
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
        assert window.customers_page.search.hasFocus()
        QTest.keyClick(window, Qt.Key.Key_4, Qt.KeyboardModifier.ControlModifier)
        assert window.navigation.currentItem().text() == "Настройки"
        before = Path(window.db_path).read_bytes()
        captured = []

        def inspect_help():
            dialog = application.activeModalWidget()
            if isinstance(dialog, HelpDialog):
                captured.extend(
                    dialog.tabs.widget(i).toPlainText() for i in range(dialog.tabs.count())
                )
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
        assert len(captured) == 2
        assert "Добавить автомобиль" in captured[0]
        assert "Завершить ремонт" in captured[1]
        assert all("вопросы механику" not in text.casefold() for text in captured)
        assert Path(window.db_path).read_bytes() == before
        assert len(Records(window.db_path).vehicles()) == 2
    finally:
        window.close()
