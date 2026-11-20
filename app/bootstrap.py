"""Запуск Qt и безопасная подготовка пользовательских данных."""

import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app.database.connection import initialize_database
from app.services.paths import database_path
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme
from app.version import VERSION


def create_application(argv: list[str] | None = None) -> QApplication:
    application = QApplication.instance()
    if application is None:
        application = QApplication(sys.argv if argv is None else argv)
    application.setApplicationName("AutoService")
    application.setApplicationVersion(VERSION)
    application.setOrganizationName("AutoService")
    application.setWindowIcon(QIcon(str(Path(__file__).parent / "assets" / "autoservice.ico")))
    application.setStyle("Fusion")
    apply_theme(application, "light")
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    return application


def create_window(path: Path | None = None, *, demo: bool = False) -> MainWindow:
    path = database_path(demo=demo) if path is None else path
    initialize_database(path)
    return MainWindow(path, demo=demo)


def acquire_data_lock(path: Path) -> QLockFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path.parent / "autoservice.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise RuntimeError(
            "AutoService уже открыт или папка занята другим экземпляром. "
            "Закройте открытое окно и повторите запуск."
        )
    return lock


def run(*, demo: bool = False) -> int:
    application = create_application()
    path = database_path(demo=demo)
    try:
        lock = acquire_data_lock(path)
        window = create_window(path, demo=demo)
    except (OSError, sqlite3.Error, RuntimeError) as error:
        QMessageBox.critical(
            None,
            "Не удалось открыть AutoService",
            "Не удалось подготовить базу данных. Проверьте доступ к папке:\n"
            f"{path.parent}\n\n"
            "Не удаляйте существующую базу.\n\n"
            f"Подробности: {error}",
        )
        return 1
    window.show()
    try:
        return application.exec()
    finally:
        lock.unlock()
