import pytest
from PySide6.QtCore import QBuffer, QDate, QIODevice, QRect, QSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QLabel, QWidget

from app.database.connection import initialize_database
from app.services.documents import Documents
from app.services.receipt_pdf import pdf_bytes
from app.services.records import Records
from app.ui.documents import DocumentDialog
from app.ui.vehicles import RepairDialog, open_document_window, open_repair_window


def setup_repair(tmp_path):
    path = tmp_path / "sheet.sqlite3"
    initialize_database(path)
    records = Records(path)
    car = records.save_vehicle(
        {
            "make": "Тестовая марка",
            "model": "Пример",
            "plate": "ТЕСТ 001",
            "vin": "testvin00000000001",
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


def test_repair_notes_follow_vehicle_and_date(application, tmp_path):
    records, documents, car, repair = setup_repair(tmp_path)
    editor = RepairDialog(records, car, records.repairs(car)[0])
    editor.recommendations.setPlainText("Осмотреть ремень")
    assert editor.save(close=False)
    editor.close()
    dialog = DocumentDialog(records, repair)
    assert dialog.recommendations.toPlainText() == "Осмотреть ремень"
    dialog.recommendations.setPlainText("Проверить натяжение")
    assert dialog.save(close=False)
    assert dialog.create()
    issued = dialog.content
    dialog.close()
    assert records.repairs(car)[0]["recommendations"] == "Проверить натяжение"
    next_visit = RepairDialog(records, car)
    next_visit.visit_date.setDate(QDate(2026, 11, 20))
    assert next_visit.recommendations.toPlainText() == ""
    next_visit.visit_date.setDate(QDate(2026, 11, 22))
    assert next_visit.recommendations.toPlainText() == "Проверить натяжение"
    assert not next_visit.description.toPlainText()
    next_visit.recommendations.selectAll()
    next_visit.recommendations.insertPlainText("Выполнено")
    next_visit.visit_date.setDate(QDate(2026, 11, 23))
    assert next_visit.recommendations.toPlainText() == "Выполнено"
    next_visit.recommendations.clear()
    assert next_visit.save(close=False)
    next_visit.close()
    assert records.previous_recommendations(car, "2026-11-24") == ""
    other = records.save_vehicle({"plate": "учебный2"})
    assert records.previous_recommendations(other, "2026-11-22") == ""
    assert documents.pdf_content(documents.history(repair)[0]["id"]) == issued


def test_shared_notes_in_two_open_windows(application, tmp_path):
    records, service, car, repair_id = setup_repair(tmp_path)
    owner = QWidget()
    editor = open_repair_window(records, car, owner, records.repairs(car)[0])
    document = open_document_window(records, repair_id, editor)
    editor.recommendations.setPlainText("Из окна ремонта")
    assert editor.save(close=False)
    assert document.recommendations.toPlainText() == "Из окна ремонта"
    document.description.setPlainText("Из документа")
    assert document.save(close=False)
    assert editor.description.toPlainText() == "Из документа"
    editor.recommendations.setPlainText("Сверить на следующем визите")
    assert editor.save(close=False)
    assert service.draft(repair_id)["recommendations"] == editor.recommendations.toPlainText()
    document.close()
    editor.close()
    owner.close()


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
        "TESTVIN00000000001",
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
    assert receipt["number"] == "ЗН-000002"


def test_both_pdf_formats_share_right_aligned_header(application, tmp_path):
    _, service, _, repair = setup_repair(tmp_path)
    size = QSize(1191, 1684)
    bounds = QRect(170, 112, 965, 182)
    headers = []
    for layout in ("receipt", "service_sheet"):
        preview = service.create(repair, layout=layout, preview=True)
        pdf, buffer = open_pdf(pdf_bytes(preview))
        assert "TESTVIN00000000001" in pdf.getAllText(0).text()
        headers.append(pdf.render(0, size).copy(bounds))
        pdf.close()
        buffer.close()

    assert headers[0] == headers[1]
    # Правая граница краски каждой из шести строк шапки, рендер в масштабе 2:1.
    ends = []
    for top, bottom in ((22, 42), (54, 69), (75, 88), (99, 111), (118, 129), (136, 147)):
        ends.append(
            max(
                x
                for x in range(715, bounds.width())
                if any(
                    color.alpha() > 200 and color.lightness() < 145
                    for y in range(round(1.135 * top), round(1.135 * bottom) + 2)
                    for color in (headers[0].pixelColor(x, y),)
                )
            )
        )
    assert max(ends) - min(ends) <= 3
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


@pytest.mark.parametrize("layout", ["service_sheet", "receipt"])
def test_document_prices_are_optional_and_survive_draft_and_issue(application, tmp_path, layout):
    records, service, _, repair = setup_repair(tmp_path)
    dialog = DocumentDialog(records, repair)
    assert [dialog.kind.itemData(i) for i in range(dialog.kind.count())] == [
        "service_sheet",
        "receipt",
    ]
    dialog.kind.setCurrentIndex(dialog.kind.findData(layout))
    assert not dialog.items_panel.isHidden()
    assert any(label.text() == "Цена необязательна" for label in dialog.findChildren(QLabel))
    dialog.add_item("part", values={"title": "Фильтр <тест>", "quantity": "2", "price": ""})
    dialog.add_item("part", values={"title": "", "quantity": "1", "price": ""})
    dialog.add_item("work", values={"title": "Замена фильтра", "quantity": "1,25", "price": ""})
    dialog.preview()
    text = dialog.pdf.getAllText(0).text()
    assert "Фильтр <тест>" in text and "Замена фильтра" in text
    assert "Итого" not in text and "₽" not in text and "Цена, ₽" not in text
    assert len(dialog.state()["items"]) == 2

    dialog.item_tables["part"].item(0, 2).setText("100,01")
    assert dialog.save(close=False)
    dialog.reject()

    reopened = DocumentDialog(records, repair)
    assert reopened.kind.currentData() == layout
    assert reopened.item_tables["work"].item(0, 2).text() == ""
    text = reopened.pdf.getAllText(0).text()
    assert text.index("Фильтр <тест>") < text.index("Замена фильтра")
    assert "100,01" in text and "200,02" in text
    caption = (
        "Итого по указанным ценам"
        if layout == "service_sheet"
        else "Итого по заказ-наряду (по указанным ценам)"
    )
    start = text.index(caption)
    end = text.index("₽", start)
    assert (
        reopened.pdf.getSelectionAtIndex(0, start, end - start + 1).boundingRectangle().right()
        > 530
    )
    assert "Получение оплаты" not in text
    assert reopened.create()
    original = reopened.content
    assert reopened.current_document["payload"]["total_kopecks"] == 20002
    assert service.draft(repair) is None
    reopened.reject()

    archived = DocumentDialog(records, repair)
    assert archived.content == original
    assert archived.item_tables["work"].item(0, 2).text() == ""
    assert not archived.items_panel.isEnabled()
    archived.reject()
