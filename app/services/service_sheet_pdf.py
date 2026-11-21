"""Сервисный лист по оригинальному бланку владельца, без зависимости от Word."""

import os
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Paragraph, Spacer

from app.services.documents import item_total_label, quantity_text
from app.services.foltz_pdf import FOLTZ_PAGE, draw_foltz_header
from app.services.receipt_pdf import register_fonts
from app.services.records import amount_text, mileage_text

TEXT_LEFT = 67.104
FIRST_BASELINE = 622.06
BODY_SIZE = 14.04
LINE_HEIGHT = 19.5
BODY_WIDTH = 484.03
BOTTOM_MARGIN = 45


class ServiceSheetBand(Flowable):
    """Векторная полоса и надпись FOLTZ MASTER по меркам исходного PDF."""

    def __init__(self):
        super().__init__()
        self.width = BODY_WIDTH
        self.height = 25.2

    def draw(self):
        canvas = self.canv
        canvas.setFillGray(0.98)
        canvas.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        canvas.setFillGray(0)
        for x, y, width, height in (
            (0, 15.6, 366.17, 0.72),
            (0, 17.04, 366.17, 0.72),
            (366.166, 16.2, 3, 0.84),
            (369.166, 15.84, 114.86, 0.72),
        ):
            canvas.rect(x, y, width, height, stroke=0, fill=1)
        canvas.setFont("ServiceSheetWordmark", 14.52)
        letter_positions = (
            434.35,
            443.26,
            454.54,
            464.26,
            473.98,
            487.3,
            500.98,
            511.42,
            519.46,
            529.06,
            538.78,
        )
        for letter, x in zip("FOLTZMASTER", letter_positions, strict=True):
            for offset, shade in ((1.92, 0.6), (0, 0), (0.96, 0)):
                canvas.setFillGray(shade)
                canvas.drawString(x - TEXT_LEFT + offset, 14.76 - offset, letter)


def build_service_sheet(document: dict) -> bytes:
    register_fonts()
    if "ServiceSheet" not in pdfmetrics.getRegisteredFontNames():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name, filename in (
            ("ServiceSheetWordmark", "timesbd.ttf"),
            ("ServiceSheetBold", "comicbd.ttf"),
            ("ServiceSheet", "comic.ttf"),
        ):
            path = fonts / filename
            if not path.is_file():
                raise RuntimeError(f"Не найден шрифт сервисного листа: {filename}.")
            pdfmetrics.registerFont(TTFont(name, str(path)))
    data = document["payload"]
    repair, vehicle = data["repair"], data["vehicle"]
    body = ParagraphStyle(
        "SheetBody",
        fontName="ServiceSheet",
        fontSize=BODY_SIZE,
        leading=LINE_HEIGHT,
    )
    bold = ParagraphStyle("SheetBold", parent=body, fontName="ServiceSheetBold", keepWithNext=True)

    def paragraph(text, style=body):
        return Paragraph(escape(text), style)

    name = " ".join(
        filter(
            None, (vehicle["plate"], vehicle["vin"].lower(), vehicle["model"] or vehicle["make"])
        )
    )
    visit = repair["visit_date"]
    mileage = "Пробег не указан"
    if repair.get("km") is not None:
        marker = " (?)" if not repair.get("verified", True) else ""
        mileage = f"{repair['km']}км{marker}"
    if repair.get("carried_km") is not None:
        mileage = f"{mileage_text(repair)} км"
    flow = [
        paragraph(name or "Автомобиль не указан"),
        Spacer(1, LINE_HEIGHT),
        paragraph(date.fromisoformat(visit).strftime("%d.%m.%y") if visit else "Не указана"),
        paragraph(mileage),
    ]
    if repair.get("carried_km") is not None:
        flow.append(paragraph("Последний известный пробег. Новый замер не указан."))
    flow.append(Spacer(1, LINE_HEIGHT))
    for line in (data["description"] or "Работы не указаны").splitlines():
        flow.append(paragraph(line or "\u00a0"))
    recommendations = data.get("recommendations", "").strip()
    if recommendations:
        flow.append(paragraph("рекомендации:", bold))
        for line in recommendations.splitlines():
            flow.append(paragraph(line or "\u00a0"))
    if data["items"]:
        for kind, title in (("part", "Запчасти"), ("work", "Работы")):
            items = [item for item in data["items"] if item["kind"] == kind]
            if not items:
                continue
            flow.append(Spacer(1, LINE_HEIGHT / 2))
            flow.append(paragraph(title, bold))
            for item in items:
                quantity = quantity_text(item["quantity_milli"])
                if item["unit_price_kopecks"] is None:
                    details = f"кол-во {quantity}"
                else:
                    unit_price = amount_text(item["unit_price_kopecks"])
                    total = amount_text(item["total_kopecks"])
                    details = f"{quantity} × {unit_price} ₽ = {total} ₽"
                flow.append(paragraph(f"{item['title']} · {details}"))
        if data["total_kopecks"] is not None:
            flow.append(Spacer(1, LINE_HEIGHT / 2))
            caption = item_total_label(data["items"])
            flow.append(paragraph(f"{caption}: {amount_text(data['total_kopecks'])} ₽", bold))
    # Последняя строка остаётся с полосой, которая идёт за текстом.
    flow[-1].keepWithNext = True
    flow.append(Spacer(1, 15.45))
    flow[-1].keepWithNext = True
    flow.append(ServiceSheetBand())

    def header(canvas, doc):
        canvas.saveState()
        draw_foltz_header(canvas)
        if doc.page > 1:
            canvas.setFont("Receipt", 9)
            canvas.drawRightString(
                TEXT_LEFT + BODY_WIDTH, 30, f"{document['number']} · Страница {doc.page}"
            )
        canvas.restoreState()

    stream = BytesIO()
    template = BaseDocTemplate(
        stream,
        pagesize=FOLTZ_PAGE,
        title=f"Сервисный лист {document['number']}",
        author="Фольц Мастер",
    )
    frame = Frame(
        TEXT_LEFT,
        BOTTOM_MARGIN,
        BODY_WIDTH,
        FIRST_BASELINE + BODY_SIZE - BOTTOM_MARGIN,
        topPadding=0,
        bottomPadding=0,
        leftPadding=0,
        rightPadding=0,
    )
    template.addPageTemplates(PageTemplate(id="service_sheet", frames=[frame], onPage=header))
    template.build(flow)
    return stream.getvalue()
