import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from app.database.connection import (
    SCHEMA_V2,
    SCHEMA_V3,
    SCHEMA_VERSION,
    backup_database,
    connect,
    initialize_database,
    restore_database,
)
from app.services.documents import Documents, TotalMismatch, normalize_items
from app.services.maintenance import Maintenance
from app.services.receipt_pdf import pdf_bytes, save_pdf
from app.services.records import Records, amount_text
from app.ui.documents import DocumentDialog


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "documents.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle(
        {
            "make": "Тестовая марка",
            "model": "Пример",
            "plate": "ТЕСТ 123",
            "contact_name": "Вымышленный контакт",
            "phone": "000-000-00-00",
        }
    )
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-11-10",
            "km": 120000,
            "description": "Тестовая запись\n<не разметка> & проверка",
            "amount_kopecks": 150050,
        },
    )
    return records, Documents(path), car, repair


def load_pdf(content, parent=None):
    buffer = QBuffer(parent)
    buffer.setData(content)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(parent)
    document.load(buffer)
    assert document.status() == QPdfDocument.Status.Ready
    return document, buffer


def test_snapshots_are_immutable_and_numbers_are_unique_after_reopen(data):
    records, service, car, repair = data
    assert service.settings() == {"name": "", "address": "", "phone": "", "requisites": ""}
    service.save_settings({"name": "Вымышленный сервис", "phone": "000"})
    document = service.create(repair)
    original_pdf = service.pdf_content(document["id"])
    service.save_settings({"name": "Новое название"})
    records.save_vehicle(records.vehicle(car) | {"plate": "ИЗМЕНЁН"}, car)
    values = records.repairs(car)[0] | {"description": "Изменено", "amount_kopecks": 0}
    records.save_repair(car, values, repair)
    reopened = Documents(records.path)
    assert reopened.get(document["id"]) == document
    assert reopened.pdf_content(document["id"]) == original_pdf
    newer = reopened.create(repair)
    assert newer["number"] != document["number"]
    assert newer["payload"]["vehicle"]["plate"] == "изменён"
    assert document["payload"]["vehicle"]["plate"] == "тест 123"
    assert document["payload"]["service"]["name"] == "Вымышленный сервис"
    assert len(reopened.history(repair)) == 2


def test_render_failure_rolls_back_document_and_number(data, monkeypatch):
    _, service, _, repair = data
    import app.services.receipt_pdf as renderer

    original = renderer.pdf_bytes
    draft = {"kind": "simple", "description": "Сохранённый черновик", "items": []}
    service.save_draft(repair, draft)
    monkeypatch.setattr(renderer, "pdf_bytes", lambda *_: (_ for _ in ()).throw(OSError("Диск")))
    with pytest.raises(OSError):
        service.create(repair, **draft, consume_draft=True)
    assert service.history(repair) == []
    assert service.draft(repair) == draft
    monkeypatch.setattr(renderer, "pdf_bytes", original)
    assert service.create(repair, **draft, consume_draft=True)["number"] == "КВ-000001"
    assert service.draft(repair) is None


def test_pdf_integrity_and_backup_restore_include_original_bytes(data):
    records, service, _, repair = data
    document = service.create(repair)
    original = service.pdf_content(document["id"])
    backup = backup_database(records.path)
    with connect(records.path) as connection:
        connection.execute("UPDATE document_files SET content=?", (b"damaged",))
    with pytest.raises(ValueError, match="целостность"):
        service.pdf_content(document["id"])
    restore_database(records.path, backup)
    assert Documents(records.path).pdf_content(document["id"]) == original


