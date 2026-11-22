"""Запуск Qt и безопасная подготовка пользовательских данных."""

import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QLockFile
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.database.connection import (
    DatabaseRecoveryRequired,
    check_database_at_startup,
    initialize_database,
    recover_database,
)
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


def create_window(path: Path | None = None) -> MainWindow:
    path = database_path() if path is None else path
    check_database_at_startup(path.resolve())
    initialize_database(path)
    return MainWindow(path)


def open_startup_window(path: Path) -> MainWindow | None:
    try:
        return create_window(path)
    except DatabaseRecoveryRequired as error:
        reason = str(error)
    while True:
        prompt = QMessageBox()
        prompt.setWindowTitle("Восстановление рабочей базы")
        prompt.setIcon(QMessageBox.Icon.Warning)
        prompt.setText(reason)
        prompt.setInformativeText(
            "Выберите резервную копию. Программа проверит её и сохранит исходные файлы "
            "рабочей базы перед восстановлением."
        )
        choose = prompt.addButton("Выбрать копию…", QMessageBox.ButtonRole.AcceptRole)
        cancel = prompt.addButton("Выйти", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(cancel)
        prompt.setEscapeButton(cancel)
        prompt.exec()
        if prompt.clickedButton() != choose:
            return None
        backups = path.parent / "backups"
        filename, _ = QFileDialog.getOpenFileName(
            None,
            "Выбрать резервную копию",
            str(backups if backups.is_dir() else path.parent),
            "База AutoService (*.sqlite3)",
        )
        if not filename:
            continue
        try:
            original = recover_database(path, Path(filename))
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(None, "Не удалось восстановить базу", str(error))
            continue
        QMessageBox.information(
            None, "База восстановлена", f"Исходный файл сохранён здесь:\n{original}"
        )
        return create_window(path)


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


def run() -> int:
    application = create_application()
    path = database_path()
    try:
        lock = acquire_data_lock(path)
    except (OSError, RuntimeError) as error:
        show_start_error(path, error)
        return 1
    try:
        try:
            window = open_startup_window(path)
        except (OSError, sqlite3.Error, RuntimeError) as error:
            show_start_error(path, error)
            return 1
        if window is None:
            return 1
        window.show()
        return application.exec()
    finally:
        lock.unlock()


def show_start_error(path: Path, error: Exception) -> None:
    QMessageBox.critical(
        None,
        "Не удалось открыть AutoService",
        f"{error}\n\nПапка базы данных — {path.parent}\n\nНе удаляйте существующую базу.",
    )
