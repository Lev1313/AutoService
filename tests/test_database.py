import sqlite3
from hashlib import sha256

import pytest

from app.database.connection import SCHEMA_VERSION, initialize_database, restore_database


def set_old_schema(connection, version):
    connection.execute("ALTER TABLE repairs DROP COLUMN document_items")
    connection.execute("ALTER TABLE repairs DROP COLUMN recommendations")
    connection.execute(f"PRAGMA user_version = {version}")


def test_initialize_and_reopen_preserves_data(tmp_path):
    path = tmp_path / "nested" / "test.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        connection.execute("INSERT INTO app_metadata VALUES ('test', 'сохранено')")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM app_metadata WHERE key = 'test'"
        ).fetchone() == ("сохранено",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_v7_lowercases_saved_plates_after_creating_backup(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        set_old_schema(connection, 7)
        connection.execute("INSERT INTO vehicles(plate,vin,archived) VALUES ('А123Bc98','VIN',1)")

    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id, plate, vin, archived FROM vehicles").fetchone() == (
            1,
            "а123bc98",
            "VIN",
            1,
        )
    (backup,) = (tmp_path / "backups").glob("*.sqlite3")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT plate FROM vehicles").fetchone() == ("А123Bc98",)


def test_v8_uppercases_vin_after_creating_backup(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        set_old_schema(connection, 8)
        connection.execute("INSERT INTO vehicles(vin,archived) VALUES ('wvwzzz1kzaw000002',1)")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT vin,archived FROM vehicles").fetchone() == (
            "WVWZZZ1KZAW000002",
            1,
        )
    (backup,) = (tmp_path / "backups").glob("*.sqlite3")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT vin FROM vehicles").fetchone() == ("wvwzzz1kzaw000002",)


def test_v9_migration_preserves_draft_and_existing_v10_fields(tmp_path):
    path = tmp_path / "test.sqlite3"
    initialize_database(path)
    payload = '{"recommendations":"Проверить тормоза","items":[{"title":"Осмотр"}]}'
    with sqlite3.connect(path) as connection:
        set_old_schema(connection, 9)
        connection.execute("INSERT INTO vehicles(id, plate) VALUES (1, 'тест')")
        connection.execute(
            "INSERT INTO repairs(id, vehicle_id, visit_date) VALUES (1, 1, '2026-11-22')"
        )
        connection.execute("INSERT INTO document_drafts VALUES (1, ?)", (payload,))

    initialize_database(path)
    (backup,) = (tmp_path / "backups").glob("*.sqlite3")
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (9,)
        assert connection.execute("SELECT payload FROM document_drafts").fetchone() == (payload,)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT recommendations, document_items FROM repairs"
        ).fetchone() == (
            "Проверить тормоза",
            '[{"title":"Осмотр"}]',
        )
        connection.execute("UPDATE repairs SET recommendations='Записано в схеме 10'")
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (10,)
        assert connection.execute("SELECT recommendations FROM repairs").fetchone() == (
            "Записано в схеме 10",
        )


@pytest.mark.parametrize("version", [0, SCHEMA_VERSION + 1])
def test_unknown_database_is_not_modified(tmp_path, version):
    path = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE existing (value TEXT)")
        connection.execute("INSERT INTO existing VALUES ('важные данные')")
        connection.execute(f"PRAGMA user_version = {version}")
    original = path.read_bytes()
    with pytest.raises(RuntimeError):
        initialize_database(path)
    assert path.read_bytes() == original


def test_notes_from_0520_draft_recovered_once(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM app_metadata WHERE key='repair_notes_synced'")
        db.execute("INSERT INTO vehicles(id,plate) VALUES (1,'учебный')")
        db.execute("INSERT INTO repairs(id,vehicle_id) VALUES (1,1),(2,1)")
        db.execute(
            "INSERT INTO document_drafts VALUES (1,?)", ('{"recommendations":"Из черновика"}',)
        )
        db.execute(
            "INSERT INTO documents VALUES (1,'СЛ-1',2,'2026-11-22','simple',?)",
            ('{"layout":"service_sheet","recommendations":"Из листа"}',),
        )
    initialize_database(path)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT recommendations FROM repairs ORDER BY id").fetchall() == [
            ("Из черновика",),
            ("Из листа",),
        ]
        db.execute("UPDATE repairs SET recommendations=''")
    initialize_database(path)
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT 1 FROM repairs WHERE recommendations<>''").fetchone()


def test_invalid_identity_is_not_modified(tmp_path):
    path = tmp_path / "other.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE app_metadata SET value = 'other'")
    original = path.read_bytes()
    with pytest.raises(RuntimeError):
        initialize_database(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    ("statement", "message"),
    [
        ("DELETE FROM document_files", "отсутствует PDF документа ТЕСТ-1"),
        ("UPDATE document_files SET content = X'01'", "повреждён PDF документа ТЕСТ-1"),
        ("UPDATE import_sources SET original_bytes = X'01'", "повреждён исходный TXT №1"),
    ],
)
def test_restore_rejects_missing_or_changed_attachments(tmp_path, statement, message):
    working = tmp_path / "working.sqlite3"
    source = tmp_path / "copy.sqlite3"
    initialize_database(working)
    initialize_database(source)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO vehicles(id, plate) VALUES (1, 'тест')")
        connection.execute("INSERT INTO repairs(id, vehicle_id) VALUES (1,1)")
        connection.execute("INSERT INTO documents VALUES (1,'ТЕСТ-1',1,'2026-11-22','simple','{}')")
        pdf = b"%PDF-test"
        txt = b"test TXT"
        connection.execute(
            "INSERT INTO document_files VALUES (1,?,?)", (pdf, sha256(pdf).hexdigest())
        )
        connection.execute(
            "INSERT INTO import_sources VALUES (1,?,?,'test','utf-8','test.txt','2026-11-22')",
            (sha256(txt).hexdigest(), txt),
        )
        connection.execute(statement)
    original = working.read_bytes()
    original_copy = source.read_bytes()
    with pytest.raises(ValueError, match=message):
        restore_database(working, source)
    assert working.read_bytes() == original
    assert source.read_bytes() == original_copy
    assert not (tmp_path / "backups").exists()
