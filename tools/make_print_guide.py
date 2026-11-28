"""Build the versioned A4 guide from the text used by the in-app help."""

import argparse
import os
import sys
from html import escape
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

ROOT = Path(__file__).resolve().parents[1]
if __package__ is None:
    sys.path.insert(0, str(ROOT))

from app.guide_text import guide_blocks  # noqa: E402
from app.version import VERSION  # noqa: E402

INK = HexColor("#243247")
BLUE = HexColor("#174b9a")
MUTED = HexColor("#56647a")
RULE = HexColor("#d8e2ee")
MARGIN = 18 * mm


def build_guide(output_dir):
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    pdfmetrics.registerFont(TTFont("Guide", str(font_dir / "arial.ttf")))
    pdfmetrics.registerFont(TTFont("Guide-Bold", str(font_dir / "arialbd.ttf")))
    pdfmetrics.registerFontFamily("Guide", normal="Guide", bold="Guide-Bold")
    styles = {
        "chapter": ParagraphStyle(
            "Chapter",
            fontName="Guide-Bold",
            fontSize=25,
            leading=29,
            textColor=INK,
            spaceAfter=13,
            keepWithNext=True,
        ),
        "heading": ParagraphStyle(
            "Heading",
            fontName="Guide-Bold",
            fontSize=10.5,
            leading=14,
            textColor=BLUE,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body",
            fontName="Guide",
            fontSize=11,
            leading=15.5,
            textColor=INK,
            spaceAfter=7,
        ),
    }
    guide = (ROOT / "docs" / "OWNER-GUIDE.txt").read_text(encoding="utf-8-sig")
    blocks = list(guide_blocks(guide))
    logo = ImageReader(str(ROOT / "app" / "assets" / "autoservice.ico"))
    story = []
    for kind, text in blocks:
        if kind == "title":
            continue
        if kind == "chapter" and story:
            story.append(PageBreak())
        number, separator, rest = text.partition(") ")
        content = (
            f"<b>{number})</b> {escape(rest)}" if separator and number.isdecimal() else escape(text)
        )
        story.append(Paragraph(content, styles[kind]))

    def page_furniture(canvas, document):
        width, height = A4
        canvas.saveState()
        canvas.drawImage(logo, MARGIN, height - 16 * mm, 7 * mm, 7 * mm, mask="auto")
        canvas.setFillColor(INK)
        canvas.setFont("Guide-Bold", 10)
        canvas.drawString(MARGIN + 9 * mm, height - 13.8 * mm, "АвтоСервис")
        canvas.setFont("Guide", 9)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(width - MARGIN, height - 13.8 * mm, "Руководство пользователя")
        canvas.setStrokeColor(RULE)
        canvas.line(MARGIN, height - 19 * mm, width - MARGIN, height - 19 * mm)
        canvas.line(MARGIN, 17 * mm, width - MARGIN, 17 * mm)
        canvas.drawString(MARGIN, 11.5 * mm, f"AutoService {VERSION}")
        canvas.drawRightString(width - MARGIN, 11.5 * mm, str(document.page))
        canvas.restoreState()

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"AutoService-{VERSION}-Руководство-пользователя.pdf"
    document = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=MARGIN - 6,
        rightMargin=MARGIN - 6,
        topMargin=24 * mm,
        bottomMargin=22 * mm,
        title=f"AutoService {VERSION}. Руководство пользователя",
        author="AutoService",
    )
    document.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Каталог готовой PDF-инструкции")
    print(build_guide(parser.parse_args().output))
