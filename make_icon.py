"""Builds Murmur's app icons from assets/logo_source.png (the 3D bear artwork), plus the menu bar bear.
Crops the rounded tile, makes the corners transparent, and places it on Apple's 1024 px icon grid.
Run: uv run --no-project --with pillow --with numpy python make_icon.py"""
import io, os, struct

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TILE_BOX = (16, 16, 1236, 1212)  # inside the artwork's rounded tile, clear of its dark rim and black border
CANVAS, TILE, RADIUS = 1024, 824, 185  # macOS icon grid: 824 px tile, ~185 px corners, centered


def rounded_mask(size, radius, scale=4):
    """Anti-aliased rounded-square alpha mask."""
    n = size * scale
    y, x = np.mgrid[0:n, 0:n] + 0.5
    r = radius * scale
    qx, qy = np.maximum(np.abs(x - n / 2) - (n / 2 - r), 0), np.maximum(np.abs(y - n / 2) - (n / 2 - r), 0)
    inside = (np.hypot(qx, qy) <= r).astype("uint8") * 255
    return Image.fromarray(inside).resize((size, size), Image.LANCZOS)


def build():
    art = Image.open(os.path.join(HERE, "assets", "logo_source.png")).convert("RGB").crop(TILE_BOX)
    tile = art.resize((TILE, TILE), Image.LANCZOS).convert("RGBA")
    tile.putalpha(rounded_mask(TILE, RADIUS))
    icon = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    icon.paste(tile, ((CANVAS - TILE) // 2, (CANVAS - TILE) // 2), tile)
    return icon


def menubar_bear(px):
    """Monochrome bear head holding a microphone, for the menu bar. Only the alpha channel matters: macOS draws
    template images black or white to match the menu bar. Drawn on a 64-unit grid, 4x supersampled."""
    s = 4 * px
    y, x = (np.mgrid[0:s, 0:s] + 0.5) * 64 / s
    circle = lambda cx, cy, r: np.hypot(x - cx, y - cy) <= r
    ellipse = lambda cx, cy, rx, ry: ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1

    def segment(ax, ay, bx, by, r):
        dx, dy = bx - ax, by - ay
        t = np.clip(((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy), 0, 1)
        return np.hypot(x - ax - t * dx, y - ay - t * dy) <= r

    bear = circle(14, 15, 9) | circle(46, 15, 9) | ellipse(30, 35, 23, 21)            # ears + head
    bear &= ~(circle(21, 32, 3.4) | circle(39, 32, 3.4))                                # eyes
    bear &= ~ellipse(30, 44, 9.5, 7)                                                    # snout
    bear |= ellipse(30, 41.5, 4, 2.8)                                                   # nose
    mic = circle(52, 47, 7.5) | segment(52, 47, 43, 61, 2.6)                            # mic head + handle
    bear &= ~(circle(52, 47, 10.5) | segment(52, 47, 43, 61, 5))                        # gap around the mic
    shape = bear | mic
    alpha = (shape.reshape(px, 4, px, 4).mean(axis=(1, 3)) * 255).astype("uint8")
    return Image.fromarray(np.dstack([np.zeros((px, px, 3), "uint8"), alpha]), "RGBA")


if __name__ == "__main__":
    out = os.path.join(HERE, "assets")
    icon = build()
    icon.save(os.path.join(out, "icon_1024.png"))
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(out, "icon_256.png"))
    buf = io.BytesIO()  # .ico holding one 256px PNG (Windows Vista+)
    icon.resize((256, 256), Image.LANCZOS).save(buf, "PNG")
    png = buf.getvalue()
    with open(os.path.join(out, "icon.ico"), "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22) + png)
    menubar_bear(36).save(os.path.join(out, "menubar_bear.png"))  # 18 pt at @2x
    print("Wrote", out)