def test_v3_migration_preserves_calendar_state_and_pre_update_backup(tmp_path):
    path = tmp_path / "v3.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO app_metadata VALUES ('application','AutoService')")
        for statement in (*SCHEMA_V2, *SCHEMA_V3):
            connection.execute(statement)
        connection.execute("INSERT INTO vehicles(id,plate) VALUES (1,'ТЕСТ')")
        connection.execute(
            "INSERT INTO maintenance_plans(id,vehicle_id,title,months) VALUES (1,1,'ТО',6)"
        )
        connection.execute(
            "INSERT INTO call_tasks(plan_id,cycle_no,manual_date,status) "
            "VALUES (1,1,'2026-12-20','contacted')"
        )
        connection.execute("PRAGMA user_version=3")
        connection.commit()
    initialize_database(path)
    task = Maintenance(path).tasks(include_handled=True)[0]
    assert task["status"] == "contacted" and task["manual_date"] == "2026-12-20"
    with connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    backup = next((tmp_path / "backups").glob("*.sqlite3"))
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_detailed_items_exact_rounding_and_total_confirmation(data):
    records, service, car, repair = data
    items = [
        {"kind": "work", "title": "Тестовая работа", "quantity": "1,5", "price": "1000"},
        {"kind": "part", "title": "Тестовая деталь", "quantity": "0,5", "price": "0,01"},
    ]
    normalized = normalize_items(items)
    assert normalized[0]["total_kopecks"] == 150000
    assert normalized[1]["total_kopecks"] == 1
    with pytest.raises(TotalMismatch):
        service.create(repair, "detailed", items)
    assert service.history(repair) == []
    document = service.create(repair, "detailed", items, allow_difference=True)
    assert document["payload"]["total_kopecks"] == 150001
    assert document["payload"]["total_difference_confirmed"]
    assert records.repairs(car)[0]["amount_kopecks"] == 150050


@pytest.mark.parametrize(
    "item",
    [
        {"title": ""},
        {"title": "Работа", "quantity": "0", "price": "0"},
        {"title": "Работа", "quantity": "NaN", "price": "1"},
        {"title": "Работа", "quantity": "1.0001", "price": "1"},
        {"title": "Работа", "quantity": "1", "price": "-1"},
    ],
)
def test_invalid_item_never_becomes_zero(item):
    with pytest.raises(ValueError):
        normalize_items([item])


def test_preview_does_not_reserve_number_and_incomplete_draft_survives(data):
    _, service, _, repair = data
    preview = service.create(repair, preview=True)
    assert preview["id"] is None
    assert service.history(repair) == []
    draft = {"kind": "detailed", "description": "Черновик", "items": [{"title": "Не закончено"}]}
    service.save_draft(repair, draft)
    assert Documents(service.path).draft(repair) == draft
    assert service.create(repair)["number"] == "КВ-000001"
    assert service.draft(repair) == draft


def test_pdf_cyrillic_optional_prices_and_atomic_export(application, data, tmp_path):
    _, service, _, repair = data
    content = pdf_bytes(service.create(repair))
    pdf, buffer = load_pdf(content)
    text = pdf.getAllText(0).text()
    assert "Квитанция" in text
    assert "тест 123" in text
    assert "Итого" not in text and "1500,50" not in text
    assert "не разметка" in text
    assert "Получение оплаты" not in text
    pdf.close()
    buffer.close()
    items = [{"title": "Бесплатная работа", "quantity": "1", "price": "0"}]
    free, buffer = load_pdf(
        pdf_bytes(service.create(repair, "detailed", items, allow_difference=True))
    )
    assert "Итого: 0,00 ₽" in free.getAllText(0).text()
    free.close()
    buffer.close()
    target = tmp_path / "receipt.pdf"
    save_pdf(content, target)
    with pytest.raises(FileExistsError):
        save_pdf(content, target)
    with pytest.raises(ValueError):
        save_pdf(b"invalid", target, overwrite=True)
    assert target.read_bytes() == content


