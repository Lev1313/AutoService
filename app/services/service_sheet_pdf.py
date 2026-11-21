"""Сервисный лист по оригинальному бланку владельца, без зависимости от Word."""

import os
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer

from app.services.receipt_pdf import human_date, register_fonts
from app.services.records import mileage_text

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "service_sheet"


def build_service_sheet(document: dict) -> bytes:
    register_fonts()
    if "ServiceSheet" not in pdfmetrics.getRegisteredFontNames():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name, filename in (("ServiceSheet", "comici.ttf"), ("ServiceSheetBold", "comicz.ttf")):
            path = fonts / filename
            if not path.is_file():
                raise RuntimeError("Не найден шрифт Comic Sans MS из шаблона сервисного листа.")
            pdfmetrics.registerFont(TTFont(name, str(path)))
    data = document["payload"]
    repair, vehicle = data["repair"], data["vehicle"]
    body = ParagraphStyle(
        "SheetBody",
        fontName="ServiceSheet",
        fontSize=14,
        leading=19.5,
        leftIndent=-6,
        rightIndent=-6,
    )
    bold = ParagraphStyle("SheetBold", parent=body, fontName="ServiceSheetBold", keepWithNext=True)
    title = ParagraphStyle(
        "SheetVehicle", fontName="Receipt", fontSize=14, leading=19.5, alignment=TA_CENTER
    )

    def paragraph(text, style=body):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)

    name = " ".join(
        str(vehicle.get(key) or "") for key in ("plate", "vin", "make", "model")
    ).strip()
    flow = [paragraph(name or "Автомобиль не указан", title), Spacer(1, 26)]
    flow.append(paragraph(human_date(repair["visit_date"])))
    mileage = mileage_text(repair)
    has_mileage = repair.get("km") is not None or repair.get("carried_km") is not None
    flow.append(paragraph(f"{mileage} км" if has_mileage else "Пробег не указан"))
    if repair.get("carried_km") is not None:
        flow.append(paragraph("Последний известный пробег; новый замер не указан."))
    for line in (data["description"] or "Работы не указаны").splitlines():
        flow.append(paragraph(line or "\u00a0"))
    recommendations = data.get("recommendations", "").strip()
    if recommendations:
        flow.append(paragraph("рекомендации:", bold))
        for line in recommendations.splitlines():
            flow.append(paragraph(line or "\u00a0"))
    # Полоса идёт за текстом, как в оригинале, а не привязана к низу страницы.
    # Последний абзац остаётся с полосой, чтобы не возникала пустая последняя страница.
    flow[-1].keepWithNext = True
    flow.append(Spacer(1, 16))
    flow[-1].keepWithNext = True
    flow.append(Image(str(ASSETS / "footer.png"), width=485, height=28))

    def header(canvas, doc):
        canvas.saveState()
        canvas.drawImage(str(ASSETS / "header.png"), 85, A4[1] - 160, width=462, height=75.5)
        if doc.page > 1:
            canvas.setFont("Receipt", 9)
            canvas.drawRightString(552, 30, f"{document['number']} · Страница {doc.page}")
        canvas.restoreState()

    stream = BytesIO()
    template = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=67,
        rightMargin=A4[0] - 552,
        topMargin=224,
        bottomMargin=45,
        title=f"Сервисный лист {document['number']}",
        author="Фольц Мастер",
    )
    template.build(flow, onFirstPage=header, onLaterPages=header)
    return stream.getvalue()
