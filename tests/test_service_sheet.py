from PySide6.QtCore import QBuffer, QIODevice, QRect, QSize
from PySide6.QtPdf import QPdfDocument

from app.database.connection import initialize_database
from app.services.documents import Documents
from app.services.receipt_pdf import pdf_bytes
from app.services.records import Records
from app.ui.documents import DocumentDialog


def setup_repair(tmp_path):
    path = tmp_path / "sheet.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle(
        {
            "make": "Тестовая марка",
            "model": "Пример",
            "plate": "ТЕСТ 001",
            "vin": "TESTVIN00000000001",
        }
    )
    repair = records.save_repair(
        car,
        {
            "visit_date": "2026-11-21",
            "km": 123456,
            "description": "Учебная замена детали <скобки> & проверка",
            "amount_kopecks": 987654,
        },
    )
    return records, Documents(path), car, repair


def open_pdf(content):
    buffer = QBuffer()
    buffer.setData(content)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    pdf = QPdfDocument()
    pdf.load(buffer)
    assert pdf.status() == QPdfDocument.Status.Ready
    return pdf, buffer


def test_sheet_fields_no_prices_and_immutable_snapshot(application, tmp_path):
    records, service, car, repair = setup_repair(tmp_path)
    preview = service.create(repair, layout="service_sheet", preview=True)
    assert not service.history(repair)
    assert preview["number"] == "ПРЕДПРОСМОТР"
    sheet = service.create(repair, layout="service_sheet", recommendations="Учебная рекомендация")
    assert sheet["number"] == "СЛ-000001"
    original = service.pdf_content(sheet["id"])
    pdf, buffer = open_pdf(original)
    assert pdf.pageCount() == 1
    text = pdf.getAllText(0).text()
    for value in (
        "тест 001",
        "testvin00000000001",
        "21.11.26",
        "123456км",
        "<скобки>",
        "рекомендации:",
        "Учебная рекомендация",
    ):
        assert value in text
    assert "9876" not in text and "Итого" not in text and "Квитанция" not in text
    pdf.close()
    buffer.close()
    records.save_vehicle(records.vehicle(car) | {"plate": "ДРУГОЙ"}, car)
    service.save_settings({"name": "Другой сервис"})
    assert Documents(service.path).pdf_content(sheet["id"]) == original
    assert (
        Documents(service.path).get(sheet["id"])["payload"]["recommendations"]
        == "Учебная рекомендация"
    )
    receipt = service.create(repair)
    assert receipt["number"] == "КВ-000002"


def test_both_pdf_formats_share_original_full_header(application, tmp_path):
    _, service, _, repair = setup_repair(tmp_path)
    size = QSize(1191, 1684)
    bounds = QRect(170, 112, 965, 182)
    headers = []
    for layout in ("receipt", "service_sheet"):
        preview = service.create(repair, layout=layout, preview=True)
        pdf, buffer = open_pdf(pdf_bytes(preview))
        headers.append(pdf.render(0, size).copy(bounds))
        pdf.close()
        buffer.close()

    assert headers[0] == headers[1]
    # Светлые и тёмные участки есть и слева с рисунком, и справа с контактами.
    for start, end in ((0, 480), (710, bounds.width())):
        assert any(
            headers[0].pixelColor(x, y).lightness() < 128
            for x in range(start, end, 4)
            for y in range(0, bounds.height(), 4)
        )


def test_sheet_long_text_and_missing_fields(application, tmp_path):
    records, service, car, repair = setup_repair(tmp_path)
    records.save_repair(car, {"description": "", "visit_date": "2026-11-21", "km": None}, repair)
    preview = service.create(repair, layout="service_sheet", preview=True)
    # Импортированные записи допускают неизвестную дату.
    preview["payload"]["repair"]["visit_date"] = None
    pdf, buffer = open_pdf(pdf_bytes(preview))
    text = pdf.getAllText(0).text()
    assert "Не указана" in text and "Пробег не указан" in text
    assert "рекомендации:" not in text
    pdf.close()
    buffer.close()
    sheet = service.create(
        repair,
        layout="service_sheet",
        description="Длинная запись & <текст> " * 900,
        recommendations="Проверка рекомендации\n" * 80 + "КОНЕЦ РЕКОМЕНДАЦИЙ",
    )
    pdf, buffer = open_pdf(service.pdf_content(sheet["id"]))
    assert pdf.pageCount() > 2
    text = "\n".join(pdf.getAllText(page).text() for page in range(pdf.pageCount()))
    assert "КОНЕЦ РЕКОМЕНДАЦИЙ" in text
    assert f"Страница {pdf.pageCount()}" in text
    for page in range(pdf.pageCount()):
        assert not pdf.render(page, QSize(595, 842)).isNull()
    pdf.close()
    buffer.close()


def test_sheet_draft_reopen_issue_and_history(application, tmp_path):
    records, service, _, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    assert dialog.kind.currentData() == "service_sheet"
    assert "Квитанция" not in dialog.pdf.getAllText(0).text()
    dialog.recommendations.setPlainText("Сохранённая рекомендация")
    dialog.save(close=False)
    dialog.reject()
    reopened = DocumentDialog(records, repair)
    assert reopened.kind.currentData() == "service_sheet"
    assert reopened.recommendations.toPlainText() == "Сохранённая рекомендация"
    assert reopened.create()
    original = reopened.content
    assert service.draft(repair) is None
    reopened.reject()
    archived = DocumentDialog(records, repair)
    assert archived.content == original
    assert archived.kind.currentData() == "service_sheet"
    assert not archived.recommendations.isEnabled()
    assert "Сервисный лист" in archived.history.currentText()
    archived.reject()
