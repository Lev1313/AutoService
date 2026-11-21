"""Размер страницы и фирменная шапка согласованного бланка."""

from base64 import a85encode
from pathlib import Path

FOLTZ_PAGE = (595.32, 841.92)
HEADER_IMAGE = Path(__file__).resolve().parents[1] / "assets" / "service_sheet" / "header.jpg"


def draw_foltz_header(canvas):
    form = "FoltzHeader"
    if not canvas.hasForm(form):
        # Сохраняем исходный JPEG и параметр интерполяции из PDF образца.
        jpeg = a85encode(HEADER_IMAGE.read_bytes(), adobe=True, wrapcol=80)[2:].decode("ascii")
        canvas.beginForm(form, -0.01, -0.01, 1.01, 1.01)
        canvas.addLiteral(
            f"BI /W 848 /H 157 /BPC 8 /CS /RGB /I true /F [/A85 /DCT]\nID\n{jpeg}\nEI"
        )
        canvas.endForm()
    canvas.saveState()
    canvas.translate(85.45, 696.12)
    canvas.scale(481.55, 89.15)
    canvas.doForm(form)
    canvas.restoreState()
