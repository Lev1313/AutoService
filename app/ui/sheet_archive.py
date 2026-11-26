"""Чтение прежних бланков из выбранного архивного PDF."""

from base64 import b64encode
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageChops
from PySide6.QtCore import QBuffer, QIODevice, QSize
from PySide6.QtPdf import QPdfDocument

from app.services.foltz_pdf import FOLTZ_PAGE
from app.services.service_sheet_pdf import (
    BODY_SIZE,
    FIRST_GAP,
    HEADER_BOTTOM,
    LINE_HEIGHT,
    TEXT_LEFT,
)


@dataclass
class PrintedLine:
    page: int
    text: str
    x: float
    top: float
    bottom: float


def png_data(image):
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(output, "PNG"):
        raise ValueError("Не удалось прочитать оформление архивного бланка.")
    return bytes(output.data())


@lru_cache(maxsize=1)
def standard_header(width, height, depth):
    from app.services.service_sheet_pdf import build_service_sheet

    block = {
        "runs": [{"text": "А", "size": BODY_SIZE, "bold": False}],
        "right": False,
        "keep": False,
        "gap": FIRST_GAP,
    }
    content = build_service_sheet({"number": "", "payload": {"manual_sheet": {"blocks": [block]}}})
    buffer = QBuffer()
    buffer.setData(content)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    pdf = QPdfDocument()
    pdf.load(buffer)
    image = pdf.render(0, QSize(width, height)).copy(0, 0, width, depth)
    pdf.close()
    return image


def archived_blocks(pdf, snapshot, blocks):
    # The early templates differ in VIN case, total alignment and, before 0.5.12,
    # typeface, date, header and bottom band. The selected PDF is authoritative.
    lines = []
    vector_band = False
    for page in range(pdf.pageCount()):
        offset = 0
        for raw in pdf.getAllText(page).text().splitlines(keepends=True):
            text = raw.strip()
            length = len(raw.rstrip("\r\n").encode("utf-16-le")) // 2
            if text:
                rect = pdf.getSelectionAtIndex(page, offset, length).boundingRectangle()
                if text.replace(" ", "") in ("FFOOLLTTZZMMAASSTTEERR", "FOLTZMASTER"):
                    vector_band = True
                elif not (rect.top() > 790 and text.startswith(snapshot["number"])):
                    lines.append(PrintedLine(page, text, rect.left(), rect.top(), rect.bottom()))
            offset += len(raw.encode("utf-16-le")) // 2
    if not lines:
        return blocks, {}
    # Retain the original header, including the original alignment of its contact details.
    depth = FOLTZ_PAGE[1] - HEADER_BOTTOM if vector_band else 160
    scale = 3
    size = QSize(round(scale * FOLTZ_PAGE[0]), round(scale * FOLTZ_PAGE[1]))
    first = pdf.render(0, size)
    header = first.copy(0, 0, first.width(), round(depth * scale))
    artwork = {}
    if not vector_band or header != standard_header(size.width(), size.height(), header.height()):
        artwork = {"header": b64encode(png_data(header)).decode("ascii"), "depth": depth}
    original_text = " ".join(line.text for line in lines)
    vin = snapshot["payload"]["vehicle"].get("vin") or ""
    if vin and vin.lower() in original_text and vin.upper() not in original_text:
        name = blocks[0]["runs"][0]
        name["text"] = name["text"].replace(vin.upper(), vin.lower(), 1)
    if blocks[-1]["right"]:
        blocks[-1]["right"] = lines[-1].x > TEXT_LEFT + 6

    def letters(text):
        return "".join(text.split())

    body = "".join(run["text"] for block in blocks for run in block["runs"])
    if vector_band and letters(original_text) == letters(body):
        return blocks, artwork
    # Some old PDFs print different fields. Recover their actual text and spacing.
    # Each already wrapped line remains independently editable in this fallback.
    restored = []
    for i, line in enumerate(lines):
        gap = FIRST_GAP if not i else 0
        if i and line.page == lines[i - 1].page:
            gap = max(0, round(line.top - lines[i - 1].top - LINE_HEIGHT, 1))
        if not i:
            gap = max(0, line.top - depth - 3)
        heading = line.text in ("рекомендации:", "Работы", "Запчасти")
        total = line.text.startswith("Итого")
        restored.append(
            {
                "gap": min(360, gap),
                "keep": heading or total,
                "right": total and line.x > TEXT_LEFT + 6,
                "runs": [
                    {
                        "text": line.text,
                        "size": BODY_SIZE if vector_band else 14,
                        "bold": heading or total,
                    }
                ],
            }
        )
    if not vector_band:
        artwork["classic"] = True
        last = lines[-1]
        page = first if last.page == 0 else pdf.render(last.page, size)
        # Crop only the stock band below the last body line. Exclude the page number.
        y = round((last.bottom + 2) * scale)
        h = min(page.height(), round(797 * scale)) - y
        if h > 0:
            region = page.copy(round(TEXT_LEFT * scale), y, round(485 * scale), h)
            with Image.open(BytesIO(png_data(region))) as im:
                white = Image.new("RGB", im.size, "white")
                white.paste(im, mask=im.getchannel("A") if "A" in im.getbands() else None)
                ink = ImageChops.difference(white, Image.new("RGB", im.size, "white")).getbbox()
                if ink:
                    band = region.copy(0, ink[1], region.width(), ink[3] - ink[1])
                    artwork["band"] = b64encode(png_data(band)).decode("ascii")
                    artwork["band_height"] = band.height() / scale
    return restored, artwork
