"""Запуск Qt и безопасная подготовка пользовательских данных."""

import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QMessageBox

from app.database.connection import initialize_database
from app.services.paths import database_path
from app.ui.main_window import MainWindow


def create_application(argv: list[str] | None = None) -> QApplication:
    application = QApplication.instance()
    if application is None:
        application = QApplication(sys.argv if argv is None else argv)
    application.setApplicationName("AutoService")
    application.setOrganizationName("AutoService")
    application.setStyle("Fusion")
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    return application


def create_window(path: Path | None = None) -> MainWindow:
    path = database_path() if path is None else path
    initialize_database(path)
    return MainWindow(path)


def run() -> int:
    application = create_application()
    try:
        window = create_window()
    except (OSError, sqlite3.Error, RuntimeError) as error:
        QMessageBox.critical(
            None,
            "Не удалось открыть AutoService",
            "Не удалось подготовить базу данных. Проверьте доступ к папке:\n"
            f"{database_path().parent}\n\n"
            "Не удаляйте существующую базу.\n\n"
            f"Подробности: {error}",
        )
        return 1
    window.show()
    return application.exec()
