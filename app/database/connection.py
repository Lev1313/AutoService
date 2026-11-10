"""Минимальная схема без бизнес-таблиц и без удаления существующих данных."""

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1


def initialize_database(path: Path) -> None:
    """Создать служебную таблицу один раз, сохраняя существующую базу."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("База создана более новой версией AutoService. Обновите программу.")
        if version == 0:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if tables:
                raise RuntimeError(
                    "Обнаружена база неизвестного формата. Данные сохранены; "
                    "требуется проверка совместимости."
                )
            connection.execute(
                "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
                ("application", "AutoService"),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        else:
            identity = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?", ("application",)
            ).fetchone()
            if identity != ("AutoService",):
                raise RuntimeError("База не распознана как AutoService. Данные не изменены.")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
