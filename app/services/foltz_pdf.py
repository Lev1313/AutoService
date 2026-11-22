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

    # Координаты полос в исходном JPEG, в пикселях от верхнего левого угла.
    # Рисунок слева сохраняет своё место. Правый край надписей — x=840.
    def draw_region(left, top, right, bottom, shift=0):
        canvas.saveState()
        canvas.translate(shift / 848, 0)
        clip = canvas.beginPath()
        clip.rect(left / 848, 1 - bottom / 157, (right - left) / 848, (bottom - top) / 157)
        canvas.clipPath(clip, stroke=0, fill=0)
        canvas.doForm(form)
        canvas.restoreState()

    draw_region(0, 0, 630, 157)
    for top, bottom, end in (
        (0, 49, 833),
        (49, 72, 825),
        (72, 94, 811),
        (94, 113, 812),
        (113, 133, 835),
        (133, 157, 835),
    ):
        draw_region(630, top, 835 if top >= 113 else 848, bottom, 840 - end)
    canvas.restoreState()
