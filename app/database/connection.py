"""Версионированная SQLite с резервной копией перед обновлением."""

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

SCHEMA_VERSION = 5
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

SCHEMA_V3 = (
    """CREATE TABLE maintenance_plans (
    id INTEGER PRIMARY KEY, vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    title TEXT NOT NULL, months INTEGER CHECK(months > 0),
    distance_km INTEGER CHECK(distance_km > 0),
    lead_days INTEGER NOT NULL DEFAULT 0 CHECK(lead_days >= 0),
    annual_km INTEGER CHECK(annual_km > 0), baseline_date TEXT, baseline_km INTEGER,
    cycle_no INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
    CHECK(months IS NOT NULL OR distance_km IS NOT NULL))""",
    """CREATE TABLE maintenance_events (
    id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
    performed_on TEXT NOT NULL, km INTEGER CHECK(km >= 0),
    repair_id INTEGER REFERENCES repairs(id), note TEXT NOT NULL DEFAULT '')""",
    """CREATE UNIQUE INDEX maintenance_event_repair ON maintenance_events(plan_id, repair_id)
    WHERE repair_id IS NOT NULL""",
    "ALTER TABLE odometer_readings ADD COLUMN maintenance_event_id INTEGER "
    "REFERENCES maintenance_events(id)",
    "CREATE UNIQUE INDEX odometer_event ON odometer_readings(maintenance_event_id) "
    "WHERE maintenance_event_id IS NOT NULL",
    """CREATE TABLE call_tasks (
    id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES maintenance_plans(id),
    cycle_no INTEGER NOT NULL, calculated_date TEXT, manual_date TEXT,
    details TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','contacted','completed','declined')),
    UNIQUE(plan_id, cycle_no))""",
    """CREATE TABLE call_attempts (
    id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES call_tasks(id),
    attempted_at TEXT NOT NULL, outcome TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', next_date TEXT)""",
    """CREATE TABLE vehicle_call_preferences (
    vehicle_id INTEGER PRIMARY KEY REFERENCES vehicles(id),
    allow_calls INTEGER NOT NULL DEFAULT 1 CHECK(allow_calls IN (0,1)))""",
)

SCHEMA_V4 = (
    """CREATE TABLE document_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE document_drafts (
    repair_id INTEGER PRIMARY KEY REFERENCES repairs(id), payload TEXT NOT NULL)""",
    """CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE,
    repair_id INTEGER NOT NULL REFERENCES repairs(id), created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('simple','detailed')), payload TEXT NOT NULL)""",
    "CREATE INDEX documents_repair ON documents(repair_id, id DESC)",
    """CREATE TABLE document_files (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    content BLOB NOT NULL, sha256 TEXT NOT NULL)""",
)

SCHEMA_V5 = (
    "ALTER TABLE repairs ADD COLUMN origin TEXT NOT NULL DEFAULT 'manual'",
    "ALTER TABLE repairs ADD COLUMN state_known INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE repairs ADD COLUMN undated_km INTEGER CHECK(undated_km >= 0)",
    """CREATE TABLE import_sources (
    id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, original_bytes BLOB NOT NULL,
    decoded_text TEXT NOT NULL, encoding TEXT NOT NULL, original_name TEXT NOT NULL,
    imported_at TEXT NOT NULL)""",
    """CREATE TABLE import_paths (
    id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES import_sources(id),
    source_path TEXT NOT NULL, UNIQUE(source_id, source_path))""",
    """CREATE TABLE import_fragments (
    id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES import_sources(id),
    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, original_text TEXT NOT NULL,
    reviewed_json TEXT NOT NULL, repair_id INTEGER NOT NULL UNIQUE REFERENCES repairs(id),
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id), semantic_hash TEXT NOT NULL,
    UNIQUE(source_id, start_offset, end_offset))""",
    "CREATE INDEX import_semantic ON import_fragments(vehicle_id, semantic_hash)",
    """CREATE TABLE import_batches (
    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, report_json TEXT NOT NULL)""",
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
        if 0 < version < SCHEMA_VERSION:
            # BEGIN IMMEDIATE защищает исходный снимок от других писателей.
            backup_database(path)
        if version < 2:
            for statement in SCHEMA_V2:
                connection.execute(statement)
        if version < 3:
            for statement in SCHEMA_V3:
                connection.execute(statement)
        if version < 4:
            for statement in SCHEMA_V4:
                connection.execute(statement)
        if version < 5:
            for statement in SCHEMA_V5:
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
        "SELECT id, vehicle_id, title, months, distance_km, lead_days, annual_km, "
        "baseline_date, baseline_km, cycle_no, enabled FROM maintenance_plans",
        "SELECT id, plan_id, performed_on, km, repair_id, note FROM maintenance_events",
        "SELECT id, plan_id, cycle_no, calculated_date, manual_date, details, status "
        "FROM call_tasks",
        "SELECT id, task_id, attempted_at, outcome, note, next_date FROM call_attempts",
        "SELECT vehicle_id, allow_calls FROM vehicle_call_preferences",
        "SELECT key, value FROM document_settings",
        "SELECT repair_id, payload FROM document_drafts",
        "SELECT id, number, repair_id, created_at, kind, payload FROM documents",
        "SELECT document_id, content, sha256 FROM document_files",
        "SELECT origin, state_known, undated_km FROM repairs",
        "SELECT id, sha256, original_bytes, decoded_text, encoding, original_name, imported_at "
        "FROM import_sources",
        "SELECT id, source_id, source_path FROM import_paths",
        "SELECT id, source_id, start_offset, end_offset, original_text, reviewed_json, "
        "repair_id, vehicle_id, semantic_hash FROM import_fragments",
        "SELECT id, created_at, report_json FROM import_batches",
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
            if identity != ("AutoService",) or not 1 <= version <= SCHEMA_VERSION:
                raise ValueError("Нужна совместимая резервная копия AutoService.")
            if version < SCHEMA_VERSION:
                # Проверяем и обновляем старую копию в отдельном временном каталоге.
                # Исходная копия и текущая база до успешной миграции не меняются.
                with TemporaryDirectory(prefix="restore-preview-", dir=path.parent) as directory:
                    prepared = Path(directory) / "prepared.sqlite3"
                    with closing(sqlite3.connect(prepared)) as staged:
                        source.backup(staged)
                    initialize_database(prepared)
                    return restore_database(path, prepared)
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
