"""Версионированная SQLite с резервной копией перед обновлением."""

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 2
SCHEMA_V2 = (
    """CREATE TABLE contacts (id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE vehicles (id INTEGER PRIMARY KEY, make TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', plate TEXT NOT NULL DEFAULT '', vin TEXT NOT NULL DEFAULT '',
    year INTEGER, notes TEXT NOT NULL DEFAULT '', contact_id INTEGER REFERENCES contacts(id),
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1)))""",
    """CREATE TABLE vehicle_contacts (id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id), contact_id INTEGER REFERENCES contacts(id),
    changed_at TEXT NOT NULL)""",
    """CREATE TABLE repairs (id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id), visit_date TEXT,
    description TEXT NOT NULL DEFAULT '',
    amount_kopecks INTEGER CHECK(amount_kopecks IS NULL OR amount_kopecks >= 0),
    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0, 1)))""",
    """CREATE TABLE odometer_readings (id INTEGER PRIMARY KEY,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    repair_id INTEGER UNIQUE REFERENCES repairs(id),
    recorded_on TEXT NOT NULL, km INTEGER NOT NULL CHECK(km >= 0),
    source TEXT NOT NULL DEFAULT 'repair',
    verified INTEGER NOT NULL DEFAULT 1 CHECK(verified IN (0, 1)))""",
    "CREATE INDEX repairs_vehicle_date ON repairs(vehicle_id, visit_date DESC, id DESC)",
    "CREATE INDEX odometer_vehicle_date ON odometer_readings(vehicle_id, recorded_on DESC)",
)


@contextmanager
def connect(path: Path):
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def backup_database(path: Path, destination: Path | None = None) -> Path:
    """Снимок SQLite; существующие файлы не перезаписываются."""
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError("База для резервного копирования не найдена.")
    if destination is None:
        directory = path.parent / "backups"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = directory / f"autoservice-{stamp}-{uuid4().hex[:8]}.sqlite3"
    if path == destination.resolve():
        raise ValueError("Резервная копия должна находиться в отдельном файле.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with (
            closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(destination)) as target,
        ):
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("Проверка резервной копии не пройдена.")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def initialize_database(path: Path) -> None:
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("База создана более новой версией AutoService. Обновите программу.")
        if version == 0:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if tables:
                raise RuntimeError("Обнаружена база неизвестного формата. Данные не изменены.")
            connection.execute(
                "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO app_metadata VALUES ('application', 'AutoService')")
        else:
            identity = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'application'"
            ).fetchone()
            if identity is None or identity[0] != "AutoService":
                raise RuntimeError("База не распознана как AutoService. Данные не изменены.")
        if version == 1:
            # BEGIN IMMEDIATE защищает исходный снимок от других писателей.
            backup_database(path)
        if version < 2:
            for statement in SCHEMA_V2:
                connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        validate_tables(connection)


def validate_tables(connection):
    for statement in (
        "SELECT id, make, model, plate, vin, year, notes, contact_id, archived FROM vehicles",
        "SELECT id, name, phone FROM contacts",
        "SELECT id, vehicle_id, contact_id, changed_at FROM vehicle_contacts",
        "SELECT id, vehicle_id, visit_date, description, amount_kopecks, completed FROM repairs",
        "SELECT id, vehicle_id, repair_id, recorded_on, km, source, verified "
        "FROM odometer_readings",
    ):
        connection.execute(statement)


def restore_database(path: Path, source_path: Path) -> Path:
    """Проверить копию, сохранить текущую базу, атомарно восстановить снимок."""
    path, source_path = path.resolve(), source_path.resolve()
    if path == source_path:
        raise ValueError("Выберите отдельный файл резервной копии.")
    try:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("Повреждённая резервная копия.")
            identity = source.execute(
                "SELECT value FROM app_metadata WHERE key='application'"
            ).fetchone()
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if identity != ("AutoService",) or version != SCHEMA_VERSION:
                raise ValueError("Нужна резервная копия AutoService текущей версии.")
            # Проверяем поля, нужные приложению, до замены текущих данных.
            validate_tables(source)
            if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("В копии обнаружены повреждённые связи записей.")
            safety_copy = backup_database(path)
            with closing(sqlite3.connect(path, timeout=5)) as target:
                source.backup(target)
    except sqlite3.Error as error:
        raise ValueError(f"Не удалось прочитать или восстановить копию: {error}") from error
    return safety_copy