def test_long_description_and_table_span_pages_with_end_preserved(application, data):
    _, service, _, repair = data
    description = (
        "НАЧАЛО ЗАПИСИ\n" + "Проверка длинной записи обслуживания.\n" * 160 + "КОНЕЦ ЗАПИСИ"
    )
    items = [{"title": f"Позиция {index}", "quantity": "1", "price": "10"} for index in range(100)]
    document = service.create(repair, "detailed", items, description, allow_difference=True)
    pdf, buffer = load_pdf(pdf_bytes(document))
    assert pdf.pageCount() >= 5
    text = "\n".join(pdf.getAllText(page).text() for page in range(pdf.pageCount()))
    assert "НАЧАЛО ЗАПИСИ" in text and "КОНЕЦ ЗАПИСИ" in text
    assert "Позиция 99" in text
    assert "1000,00" in text
    assert "Получение оплаты" not in text
    assert f"Страница {pdf.pageCount()}" in text
    pdf.close()
    buffer.close()


def test_print_pipeline_to_pdf_and_selected_pages(application, data, tmp_path):
    _, service, _, repair = data
    description = "Тест печати\n" * 140
    content = pdf_bytes(service.create(repair, description=description))
    source = tmp_path / "source.pdf"
    source.write_bytes(content)
    target = tmp_path / "printed.pdf"
    # Изолируем native-компонент печати Windows: тест проверяет и код завершения процесса.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from app.bootstrap import create_application
