"""Generate the application icon (icon.ico) without external assets.

Run: python tools/make_icon.py
Requires: pillow
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "icon.ico")
PNG = os.path.join(os.path.dirname(HERE), "icon.png")

S = 512
BG_TOP = (76, 141, 255)
BG_BOT = (34, 84, 190)


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Vertical gradient inside a rounded square.
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line(
            [(0, y), (S, y)],
            fill=tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3)) + (255,),
        )
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=110, fill=255)
    img.paste(grad, (0, 0), mask)

    # Speech bubble.
    d.rounded_rectangle([96, 122, 416, 330], radius=58, fill=(255, 255, 255, 245))
    d.polygon([(168, 318), (238, 318), (176, 396)], fill=(255, 255, 255, 245))

    # "A" on the left, arrow, "EN" on the right — translation at a glance.
    d.text((150, 176), "A", fill=(40, 90, 200, 255), font_size=118, anchor="lm")
    d.line([(214, 226), (286, 226)], fill=(40, 90, 200, 255), width=14)
    d.polygon([(286, 208), (318, 226), (286, 244)], fill=(40, 90, 200, 255))
    d.text((330, 226), "EN", fill=(40, 90, 200, 255), font_size=86, anchor="lm")

    # Sound waves below the bubble.
    for i, (x, h) in enumerate([(150, 40), (196, 74), (242, 104), (288, 74), (334, 40)]):
        d.rounded_rectangle(
            [x - 11, 430 - h // 2, x + 11, 430 + h // 2],
            radius=11, fill=(255, 255, 255, 210),
        )
    return img


def main() -> None:
    img = build()
    img.save(PNG)
    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.1f} KB)")
    print(f"wrote {PNG}")


if __name__ == "__main__":
    main()
