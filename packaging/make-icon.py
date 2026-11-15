"""Render packaging/notch.ico from assets/Cuvyro_Logo.png.

Windows Explorer, the taskbar and Inno Setup need several sizes in one .ico,
so each size is downscaled separately from the master artwork rather than
letting Windows rescale a single bitmap.

Usage:  python packaging/make-icon.py
"""

import os

from PIL import Image

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
PAD = 0.04  # keep the mark off the icon edge so it reads at 16px

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(REPO_ROOT, "assets", "Cuvyro_Logo.png")
OUTPUT = os.path.join(HERE, "notch.ico")


def load_square(source: str) -> Image.Image:
    img = Image.open(source).convert("RGBA")
    if img.width != img.height:
        side = min(img.width, img.height)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
    inset = int(img.width * PAD)
    inner = img.width - 2 * inset
    canvas = Image.new("RGBA", (img.width, img.width), (0, 0, 0, 0))
    canvas.paste(img.resize((inner, inner), Image.LANCZOS), (inset, inset))
    return canvas


def main() -> None:
    master = load_square(SOURCE)
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    # Pillow writes all requested sizes when given the largest frame + sizes.
    frames[-1].save(OUTPUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
