import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest

from app.bootstrap import create_window
from app.services.paths import data_directory, database_path
from app.ui.main_window import SECTIONS


def test_imports_have_no_filesystem_side_effects(tmp_path):
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "import main; import app.bootstrap; import app.ui.main_window"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_user_data_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_directory() == tmp_path / "AutoService"
    assert database_path() == tmp_path / "AutoService" / "autoservice.sqlite3"
    assert not data_directory().exists()


def test_appearance_switch_persists_for_the_same_database(application, tmp_path):
    path = tmp_path / "test.sqlite3"
    window = create_window(path)
    light = application.palette().color(QPalette.ColorRole.Window)
    window.theme_select.setCurrentIndex(window.theme_select.findData("dark"))
    dark = application.palette().color(QPalette.ColorRole.Window)
    assert dark.lightness() < light.lightness()
    assert application.property("appearanceTheme") == "dark"
    window.close()

    reopened = create_window(path)
    try:
        assert reopened.theme_select.currentData() == "dark"
        assert application.palette().color(QPalette.ColorRole.Window) == dark
        reopened.theme_select.setCurrentIndex(reopened.theme_select.findData("light"))
        assert application.palette().color(QPalette.ColorRole.Window) == light
    finally:
        reopened.close()


def test_window_and_navigation(application, tmp_path):
    window = create_window(tmp_path / "test.sqlite3")
    try:
        window.show()
        application.processEvents()
        assert window.isVisible()
        assert window.pages.currentWidget() is window.vehicles_page
        assert window.navigation.count() == len(SECTIONS)
        for index in range(len(SECTIONS)):
            window.navigation.setCurrentRow(index)
            application.processEvents()
            assert window.pages.currentIndex() == index
        window.navigation.setFocus()
        QTest.keyClick(window.navigation, Qt.Key.Key_Home)
        assert window.pages.currentWidget() is window.vehicles_page
        QTest.keyClick(window.navigation, Qt.Key.Key_Down)
        assert window.pages.currentIndex() == 1
        assert window.pages.currentWidget() is window.customers_page
        assert window.navigation.currentItem().text() == "Клиенты"
        window.focus_search()
        assert window.pages.currentWidget() is window.customers_page
        assert window.customers_page.search.hasFocus()
        window.navigation.setCurrentRow(0)
        window.focus_search()
        assert window.pages.currentWidget() is window.vehicles_page
        table = window.vehicles_page.table
        assert table.rowCount() == 0
        window.records.save_vehicle({"make": "Лада", "plate": "ТЕСТ 123"})
        window.vehicles_page.refresh()
        assert table.rowCount() == 1
        assert table.item(0, 0).text() == "Лада"
        window.vehicles_page.search.setText("тест123")
        assert table.rowCount() == 1
        window.vehicles_page.search.setText("Несуществующий")
        assert table.rowCount() == 0
    finally:
        window.close()
