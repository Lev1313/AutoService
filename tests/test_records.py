import sqlite3
from contextlib import closing

import pytest

from app.database.connection import (
    SCHEMA_VERSION,
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from app.services.records import Records, parse_amount


@pytest.fixture
def records(tmp_path):
    path = tmp_path / "records.sqlite3"
    initialize_database(path)
    return Records(path)


def test_search_edit_and_reopen_preserve_car_history(records):
    car = records.save_vehicle(
        {
            "make": "ШКОДА",
            "model": "Октавия",
            "plate": "А 123 ВВ 10",
            "vin": "TESTVIN",
            "contact_name": "Тестовый контакт",
            "phone": "+7 (000) 123-45-67",
        }
    )
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-11-10",
            "km": 120000,
            "description": "Масло\nФильтр",
            "amount_kopecks": 150050,
            "completed": True,
        },
    )
    assert records.vehicle(car)["plate"] == "а 123 вв 10"
    for query in ("шкода", "ОКТАВИЯ", "а123вв10", "А123ВВ10", "testvin", "тестовый", "1234567"):
        assert [row["id"] for row in records.vehicles(query)] == [car]
    changed = records.vehicle(car) | {"plate": "В456ВВ10", "contact_name": "Другой контакт"}
    records.save_vehicle(changed, car)
    initialize_database(records.path)
    reopened = Records(records.path)
    assert reopened.vehicle(car)["plate"] == "в456вв10"
    assert reopened.vehicle(car)["vin"] == "TESTVIN"
    assert reopened.repairs(car)[0]["id"] == repair
    assert reopened.repairs(car)[0]["description"] == "Масло\nФильтр"
    assert reopened.latest_mileage(car)["km"] == 120000
    with connect(records.path) as connection:
        assert connection.execute("SELECT count(*) FROM vehicle_contacts").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM contacts").fetchone()[0] == 2


def test_shared_contact_does_not_merge_vehicles_or_change_other_car(records):
    first = records.save_vehicle({"plate": "ТЕСТ 1", "contact_name": "Один", "phone": "000"})
    contact = records.vehicle(first)["contact_id"]
    second = records.save_vehicle(
        {
            "plate": "ТЕСТ 2",
            "contact_id": contact,
            "contact_name": "Один",
            "phone": "000",
        }
    )
    records.save_vehicle(records.vehicle(second) | {"contact_name": "Другой"}, second)
    assert records.vehicle(first)["contact_name"] == "Один"
    assert records.vehicle(second)["contact_name"] == "Другой"
    assert records.vehicle(second)["contact_id"] != contact


def test_duplicates_warn_without_merging(records):
    first = records.save_vehicle({"plate": "А 123 ВВ 10"})
    assert records.duplicates("a123bb10", "")[0]["id"] == first
    assert records.duplicates("А123ВВ10", "", first) == []
    records.save_vehicle({"plate": "А123ВВ10"})
    assert len(records.vehicles()) == 2


@pytest.mark.parametrize(("text", "expected"), [("", None), ("0", 0), ("1 500,50", 150050)])
def test_money_unknown_free_and_exact_kopecks(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-1", "0.001", "текст", "1000000000"])
def test_invalid_amount_is_rejected(text):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_draft_completion_edit_and_mileage_consistency(records):
    car = records.save_vehicle({"plate": "ТЕСТ"})
    values = {"visit_date": "2026-10-01", "km": 100000, "description": "Черновик"}
    repair = records.save_repair(car, values)
    assert records.repairs(car)[0]["completed"] == 0
    assert records.repairs(car)[0]["amount_kopecks"] is None
    records.save_repair(car, values | {"completed": True, "amount_kopecks": 0}, repair)
    assert records.repairs(car)[0]["completed"] == 1
    assert records.repairs(car)[0]["amount_kopecks"] == 0
    with pytest.raises(ValueError, match="противоречит"):
        records.save_repair(car, values | {"visit_date": "2026-11-01", "km": 90000})
    records.save_repair(
        car,
        values | {"visit_date": "2026-11-01", "km": 90000},
        allow_unverified=True,
    )
    assert records.latest_mileage(car)["km"] == 100000
    assert records.repairs(car)[0]["verified"] == 0
    records.save_repair(car, values | {"km": None}, repair)
    assert records.latest_mileage(car) is None


def test_invalid_repair_is_atomic_and_cannot_move_between_cars(records):
    first = records.save_vehicle({"plate": "ТЕСТ1"})
    second = records.save_vehicle({"plate": "ТЕСТ2"})
    values = {"visit_date": "2026-11-10", "km": 20}
    repair = records.save_repair(first, values)
    with pytest.raises(ValueError):
        records.save_repair(second, values | {"description": "Подмена"}, repair)
    assert records.repairs(first)[0]["description"] == ""
    assert records.repairs(second) == []
    for invalid in ({"km": -1}, {"amount_kopecks": 1.5}, {"visit_date": "не дата"}):
        with pytest.raises(ValueError):
            records.save_repair(first, values | invalid)
    assert len(records.repairs(first)) == 1


def test_migration_preserves_v1_and_keeps_pre_migration_backup(tmp_path):
    path = tmp_path / "v1.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application', 'AutoService')")
        connection.execute("INSERT INTO app_metadata VALUES ('test', 'сохранено')")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    initialize_database(path)
    with connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            connection.execute("SELECT value FROM app_metadata WHERE key='test'").fetchone()[0]
            == "сохранено"
        )
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    initialize_database(path)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_backup_restore_safety_copy_and_invalid_source(records, tmp_path):
    car = records.save_vehicle({"plate": "ДО"})
    backup = backup_database(records.path, tmp_path / "external" / "copy.sqlite3")
    records.save_vehicle(records.vehicle(car) | {"plate": "ПОСЛЕ"}, car)
    safety = restore_database(records.path, backup)
    assert records.vehicle(car)["plate"] == "до"
    assert Records(safety).vehicle(car)["plate"] == "после"
    original = records.path.read_bytes()
    wrong = tmp_path / "wrong.sqlite3"
    wrong.write_text("not a database", encoding="utf-8")
    with pytest.raises(ValueError):
        restore_database(records.path, wrong)
    assert records.path.read_bytes() == original
    with pytest.raises(FileExistsError):
        backup_database(records.path, backup)
    with pytest.raises(ValueError):
        backup_database(records.path, records.path)


def test_failed_migration_rolls_back_and_backup_failure_aborts(tmp_path, monkeypatch):
    import app.database.connection as module

    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application', 'AutoService')")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    original = path.read_bytes()
    monkeypatch.setattr(module, "SCHEMA_V2", (*module.SCHEMA_V2, "INVALID SQL"))
    with pytest.raises(sqlite3.Error):
        initialize_database(path)
    assert path.read_bytes() == original
    monkeypatch.setattr(
        module, "backup_database", lambda *_: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        initialize_database(path)
    assert path.read_bytes() == original
