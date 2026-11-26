"""Сервисный лист по оригинальному бланку владельца, без зависимости от Word."""

import json
import os
import re
from base64 import b64decode
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
from math import isfinite
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Flowable, Frame, PageTemplate, Paragraph, Spacer
from reportlab.platypus import Image as SheetImage

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


def register_sheet_fonts():
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


HEADER_BOTTOM = 696.12
FIRST_GAP = HEADER_BOTTOM - FIRST_BASELINE - BODY_SIZE


def sheet_blocks(data):
    """Editable paragraphs, including the physical space before each one, in points."""
    repair, vehicle = data["repair"], data["vehicle"]
    blocks = []

    def add(text, gap=0, *, bold=False, right=False):
        blocks.append(
            {
                "gap": gap,
                "right": right,
                "keep": bold,
                "runs": [{"text": text, "size": BODY_SIZE, "bold": bold}],
            }
        )

    name = " ".join(
        filter(
            None, (vehicle["plate"], vehicle["vin"].upper(), vehicle["model"] or vehicle["make"])
        )
    )
    visit = repair["visit_date"]
    mileage = "Пробег не указан"
    if repair.get("km") is not None:
        marker = " (?)" if not repair.get("verified", True) else ""
        mileage = f"{repair['km']}км{marker}"
    if repair.get("carried_km") is not None:
        mileage = f"{mileage_text(repair)} км"
    add(name or "Автомобиль не указан", FIRST_GAP)
    add(date.fromisoformat(visit).strftime("%d.%m.%y") if visit else "Не указана", LINE_HEIGHT)
    add(mileage)
    if repair.get("carried_km") is not None:
        add("Последний известный пробег. Новый замер не указан.")
    for n, line in enumerate((data["description"] or "Работы не указаны").splitlines()):
        add(line, 0 if n else LINE_HEIGHT)
    recommendations = data.get("recommendations", "").strip()
    if recommendations:
        add("рекомендации:", bold=True)
        for line in recommendations.splitlines():
            add(line)
    if data["items"]:
        for kind, title in (("part", "Запчасти"), ("work", "Работы")):
            items = [item for item in data["items"] if item["kind"] == kind]
            if not items:
                continue
            add(title, LINE_HEIGHT / 2, bold=True)
            for item in items:
                quantity = quantity_text(item["quantity_milli"])
                if item["unit_price_kopecks"] is None:
                    details = f"кол-во {quantity}"
                else:
                    unit_price = amount_text(item["unit_price_kopecks"])
                    total = amount_text(item["total_kopecks"])
                    details = f"{quantity} × {unit_price} ₽ = {total} ₽"
                add(f"{item['title']} · {details}")
        if data["total_kopecks"] is not None:
            caption = item_total_label(data["items"])
            add(
                f"{caption}: {amount_text(data['total_kopecks'])} ₽",
                LINE_HEIGHT / 2,
                bold=True,
                right=True,
            )
    return blocks


def sheet_signature(blocks):
    return sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass
class SheetLine:
    page: int
    block: int
    top: float
    height: float
    text: str
    edges: list[float]


class SheetParagraph(Paragraph):
    """Expose the same wrapped lines used to draw the PDF for editing hit tests."""

    def draw(self):
        super().draw()
        trace = self.style.trace
        if trace is None:
            return
        x, top = self.canv.absolutePosition(0, self.height)
        top = FOLTZ_PAGE[1] - top
        for line in self.blPara.lines:
            if self.blPara.kind == 0:
                unused, words = line
                runs = [(" ".join(words), self.blPara.fontName, self.blPara.fontSize)]
            else:
                unused = line.extraSpace
                runs = [(frag.text, frag.fontName, frag.fontSize) for frag in line.words]
            offset = {TA_RIGHT: unused, TA_CENTER: unused / 2}.get(self.style.alignment, 0)
            edges = [x + offset]
            for text, font, size in runs:
                for char in text:
                    edges.append(edges[-1] + pdfmetrics.stringWidth(char, font, size))
            trace.append(
                SheetLine(
                    self.canv.getPageNumber() - 1,
                    self.style.block_index,
                    top,
                    self.style.leading,
                    "".join(r[0] for r in runs),
                    edges,
                )
            )
            top += self.style.leading


