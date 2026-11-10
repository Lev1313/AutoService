"""Пользовательские данные хранятся отдельно от программы."""

import os
from pathlib import Path


def data_directory() -> Path:
    """Вернуть путь без создания каталогов и других побочных эффектов."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "AutoService"


def database_path() -> Path:
    return data_directory() / "autoservice.sqlite3"
