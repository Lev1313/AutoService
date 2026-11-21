"""Многостраничный PDF A4 с русскими шрифтами и сохранением точных сумм."""

import os
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services.documents import item_total_label, quantity_text
from app.services.foltz_pdf import FOLTZ_PAGE, draw_foltz_header
from app.services.records import amount_text, mileage_text


def register_fonts():
    if "Receipt" in pdfmetrics.getRegisteredFontNames():
        return
    directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name, filename in (("Receipt", "arial.ttf"), ("ReceiptBold", "arialbd.ttf")):
        path = directory / filename
        if not path.is_file():
            raise RuntimeError("Не найден системный шрифт Arial для русскоязычного PDF.")
        pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily("Receipt", normal="Receipt", bold="ReceiptBold")


def human_date(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "Не указана"


def pdf_bytes(document: dict) -> bytes:
    try:
        return build_pdf(document)
    except (OSError, ValueError, RuntimeError):
        raise
    except Exception as error:
        raise RuntimeError(f"Не удалось сформировать PDF: {error}") from error


def build_pdf(document: dict) -> bytes:
    if document["payload"].get("layout") == "service_sheet":
        from app.services.service_sheet_pdf import build_service_sheet

        return build_service_sheet(document)
    register_fonts()
    data = document["payload"]
    service, vehicle, repair = data["service"], data["vehicle"], data["repair"]
    stream = BytesIO()
    style = ParagraphStyle("Body", fontName="Receipt", fontSize=10, leading=14, spaceAfter=5)
    small = ParagraphStyle(
        "Small", parent=style, fontSize=8, leading=11, textColor=colors.HexColor("#536174")
    )
    heading = ParagraphStyle(
        "Heading", parent=style, fontName="ReceiptBold", fontSize=19, leading=24, spaceAfter=8
    )
    section = ParagraphStyle(
        "Section",
        parent=style,
        fontName="ReceiptBold",
        fontSize=11,
        leading=15,
        spaceBefore=9,
        spaceAfter=6,
    )
    right = ParagraphStyle("Right", parent=style, alignment=TA_RIGHT)

    def paragraph(value, selected=style):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), selected)

    # Только первая страница содержит шапку и увеличенный верхний отступ.
    flow = [Spacer(1, 151 - 16 * mm)]
    if service["name"]:
        flow.append(paragraph(service["name"], section))
    for key in ("address", "phone", "requisites"):
        if service[key]:
            flow.append(paragraph(service[key], small))
    flow.append(Spacer(1, 4 * mm))
    flow.append(paragraph(f"Квитанция {document['number']}", heading))
    flow.append(paragraph(f"Составлена: {human_date(document['created_at'][:10])}"))
    vehicle_name = " ".join(filter(None, (vehicle["make"], vehicle["model"]))) or "Автомобиль"
    details = [
        ("Автомобиль", f"{vehicle_name} · {vehicle['plate'] or 'госномер не указан'}"),
        (
            "Контакт",
            f"{vehicle['contact_name'] or 'Не указан'} · {vehicle['phone'] or 'телефон не указан'}",
        ),
        ("Дата ремонта", human_date(repair["visit_date"])),
    ]
    if vehicle["vin"]:
        details.append(("VIN", vehicle["vin"]))
    if repair["km"] is not None or repair.get("carried_km") is not None:
        details.append(("Пробег, км", mileage_text(repair)))
        if repair.get("carried_km") is not None:
            details.append(("", "Последний известный; новый пробег не указан."))
    info = Table(
        [[paragraph(key, small), paragraph(value)] for key, value in details],
        colWidths=[32 * mm, 142 * mm],
    )
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    flow.append(info)
    flow.append(paragraph("Описание ремонта", section))
    # Отдельные абзацы позволяют переносить длинную запись на несколько страниц.
    for line in (data["description"] or "Описание не указано").splitlines():
        flow.append(paragraph(line or " "))
    if document["kind"] == "detailed":
        flow.append(paragraph("Работы и запчасти", section))
        has_prices = data["total_kopecks"] is not None
        headers = ["Тип", "Наименование", "Кол-во"]
        widths = [20 * mm, 136 * mm, 18 * mm]
        if has_prices:
            headers.extend(("Цена, ₽", "Сумма, ₽"))
            widths = [20 * mm, 83 * mm, 18 * mm, 25 * mm, 28 * mm]
        rows = [[paragraph(value, small) for value in headers]]
        for item in data["items"]:
            row = [
                paragraph("Работа" if item["kind"] == "work" else "Запчасть", small),
                paragraph(item["title"]),
                paragraph(quantity_text(item["quantity_milli"]), right),
            ]
            if has_prices:
                row.extend(
                    (
                        paragraph(amount_text(item["unit_price_kopecks"]), right),
                        paragraph(amount_text(item["total_kopecks"]), right),
                    )
                )
            rows.append(row)
        table = LongTable(
            rows,
            colWidths=widths,
            repeatRows=1,
            splitInRow=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1f7")),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#bac6d3")),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dce3ec")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        flow.append(table)
        if has_prices:
            total = amount_text(data["total_kopecks"])
            caption = item_total_label(data["items"])
            flow.append(
                KeepTogether([Spacer(1, 7 * mm), paragraph(f"{caption}: {total} ₽", heading)])
            )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Receipt", 8)
        canvas.setFillColor(colors.HexColor("#536174"))
        canvas.drawString(18 * mm, 11 * mm, document["number"])
        canvas.drawRightString(FOLTZ_PAGE[0] - 18 * mm, 11 * mm, f"Страница {doc.page}")
        canvas.restoreState()

    def first_page(canvas, doc):
        draw_foltz_header(canvas)
        footer(canvas, doc)

    template = SimpleDocTemplate(
        stream,
        pagesize=FOLTZ_PAGE,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Квитанция {document['number']}",
        author=service["name"] or "AutoService",
    )
    template.build(flow, onFirstPage=first_page, onLaterPages=footer)
    return stream.getvalue()


def save_pdf(content: bytes, path: Path, overwrite=False):
    path = Path(path)
    if not content.startswith(b"%PDF-"):
        raise ValueError("Не удалось сформировать PDF.")
    if path.exists() and not overwrite:
        raise FileExistsError("Файл уже существует. Выберите другое имя или подтвердите замену.")
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=path.parent, prefix=".autoservice-", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Windows rename не заменяет существующий файл и защищает от гонки имени.
            temporary.rename(path)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
