"""Draws assets/icon.ico, the same picture as ui/public/icon.svg. Needs pip install pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 16  # the SVG is 64 units wide; draw at 1024 px and let Pillow shrink it
ACCENT, SHEET, RULE, MASK = "#0F7A68", "#F4F7F6", "#C9D3D0", "#F2C14E"


def box(x0, y0, x1, y1):
    return [x0 * S, y0 * S, x1 * S, y1 * S]


img = Image.new("RGBA", (64 * S, 64 * S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle(box(0, 0, 64, 64), radius=14 * S, fill=ACCENT)
d.rounded_rectangle(box(12, 14, 52, 50), radius=4 * S, fill=SHEET)
for y in (23, 32, 41):
    d.line(box(12, y, 52, y), fill=RULE, width=int(1.5 * S))
d.line(box(25, 14, 25, 50), fill=RULE, width=int(1.5 * S))
d.rounded_rectangle(box(28, 26, 48, 29), radius=1.5 * S, fill=MASK)
d.rounded_rectangle(box(28, 35, 42, 38), radius=1.5 * S, fill=MASK)
d.rounded_rectangle(box(15.5, 17.5, 21.5, 20), radius=1.25 * S, fill=ACCENT)

out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
out.parent.mkdir(exist_ok=True)
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out)
