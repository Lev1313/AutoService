import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTableWidget

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


def test_window_and_navigation(application, tmp_path):
    window = create_window(tmp_path / "test.sqlite3")
    try:
        window.show()
        application.processEvents()
        assert window.isVisible()
        assert window.navigation.count() == len(SECTIONS)
        for index in range(len(SECTIONS)):
            window.navigation.setCurrentRow(index)
            application.processEvents()
            assert window.pages.currentIndex() == index
        window.navigation.setFocus()
        QTest.keyClick(window.navigation, Qt.Key.Key_Home)
        assert window.pages.currentIndex() == 0
        QTest.keyClick(window.navigation, Qt.Key.Key_Down)
        assert window.pages.currentIndex() == 1
        table = window.pages.widget(1).findChild(QTableWidget)
        assert table.rowCount() == 3
        assert table.item(0, 3).text() == "Демонстрация"
    finally:
        window.close()