from app.ui.documents import print_pdf
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
app = create_application([])
document = QPdfDocument()
document.load(sys.argv[1])
assert document.pageCount() >= 2
printer = QPrinter(QPrinter.PrinterMode.HighResolution)
printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
printer.setOutputFileName(sys.argv[2])
printer.setResolution(150)
printer.setFromTo(2, 2)
print_pdf(document, printer)
document.close()
""",
            str(source),
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    printed, printed_buffer = load_pdf(target.read_bytes())
    assert printed.pageCount() == 1
    assert not printed.render(0, printed.pagePointSize(0).toSize()).isNull()
    printed.close()
    printed_buffer.close()


def test_document_dialog_preview_save_history_and_stale_preview_guard(
    application, data, monkeypatch
):
    records, service, car, repair = data
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = DocumentDialog(records, repair)
    dialog.show()
    application.processEvents()
    assert dialog.pdf.pageCount() == 1, warnings
    assert service.history(repair) == []
    assert not dialog.export_button.isEnabled()
    assert dialog.print_button.text() == "Оформить и печатать"
    assert dialog.document_status.text() == "Черновик"
    dialog.description.setPlainText("Изменённый текст документа")
    assert dialog.content is None
    QTest.qWait(dialog.preview_timer.interval() + 100)
    assert "Изменённый текст документа" in dialog.pdf.getAllText(0).text()
    dialog.draft_button.click()
    assert service.draft(repair)["description"] == "Изменённый текст документа"
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    assert len(service.history(repair)) == 1, warnings
    assert service.draft(repair) is None
    assert dialog.export_button.isEnabled()
    assert dialog.print_button.text() == "Печать"
    assert dialog.document_status.text().startswith("Оформлена · ")
    assert not dialog.create_button.isEnabled()
    dialog.reject()
    corrected = "Уточнённое описание ремонта"
    records.save_repair(car, records.repairs(car)[0] | {"description": corrected}, repair)

    reopened = DocumentDialog(records, repair)
    document = reopened.current_document
    assert document["payload"]["description"] == "Изменённый текст документа"
    assert reopened.pdf.pageCount() == 1
    reopened.new_button.click()
    assert reopened.current_document is None
    assert reopened.description.toPlainText() == corrected
    assert corrected in reopened.pdf.getAllText(0).text()
    reopened.description.setPlainText("Редактирование второго черновика")
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    reopened.history.setCurrentIndex(reopened.history.findData(document["id"]))
    assert reopened.history.currentIndex() == -1
    assert reopened.description.toPlainText() == "Редактирование второго черновика"
    reopened.draft_button.click()
    assert reopened.document_status.text() == "Черновик сохранён"
    reopened.history.setCurrentIndex(reopened.history.findData(document["id"]))
    assert reopened.current_document["id"] == document["id"]
    assert len(service.history(repair)) == 1
    reopened.reject()

    with_draft = DocumentDialog(records, repair)
    assert with_draft.current_document["id"] == document["id"]
    with_draft.new_button.click()
    assert with_draft.description.toPlainText() == "Редактирование второго черновика"
    with_draft.reject()


def test_detailed_price_editor_groups_and_reopens_legacy_draft(application, data):
    records, service, _, repair = data
    work = {"kind": "work", "title": "Работа", "quantity": "1", "price": "800"}
    part = {"kind": "part", "title": "Фильтр", "quantity": "2", "price": "100"}
    service.save_draft(repair, {"kind": "detailed", "description": "Замена", "items": [work, part]})

    dialog = DocumentDialog(records, repair)
    dialog.show()
    application.processEvents()
    assert dialog.state()["items"] == [part, work]
    assert dialog.item_subtotals["part"].text() == f"{amount_text(20000)} ₽"
    assert dialog.item_subtotals["work"].text() == f"{amount_text(80000)} ₽"

    QTest.keyClick(dialog, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)
    QTest.keyClicks(dialog.focusWidget(), "Seal")
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
    QTest.keyClicks(dialog.focusWidget(), "125")
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier)
    QTest.keyClicks(dialog.focusWidget(), "Install")
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
    QTest.keyClicks(dialog.focusWidget(), "400")
    QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)

    expected = [
        part,
        {"kind": "part", "title": "Seal", "quantity": "1", "price": "125"},
        work,
        {"kind": "work", "title": "Install", "quantity": "1", "price": "400"},
    ]
    assert dialog.state()["items"] == expected
    assert dialog.total.text() == f"Итог позиций: {amount_text(152500)} ₽"
    assert not dialog.isVisible()
    assert service.draft(repair)["items"] == expected

    reopened = DocumentDialog(records, repair)
    assert reopened.state()["items"] == expected
    assert "тест 123" in reopened.pdf.getAllText(0).text()
    reopened.reject()


def test_issue_and_print_cancel_keeps_number(application, data, monkeypatch):
    from PySide6.QtWidgets import QDialog

    import app.ui.documents as documents_ui

    records, service, _, repair = data
    opened = []
    warnings = []

    class Printer:
        PrinterMode = documents_ui.QPrinter.PrinterMode

        def __init__(self, mode):
            assert mode == self.PrinterMode.HighResolution

        def setPageSize(self, size):
            pass

        def setResolution(self, resolution):
            assert resolution == 300

    class CancelPrintDialog:
        def __init__(self, printer, parent):
            opened.append(parent.current_document["number"])

        def setMinMax(self, first, last):
            assert (first, last) == (1, 1)

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(documents_ui.QPrinterInfo, "availablePrinterNames", lambda: ["Тест"])
    monkeypatch.setattr(documents_ui, "QPrinter", Printer)
    monkeypatch.setattr(documents_ui, "QPrintDialog", CancelPrintDialog)
    dialog = DocumentDialog(records, repair)
    assert service.history(repair) == []
    dialog.kind.setCurrentIndex(dialog.kind.findData("receipt"))
    QTest.qWait(dialog.preview_timer.interval() + 100)
    dialog.print_button.click()
    assert not warnings
    assert opened == ["КВ-000001"]
    assert "Квитанция КВ-000001 сохранена" in dialog.message.text()
    assert dialog.export_button.isEnabled()
    dialog.print_button.click()
    assert opened == ["КВ-000001", "КВ-000001"]
    assert len(service.history(repair)) == 1
    dialog.reject()


def test_print_without_available_printers(application, data, monkeypatch):
    from app.ui.documents import QPrinterInfo

    records, service, _, repair = data
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(QPrinterInfo, "availablePrinterNames", lambda: [])
    dialog = DocumentDialog(records, repair)
    dialog.print_button.click()
    assert dialog.print_button.isEnabled(), warnings
    assert len(service.history(repair)) == 1
    assert len(warnings) == 1
    assert "Диспетчер печати" in warnings[0]
    assert "Сохранить PDF" in warnings[0]
    assert dialog.export_button.isEnabled()
    dialog.reject()
