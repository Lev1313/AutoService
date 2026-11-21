import sqlite3
from contextlib import closing
from copy import deepcopy
from hashlib import sha256

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from app.database.connection import (
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_V4,
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from app.services.documents import Documents
from app.services.maintenance import Maintenance
from app.services.records import Records
from app.services.txt_import import TxtImport
from app.services.txt_parser import decode_text, read_source, split_fragment
from app.ui.txt_import import ImportDialog, SourceDialog
from app.ui.vehicles import RepairDialog

TEXT = (
    "Марка: Тестовая\r\nМодель: Демо\r\nГосномер: А123ВВ10\r\n"
    "Контакт: Вымышленный\r\nТелефон: 000-000\r\n"
    "10.01.2026\r\nПробег: 100 000 км\r\nИтого: 1500,50 руб.\r\n"
    "Рекомендовано заменить масло. Оплачено неизвестно.\r\n"
    "20.03.2026\r\nПробег: 105000\r\nТестовая запись без стоимости.\r\n"
)


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "import.sqlite3"
    initialize_database(path)
    source = tmp_path / "example.txt"
    source.write_bytes(TEXT.encode("utf-8"))
    return Records(path), TxtImport(path), source


def entries(source, target=None):
    return [
        {
            "source": source,
            "fragment": fragment,
            "values": deepcopy(fragment.values),
            "confirmed": True,
            "target": target or source.bound_vehicle_id or f"file:{source.digest}",
            "allow_duplicate": False,
        }
        for fragment in source.fragments
    ]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1251", "utf-16"])
def test_supported_encodings_preserve_bytes_and_full_ranges(tmp_path, encoding):
    path = tmp_path / "source.txt"
    raw = TEXT.encode(encoding)
    path.write_bytes(raw)
    source = read_source(path)
    assert source.raw == raw
    assert source.text == TEXT
    assert len(source.fragments) == 2
    assert "".join(fragment.original for fragment in source.fragments) == TEXT
    assert source.fragments[0].values["amount_kopecks"] == 150050
    assert source.fragments[1].values["amount_kopecks"] is None
    assert source.fragments[0].values["completed"] is None
    assert not source.fragments[0].values["mileage_verified"]
    assert path.read_bytes() == raw


def test_other_encoding_can_be_chosen_without_replacement():
    text = "Пробег и вымышленные данные"
    assert decode_text(text.encode("cp866"), "cp866")[0] == text
    with pytest.raises(UnicodeDecodeError):
        decode_text(b"\xff", "utf-8")


def test_existing_source_cannot_mix_new_decoding(data):
    _, service, path = data
    source = service.preview(path)
    service.commit(entries(source)[:1])
    with pytest.raises(ValueError, match="кодировке"):
        service.preview(path, encoding="cp866")
    changed = read_source(path, encoding="cp866")
    with pytest.raises(ValueError, match="кодировки"):
        service.commit(entries(changed))


def test_v4_upgrade_keeps_frozen_pdf_and_existing_repairs(tmp_path):
    path = tmp_path / "v4.sqlite3"
    frozen = b"%PDF-immutable-test-snapshot"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application','AutoService')")
        for statement in (*SCHEMA_V2, *SCHEMA_V3, *SCHEMA_V4):
            connection.execute(statement)
        connection.execute("INSERT INTO vehicles(id,plate) VALUES (1,'ТЕСТ')")
        connection.execute(
            "INSERT INTO repairs(id,vehicle_id,visit_date) VALUES (1,1,'2026-11-01')"
        )
        connection.execute(
            "INSERT INTO documents VALUES (1,'КВ-000001',1,'2026-11-01','simple','{}')"
        )
        connection.execute(
            "INSERT INTO document_files VALUES (1,?,?)", (frozen, sha256(frozen).hexdigest())
        )
        connection.execute("PRAGMA user_version=4")
        connection.commit()
    initialize_database(path)
    assert Documents(path).pdf_content(1) == frozen
    assert Records(path).repairs(1)[0]["state_known"] == 1
    assert Records(path).repairs(1)[0]["origin"] == "manual"


def test_imported_mileage_is_not_silently_trusted_by_text_edit(data):
    records, service, path = data
    source = service.preview(path)
    result = service.commit(entries(source))
    car = result["imported"][0]["vehicle_id"]
    repair = records.repairs(car)[0]
    records.save_repair(car, repair | {"description": "Исправлен только текст"}, repair["id"])
    assert records.latest_mileage(car) is None
    records.save_repair(car, repair | {"mileage_verified": True}, repair["id"])
    assert records.latest_mileage(car)["km"] == repair["km"]


def test_unknown_dates_amounts_and_payments_are_not_invented(tmp_path):
    path = tmp_path / "unknown.txt"
    path.write_text(
        "Рекомендовано ТО.\nОплачено: 5000\nИтого: 1000\nИтого: 2000\n", encoding="utf-8"
    )
    fragment = read_source(path).fragments[0]
    assert fragment.values["visit_date"] is None
    assert fragment.values["amount_kopecks"] is None
    assert fragment.values["km"] is None
    assert "Оплачено: 5000" in fragment.original
    assert any("несколько разных сумм" in value.casefold() for value in fragment.warnings)
    path.write_text("31.02.2026\nПробег: около ста тысяч\n", encoding="utf-8")
    fragment = read_source(path).fragments[0]
    assert fragment.values["visit_date"] is None and fragment.values["km"] is None


def test_preview_is_read_only_and_commit_preserves_sources_and_unknowns(data):
    records, service, path = data
    original_db = records.path.read_bytes()
    original_txt = path.read_bytes()
    source = service.preview(path)
    assert records.path.read_bytes() == original_db
    result = service.commit(entries(source))
    assert len(result["imported"]) == 2 and result["backup"]
    assert len(records.vehicles()) == 1
    assert records.vehicles()[0]["plate"] == "а123вв10"
    car = records.vehicles()[0]["id"]
    repairs = records.repairs(car)
    assert len(repairs) == 2
    assert all(row["origin"] == "txt" and row["state_known"] == 0 for row in repairs)
    assert repairs[0]["amount_kopecks"] is None
    assert records.latest_mileage(car) is None
    assert Maintenance(records.path).tasks() == []
    source_data = service.provenance(repairs[0]["id"])
    assert source_data["original_bytes"] == original_txt
    assert source_data["decoded_text"] == TEXT
    assert path.read_bytes() == original_txt
    assert len(TxtImport(records.path).history()) == 1


def test_repeat_copy_partial_import_and_manual_split_cannot_duplicate_source(data, tmp_path):
    records, service, path = data
    source = service.preview(path)
    batch = entries(source)
    first = service.commit(batch[:1])
    second = service.commit(entries(service.preview(path)))
    assert len(first["imported"]) == 1
    assert len(second["imported"]) == 1 and len(second["skipped"]) == 1
    assert len(records.vehicles()) == 1


def test_partial_resume_with_existing_target_and_copied_file_deduplicates(data, tmp_path):
    records, service, path = data
    source = service.preview(path)
    first = service.commit(entries(source)[:1])
    car = first["imported"][0]["vehicle_id"]
    second = service.commit(entries(service.preview(path), car))
    assert len(second["imported"]) == 1 and len(second["skipped"]) == 1
    copy = tmp_path / "renamed.txt"
    copy.write_bytes(path.read_bytes())
    third = service.commit(entries(service.preview(copy), car))
    assert third["imported"] == [] and len(third["skipped"]) == 2
    assert len(records.repairs(car)) == 2


def test_changed_source_and_unconfirmed_data_never_write(data):
    records, service, path = data
    source = service.preview(path)
    batch = entries(source)
    batch[0]["confirmed"] = False
    with pytest.raises(ValueError, match="подтвердить"):
        service.commit(batch)
    path.write_text(TEXT + "Новое содержимое", encoding="utf-8")
    with pytest.raises(ValueError, match="изменён"):
        service.commit(entries(source))
    assert records.vehicles() == []


def test_error_after_first_insert_rolls_back_whole_batch(data):
    records, service, path = data
    car = records.save_vehicle({"make": "Существующая"})
    batch = entries(service.preview(path), car)
    batch[1]["target"] = 999999
    with pytest.raises(ValueError, match="больше не существует"):
        service.commit(batch)
    assert records.repairs(car) == []
    assert service.history() == []
    with connect(records.path) as connection:
        assert connection.execute("SELECT count(*) FROM import_sources").fetchone()[0] == 0


def test_backup_restores_original_bytes_even_when_source_file_is_gone(data):
    records, service, path = data
    original = path.read_bytes()
    result = service.commit(entries(service.preview(path)))
    repair = result["imported"][0]["repair_id"]
    backup = backup_database(records.path)
    with connect(records.path) as connection:
        connection.execute("UPDATE import_sources SET original_bytes=?", (b"damaged",))
    with pytest.raises(ValueError, match="целостность"):
        service.provenance(repair)
    restore_database(records.path, backup)
    path.unlink()
    assert TxtImport(records.path).provenance(repair)["original_bytes"] == original


def test_batch_failure_is_atomic_and_semantic_duplicate_requires_confirmation(data, tmp_path):
    records, service, path = data
    car = records.save_vehicle({"plate": "А123ВВ10"})
    source = service.preview(path)
    batch = entries(source)
    # Совпадение машины не объединяется автоматически.
    with pytest.raises(ValueError, match="номер или VIN"):
        service.commit(batch)
    assert records.repairs(car) == []
    batch = entries(source, car)
    service.commit(batch)
    copy = tmp_path / "edited-source.txt"
    copy.write_bytes(path.read_bytes() + b"\r\n")
    newer = service.preview(copy)
    newer_entries = entries(newer, car)
    # У первого фрагмента тот же смысл, хотя хеш файла иной.
    with pytest.raises(ValueError, match="похожий ремонт"):
        service.commit(newer_entries)
    assert len(records.repairs(car)) == 2
    for entry in newer_entries:
        entry["allow_duplicate"] = True
    result = service.commit(newer_entries)
    assert len(result["imported"]) == 2 and len(records.repairs(car)) == 4


def test_manual_split_preserves_original_and_overlapping_reimport_is_skipped(data):
    records, service, path = data
    source = service.preview(path)
    service.commit(entries(source))
    reloaded = service.preview(path)
    split_fragment(reloaded, 0, 3)
    assert "".join(fragment.original for fragment in reloaded.fragments) == TEXT
    car = records.vehicles()[0]["id"]
    report = service.commit(entries(reloaded, car))
    assert report["imported"] == []
    assert len(records.repairs(car)) == 2


def test_unknown_date_and_mileage_remain_unknown_after_edit_and_open(
    application, data, monkeypatch
):
    records, service, path = data
    path.write_text("Марка: Тест\nПробег: 123000\nОписание без даты\n", encoding="utf-8")
    source = service.preview(path)
    result = service.commit(entries(source))
    car, repair = result["imported"][0]["vehicle_id"], result["imported"][0]["repair_id"]
    value = records.repairs(car)[0]
    assert value["visit_date"] is None and value["km"] == 123000
    assert records.latest_mileage(car) is None
    monkeypatch.setattr(QMessageBox, "warning", lambda *_: None)
    editor = RepairDialog(records, car, value)
    editor.show()
    assert editor.state()["visit_date"] is None
    editor.description.setPlainText("Исправленное описание")
    QTest.mouseClick(editor.save_button, Qt.MouseButton.LeftButton)
    value = records.repairs(car)[0]
    assert value["visit_date"] is None and value["km"] == 123000
    assert service.provenance(repair)["decoded_text"].splitlines()[-1] == "Описание без даты"
    assert value["state_known"] == 0
    document = Documents(records.path).create(repair)
    assert document["payload"]["repair"]["visit_date"] is None
    assert document["payload"]["repair"]["km"] == 123000
    viewer = SourceDialog(records.path, repair)
    viewer.show()
    viewer.close()


def test_import_dialog_requires_review_then_imports_without_touching_original(
    application, data, monkeypatch
):
    records, _, path = data
    original = path.read_bytes()
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = ImportDialog(records)
    dialog.show()
    dialog.add_paths([path])
    assert len(dialog.entries) == 2
    assert not dialog.import_button.isEnabled()
    assert records.vehicles() == []
    for entry in dialog.entries:
        dialog.tree.setCurrentItem(entry["item"])
        QTest.mouseClick(dialog.confirm_button, Qt.MouseButton.LeftButton)
    assert dialog.import_button.isEnabled(), warnings
    QTest.mouseClick(dialog.import_button, Qt.MouseButton.LeftButton)
    assert len(records.vehicles()) == 1, warnings
    assert len(records.repairs(records.vehicles()[0]["id"])) == 2
    assert path.read_bytes() == original
    assert "Добавлено записей: 2" in dialog.report.toPlainText()
    dialog.edited = False
    dialog.reject()