def build_service_sheet(document: dict, trace=None) -> bytes:
    register_sheet_fonts()
    data = document["payload"]
    manual = data.get("manual_sheet")
    blocks = manual["blocks"] if manual else sheet_blocks(data)
    if not blocks:
        raise ValueError("Нужен хотя бы один абзац сервисного листа.")
    artwork = manual.get("artwork", {}) if manual else {}
    classic = artwork.get("classic", False)
    if classic and "SheetItalic" not in pdfmetrics.getRegisteredFontNames():
        folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name, file in (("SheetItalic", "comici.ttf"), ("SheetBoldItalic", "comicz.ttf")):
            pdfmetrics.registerFont(TTFont(name, str(folder / file)))
    flow = []
    for index, block in enumerate(blocks):
        gap = block["gap"]
        if not isfinite(gap) or not 0 <= gap <= 360:
            raise ValueError("Проверьте отступ абзаца.")
        if index and gap:
            flow.append(Spacer(1, gap))
        parts = []
        runs = block["runs"]
        sizes = [r["size"] for r in runs]
        if not sizes or not all(isfinite(v) and 1 <= v <= 144 for v in sizes):
            raise ValueError("Размер шрифта должен быть от 1 до 144 пунктов.")
        size = max(sizes)
        style = ParagraphStyle(
            "SheetBody",
            fontName="ServiceSheet",
            fontSize=size,
            leading=LINE_HEIGHT * size / BODY_SIZE,
            alignment=TA_CENTER
            if classic and index == 0
            else TA_RIGHT
            if block["right"]
            else TA_LEFT,
            keepWithNext=block["keep"],
        )
        style.trace, style.block_index = trace, index
        for run_index, run in enumerate(runs):
            font = "ServiceSheetBold" if run["bold"] else "ServiceSheet"
            if classic:
                font = (
                    "Receipt" if not index else "SheetBoldItalic" if run["bold"] else "SheetItalic"
                )
            text = run["text"]
            if manual:
                # Keep typed indentation and repeated spaces through ReportLab's HTML parser.
                text = re.sub(r" +", lambda m: " " + "\u00a0" * (len(m[0]) - 1), text)
                if run_index == 0 and text.startswith(" "):
                    text = "\u00a0" + text[1:]
                if run_index == len(runs) - 1 and text.endswith(" "):
                    text = text[:-1] + "\u00a0"
            parts.append(f'<font name="{font}" size="{run["size"]}">{escape(text)}</font>')
        markup = "".join(parts) if any(r["text"] for r in runs) else "\u00a0"
        flow.append(SheetParagraph(markup, style))
    # Последняя строка остаётся с полосой, которая идёт за текстом.
    flow[-1].keepWithNext = True
    flow.append(Spacer(1, 15.45))
    flow[-1].keepWithNext = True
    if "band" in artwork:
        flow.append(
            SheetImage(
                BytesIO(b64decode(artwork["band"])), width=BODY_WIDTH, height=artwork["band_height"]
            )
        )
    else:
        flow.append(ServiceSheetBand())

    def header(canvas, doc):
        canvas.saveState()
        if "header" in artwork:
            depth = artwork["depth"]
            canvas.drawImage(
                ImageReader(BytesIO(b64decode(artwork["header"]))),
                0,
                FOLTZ_PAGE[1] - depth,
                FOLTZ_PAGE[0],
                depth,
                mask="auto",
            )
        else:
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
    header_bottom = FOLTZ_PAGE[1] - artwork["depth"] if "depth" in artwork else HEADER_BOTTOM
    for name, gap in (
        ("first", blocks[0]["gap"]),
        ("next", blocks[0]["gap"] if classic else FIRST_GAP),
    ):
        frame = Frame(
            TEXT_LEFT,
            BOTTOM_MARGIN,
            BODY_WIDTH,
            header_bottom - gap - BOTTOM_MARGIN,
            topPadding=0,
            bottomPadding=0,
            leftPadding=0,
            rightPadding=0,
        )
        template.addPageTemplates(
            PageTemplate(id=name, frames=[frame], onPage=header, autoNextPageTemplate="next")
        )
    template.build(flow)
    return stream.getvalue()
