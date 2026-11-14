import sqlite3
from contextlib import closing

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QMessageBox
from test_txt_import import entries

from app.database.connection import (
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    SCHEMA_V5,
    SCHEMA_VERSION,
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from app.services.documents import Documents
from app.services.maintenance import Maintenance
from app.services.records import Records, mileage_text
from app.services.txt_import import TxtImport
from app.ui.documents import DocumentDialog
from app.ui.vehicles import RepairDialog, VehicleCard


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle({"plate": "ВЫМЫШЛЕННЫЙ"})
    first = records.save_repair(car, {"visit_date": "2026-01-01", "km": 1000})
    return records, car, first


def test_carried_is_snapshot_not_a_measurement(data):
    records, car, first = data
    values = {"visit_date": "2026-02-01", "carried_km": 1000, "carried_on": "2026-01-01"}
    second = records.save_repair(car, values)
    records.save_repair(car, {"visit_date": "2026-03-01", "km": 2000})
    initialize_database(records.path)
    repair = next(r for r in Records(records.path).repairs(car) if r["id"] == second)
    assert repair["km"] is None
    assert repair["carried_km"] == 1000
    assert mileage_text(repair).startswith("1 000 ?")
    assert "?" in mileage_text(repair)
    assert records.latest_mileage(car, "2026-02-01")["repair_id"] == first
    with connect(records.path) as connection:
        assert connection.execute("SELECT count(*) FROM odometer_readings").fetchone()[0] == 2
    document = Documents(records.path).create(second)
    assert document["payload"]["repair"]["carried_on"] == "2026-01-01"
    assert Documents(records.path).pdf_content(document["id"]).startswith(b"%PDF")
    with pytest.raises(ValueError):
        records.save_repair(car, values | {"visit_date": "2025-12-01"})
    with pytest.raises(ValueError):
        records.save_repair(car, values | {"km": 1100})


def test_ui_carries_only_before_date_and_can_confirm_same_value(application, data):
    records, car, first = data
    dialog = RepairDialog(records, car)
    dialog.visit_date.setDate(QDate(2026, 2, 1))
    assert dialog.mileage.text() == "1000 ?"
    dialog.visit_date.setDate(QDate(2025, 12, 31))
    assert dialog.mileage.text() == ""
    dialog.visit_date.setDate(QDate(2026, 2, 1))
    dialog.description.setPlainText("Без нового замера")
    assert dialog.save()
    repair = records.repairs(car)[0]
    assert repair["km"] is None and repair["carried_km"] == 1000
    reopen = RepairDialog(records, car, repair)
    assert reopen.mileage.text() == "1000 ?"
    reopen.measured.setChecked(True)
    assert reopen.mileage.text() == "1000"
    assert reopen.save()
    assert records.latest_mileage(car)["recorded_on"] == "2026-02-01"
    assert records.repairs(car)[0]["carried_km"] is None
    dialog.deleteLater()
    reopen.deleteLater()


def test_delete_restore_preserves_pdf_txt_and_independent_to(data, tmp_path):
    records, car, first = data
    source_path = tmp_path / "оригинал.txt"
    raw = "01.03.2026\r\nПробег: 2000\r\nТестовая работа\r\n".encode("cp1251")
    source_path.write_bytes(raw)
    importer = TxtImport(records.path)
    source = importer.preview(source_path)
    importer.commit(entries(source, car))
    repair = records.repairs(car)[0]
    repair_id = repair["id"]
    records.save_repair(car, repair | {"mileage_verified": True, "completed": True}, repair_id)
    docs = Documents(records.path)
    document = docs.create(repair_id)
    pdf = docs.pdf_content(document["id"])
    maintenance = Maintenance(records.path)
    plan_id = maintenance.save_plan(
        car,
        {
            "title": "Вымышленное ТО",
            "months": 6,
            "baseline_date": "2026-01-01",
            "lead_days": 0,
        },
    )
    maintenance.record_completion(plan_id, "2026-03-01", 2000, repair_id)
    records.set_repair_deleted(car, repair_id)
    assert len(records.repairs(car)) == 1
    assert records.repairs(car, True)[0]["deleted_at"]
    assert records.latest_mileage(car)["source"] == "maintenance"
    assert not records.latest_mileage(car)["repair_id"]
    assert docs.pdf_content(document["id"]) == pdf
    assert importer.provenance(repair_id)["original_bytes"] == raw
    assert source_path.read_bytes() == raw
    with pytest.raises(ValueError, match="восстановите"):
        docs.create(repair_id)
    repeated = importer.preview(source_path)
    assert repeated.fragments[0].imported
    copy = backup_database(records.path, tmp_path / "backup.sqlite3")
    records.set_repair_deleted(car, repair_id, False)
    restore_database(records.path, copy)
    assert len(Records(records.path).repairs(car)) == 1
    assert maintenance.plans(car)[0]["cycle_no"] == 2
    records.set_repair_deleted(car, repair_id, False)
    assert len(records.repairs(car)) == 2
    assert docs.pdf_content(document["id"]) == pdf


def test_delete_ui_cancel_and_archived_documents(application, data, monkeypatch):
    records, car, first = data
    document = Documents(records.path).create(first)
    card = VehicleCard(records, car)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    card.toggle_deleted()
    assert records.repairs(car)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    card.toggle_deleted()
    assert not records.repairs(car)
    card.show_deleted.setChecked(True)
    assert card.history.rowCount() == 1
    assert not card.open_button.isEnabled()
    archive = DocumentDialog(records, first)
    assert archive.current_document["id"] == document["id"]
    assert archive.content == Documents(records.path).pdf_content(document["id"])
    assert not archive.create_button.isEnabled()
    card.toggle_deleted()
    assert len(records.repairs(car)) == 1
    archive.close()
    card.close()


def test_v5_migration_backup_and_transaction_rollback(tmp_path, monkeypatch):
    path = tmp_path / "v5.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application','AutoService')")
        for statement in (*SCHEMA_V2, *SCHEMA_V3, *SCHEMA_V4, *SCHEMA_V5):
            connection.execute(statement)
        connection.execute("PRAGMA user_version=5")
        connection.commit()
    raw = path.read_bytes()
    import app.database.connection as database

    validate = database.validate_tables

    def fail(_):
        raise RuntimeError("Искусственный сбой проверки")

    monkeypatch.setattr(database, "validate_tables", fail)
    with pytest.raises(RuntimeError):
        initialize_database(path)
    assert path.read_bytes() == raw
    monkeypatch.setattr(database, "validate_tables", validate)
    initialize_database(path)
    with connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    copies = list((tmp_path / "backups").glob("*.sqlite3"))
    assert copies
    with closing(sqlite3.connect(copies[0])) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert "carried_km" not in [r[1] for r in connection.execute("PRAGMA table_info(repairs)")]
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restoring_conflicting_mileage_requires_explicit_choice(data):
    from app.services.records import RestoreMileageConflict

    records, car, first = data
    records.set_repair_deleted(car, first)
    records.save_repair(car, {"visit_date": "2026-02-01", "km": 900})
    with pytest.raises(RestoreMileageConflict):
        records.set_repair_deleted(car, first, False)
    assert len(records.repairs(car)) == 1
    records.set_repair_deleted(car, first, False, allow_unverified=True)
    restored = next(r for r in records.repairs(car) if r["id"] == first)
    assert restored["km"] == 1000 and restored["verified"] == 0
    assert records.latest_mileage(car)["km"] == 900
