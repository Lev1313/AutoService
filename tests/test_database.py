import sqlite3

import pytest

from app.database.connection import SCHEMA_VERSION, initialize_database


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


def test_invalid_identity_is_not_modified(tmp_path):
    path = tmp_path / "other.sqlite3"
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE app_metadata SET value = 'other'")
    original = path.read_bytes()
    with pytest.raises(RuntimeError):
        initialize_database(path)
    assert path.read_bytes() == original
