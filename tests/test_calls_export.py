import json
import sqlite3
from contextlib import closing
from hashlib import sha256
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QMessageBox
from test_txt_import import entries

from app.database.connection import initialize_database
from app.services import export_data, export_xlsx
from app.services.documents import Documents
from app.services.export_data import export_database
from app.services.maintenance import Maintenance
from app.services.records import Records
from app.services.txt_import import TxtImport
from app.ui.export import ExportDialog
from app.ui.main_window import MainWindow
from app.ui.maintenance import CallsPage


def exact_text(path, encoding="utf-8"):
    with path.open(encoding=encoding, newline="") as stream:
        return stream.read()


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle(
        {
            "make": "Skoda",
            "model": "Octavia",
            "vin": "000123",
            "plate": "=1+1",
            "contact_name": '=HYPERLINK("bad")',
            "phone": "000123",
        }
    )
    records.save_contact({"name": "Без машины", "phone": "+000"})
    records.save_repair(car, {"visit_date": "2026-01-01", "km": 1000, "amount_kopecks": 0})
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-02-01",
            "carried_km": 1000,
            "carried_on": "2026-01-01",
            "amount_kopecks": 150050,
            "description": "Очень длинная запись\r\n" + "Я" * 40000 + "\n😀" * 3000,
        },
    )
    docs = Documents(path)
    # Use the short repair for PDF QA; long text is preserved in the repair export.
    document = docs.create(records.repairs(car)[1]["id"])
    records.set_repair_deleted(car, repair)
    source_path = tmp_path / "источник.txt"
    source_path.write_bytes("01.03.2026\r\nВымышленный импорт\r\n".encode("cp1251"))
    importer = TxtImport(path)
    importer.commit(entries(importer.preview(source_path), car))
    service = Maintenance(path)
    plan = service.save_plan(
        car, {"title": "Тестовое ТО", "months": 6, "lead_days": 0, "baseline_date": "2026-01-01"}
    )
    task = next(row for row in service.tasks(include_handled=True) if row["plan_id"] == plan)
    service.record_call(task["id"], "postponed", "2026-11-20", "Тестовый перенос")
    return records, car, repair, document, source_path


def workbook_rows(path):
    ns = {"s": export_xlsx.NS}
    result = {}
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for i, sheet in enumerate(workbook.findall("s:sheets/s:sheet", ns), 1):
            tree = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
            rows = []
            for row in tree.findall("s:sheetData/s:row", ns):
                cells = {}
                for cell in row:
                    assert cell.find("s:f", ns) is None
                    value = (
                        cell.find("s:is/s:t", ns)
                        if cell.get("t") == "inlineStr"
                        else cell.find("s:v", ns)
                    )
                    cells["".join(filter(str.isalpha, cell.get("r")))] = (
                        cell.get("t"),
                        value.text if value is not None else "",
                    )
                rows.append(cells)
            result[sheet.get("name")] = rows
    return result


@pytest.mark.parametrize("format", ["txt", "xlsx"])
def test_export_whole_snapshot_originals_and_exact_values(data, tmp_path, format):
    records, car, repair, document, source_path = data
    before = records.path.read_bytes()
    destination = tmp_path / f"выгрузка-{format}"
    metadata = export_database(records.path, destination, format)
    assert records.path.read_bytes() == before
    payload = json.loads((destination / "data.json").read_text(encoding="utf-8"))
    assert metadata["counts"]["repairs"] == 3
    assert metadata["counts"]["contacts"] == 2
    exported = next(r for r in payload["repairs"] if r["id"] == repair)
    original = next(r for r in records.repairs(car, True) if r["id"] == repair)
    assert exported["description"] == original["description"]
    assert exported["deleted_at"] and exported["carried_km"] == 1000
    assert exported["km"] is None
    assert exported["amount_kopecks"] == 150050 and exported["amount_rub"] == "1500,50"
    assert any(r["amount_kopecks"] is None for r in payload["repairs"])
    assert any(r["amount_kopecks"] == 0 for r in payload["repairs"])
    source = payload["import_sources"][0]
    assert (destination / source["file"]).read_bytes() == source_path.read_bytes()
    pdf = payload["document_files"][0]
    assert (destination / pdf["file"]).read_bytes() == Documents(records.path).pdf_content(
        document["id"]
    )
    for filename, digest in metadata["files"].items():
        assert sha256((destination / filename).read_bytes()).hexdigest() == digest
    if format == "txt":
        text = exact_text(destination / "AutoService.txt", "utf-8-sig")
        assert original["description"] in text
        assert "?" in text and "Удалён" in text
        assert (destination / f"vehicles/vehicle-{car}.txt").is_file()
    else:
        sheets = workbook_rows(destination / "AutoService.xlsx")
        assert set(export_data.TABLES.values()).issubset(sheets)
        all_cells = [value for rows in sheets.values() for row in rows for value in row.values()]
        assert ("inlineStr", "=1+1") in all_cells
        assert ("inlineStr", "000123") in all_cells
        sidecars = [exact_text(p) for p in (destination / "texts").glob("*.txt")]
        assert original["description"] in sidecars
    with pytest.raises(FileExistsError):
        export_database(records.path, destination, format)
    assert records.path.read_bytes() == before


def test_export_failure_and_omitted_originals(data, tmp_path, monkeypatch):
    records, *_ = data
    before = records.path.read_bytes()

    def fail(*_):
        raise OSError("Вымышленный отказ диска")

    original = export_data.write_workbook
    monkeypatch.setattr(export_data, "write_workbook", fail)
    target = tmp_path / "failed"
    with pytest.raises(OSError):
        export_database(records.path, target, "xlsx")
    assert not target.exists()
    assert not list(tmp_path.glob(".autoservice-export-*"))
    assert records.path.read_bytes() == before
    monkeypatch.setattr(export_data, "write_workbook", original)
    export_database(records.path, target, "xlsx", False)
    assert not (target / "pdf").exists() and not (target / "originals").exists()
    data = json.loads((target / "data.json").read_text(encoding="utf-8"))
    assert data["import_sources"][0]["file"] is None


def test_excel_control_characters_unicode_limits_and_row_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(export_xlsx, "MAX_ROWS", 4)
    values = ["=1+1", "000123", "Я" * 32767, "😀" * 17000, "control\x00text", "literal _x0041_"]
    saved = {}

    def sidecar(name, text):
        saved[name] = text
        return f"texts/{name}.txt"

    path = tmp_path / "limits.xlsx"
    export_xlsx.write_workbook(path, [("Проверка", ["Текст"], [[v] for v in values])], sidecar)
    sheets = workbook_rows(path)
    assert len(sheets) == 2
    assert list(saved.values()) == values[3:]
    assert sheets["Проверка 1"][1]["A"] == ("inlineStr", "=1+1")


def test_calendar_same_tasks_manual_date_handled_and_unknown(application, tmp_path):
    path = tmp_path / "calls.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle({"plate": "ТЕСТ"})
    service = Maintenance(path)
    service.save_plan(
        car, {"title": "Известная дата", "months": 6, "lead_days": 0, "baseline_date": "2026-01-01"}
    )
    service.save_plan(car, {"title": "Нет срока", "months": 6, "lead_days": 0})
    window = MainWindow(path)
    assert [window.navigation.item(i).text() for i in range(window.navigation.count())].count(
        "Календарь"
    ) == 0
    assert len(window.findChildren(CallsPage)) == 1
    page = window.calls_page
    assert page.filter.currentData() == "due"
    page.view_mode.setCurrentIndex(1)
    assert page.filter.currentData() == "due"
    page.view_mode.setCurrentIndex(0)
    assert page.filter.currentData() == "due"
    page.filter.setCurrentIndex(page.filter.findData("all"))
    known = next(row for row in page.rows if row["due_date"])
    count = len(service.tasks(include_handled=True))
    page.view_mode.setCurrentIndex(1)
    page.calendar.setSelectedDate(QDate.fromString(known["due_date"], "yyyy-MM-dd"))
    assert [row["id"] for row in page.rows] == [known["id"]]
    service.record_call(known["id"], "postponed", "2026-11-20", "Перенос")
    page.refresh()
    assert not page.rows
    page.calendar.setSelectedDate(QDate(2026, 11, 20))
    assert page.rows[0]["id"] == known["id"]
    assert page.rows[0]["manual_date"] == "2026-11-20"
    service.record_call(known["id"], "contacted")
    page.filter.setCurrentIndex(page.filter.findData("handled"))
    assert page.rows[0]["id"] == known["id"]
    page.show_unknown()
    assert page.rows[0]["title"] == "Нет срока"
    assert page.view_mode.currentData() == "list"
    unknown = page.rows[0]["id"]
    page.view_mode.setCurrentIndex(1)
    assert page.filter.currentData() == "unknown"
    assert [row["id"] for row in page.rows] == [unknown]
    page.calendar.setSelectedDate(QDate(2026, 12, 1))
    assert [row["id"] for row in page.rows] == [unknown]
    page.view_mode.setCurrentIndex(0)
    assert page.filter.currentData() == "unknown"
    assert len(service.tasks(include_handled=True)) == count
    window.close()


def test_export_dialog_uses_selected_format(application, data, tmp_path, monkeypatch):
    records, *_ = data
    monkeypatch.setattr(QMessageBox, "information", lambda *_: None)
    dialog = ExportDialog(records)
    dialog.directory.setText(str(tmp_path))
    dialog.format.setCurrentIndex(1)
    dialog.export()
    assert (dialog.destination / "AutoService.xlsx").is_file()
    dialog.close()


def test_export_keeps_a_consistent_snapshot_with_another_writer(data, tmp_path, monkeypatch):
    records, car, *_ = data
    with closing(sqlite3.connect(records.path)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    writer = export_data.write_txt

    def during_export(stage, snapshot, captured):
        records.save_contact({"name": "Создан после начала выгрузки", "phone": "000-after"})
        writer(stage, snapshot, captured)

    monkeypatch.setattr(export_data, "write_txt", during_export)
    target = tmp_path / "snapshot"
    export_database(records.path, target)
    exported = json.loads((target / "data.json").read_text(encoding="utf-8"))
    assert len(exported["contacts"]) == 2
    assert len(records.contacts()) == 3


def test_publish_retries_transient_windows_lock_without_overwrite(tmp_path, monkeypatch):
    from pathlib import Path

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "record.txt").write_text("Тест")
    destination = tmp_path / "complete"
    rename = Path.rename
    attempts = []

    def locked_once(source, target):
        attempts.append(1)
        if len(attempts) == 1:
            error = PermissionError("Временная блокировка Windows")
            error.winerror = 5
            raise error
        return rename(source, target)

    monkeypatch.setattr(Path, "rename", locked_once)
    monkeypatch.setattr(export_data, "sleep", lambda _: None)
    export_data.publish_directory(stage, destination)
    assert len(attempts) == 2 and (destination / "record.txt").read_text() == "Тест"


def test_excel_preserves_crlf_in_inline_text(tmp_path):
    path = tmp_path / "newlines.xlsx"
    value = "Первая строка\r\nВторая строка\rТретья\nЧетвёртая"
    export_xlsx.write_workbook(
        path, [("Текст", ["Описание"], [[value]])], lambda *_: pytest.fail("Короткий текст")
    )
    assert workbook_rows(path)["Текст"][1]["A"] == ("inlineStr", value)
