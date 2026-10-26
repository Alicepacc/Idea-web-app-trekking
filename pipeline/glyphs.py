"""Glyphs for the map labels: Barlow Condensed as signed distance fields in MapLibre's PBF format.

MapLibre draws map text from pre-rendered glyph sheets. The claude.ai page cannot
load them from other sites, so they are built here from the font files (SIL Open
Font License, data/raw/fonts) and shipped with the page in one pack.

Each glyph is rendered 8 times larger than MapLibre's 24 px em, its exact
distance field is computed at that size and sampled down: the same values as
MapLibre's own generator (radius 8 px, edge at 192/255, 3 px border).

Writes web/data/glyphs/glyphs.webp (all ranges end to end) and web/data/glyphs/index.json:
  {"<font name>/<first>-<last>": [offset, length]}
"""
import json
import math
import urllib.request

import freetype
import numpy as np
from scipy.ndimage import distance_transform_edt

from common import RAW, WEB_DATA

FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/barlowcondensed/{}"
FONTS = {
    "Barlow Condensed Medium": "BarlowCondensed-Medium.ttf",
    "Barlow Condensed SemiBold": "BarlowCondensed-SemiBold.ttf",
    "Barlow Condensed Medium Italic": "BarlowCondensed-MediumItalic.ttf",
}
# Latin, Latin-1 (à è ö ü ß), Latin Extended-A (ł ő ž), punctuation (– ’ “ ”)
RANGES = [(0, 255), (256, 511), (8192, 8447)]
EM, BORDER, RADIUS, CUTOFF, SCALE = 24, 3, 8, 0.25, 8
OUT = WEB_DATA / "glyphs"


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def field(num, value):
    """A protobuf field: bytes are length-delimited, integers varints."""
    if isinstance(value, bytes):
        return varint(num << 3 | 2) + varint(len(value)) + value
    return varint(num << 3) + varint(value)


def zigzag(n):
    return (n << 1) ^ (n >> 31)


def font_file(name):
    path = RAW / "fonts" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(FONT_URL.format(name), timeout=120) as r:
            path.write_bytes(r.read())
    return path


class Font:
    def __init__(self, path):
        self.face = freetype.Face(str(path))
        self.face.set_pixel_sizes(0, EM * SCALE)
        # MapLibre places a line's glyphs 17 px above the anchor and was tuned on fonts whose
        # ascender is about 17 px plus half the cap height: aim capitals at the label centre.
        self.face.load_char("H", freetype.FT_LOAD_NO_HINTING)
        cap = self.face.glyph.metrics.horiBearingY / 64 / SCALE
        self.ascender = round(17 + cap / 2)

    def glyph(self, cp):
        if not self.face.get_char_index(cp):
            return None
        self.face.load_char(chr(cp), freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING)
        g = self.face.glyph
        advance = round(g.advance.x / 64 / SCALE)
        bm = g.bitmap
        if bm.width == 0 or bm.rows == 0:
            return field(1, cp) + field(3, 0) + field(4, 0) + field(5, 0) + field(6, 0) + field(7, advance)
        ink = np.array(bm.buffer, np.uint8).reshape(bm.rows, bm.pitch)[:, :bm.width] >= 128
        # the glyph box on the 24 px grid
        left = math.floor(g.bitmap_left / SCALE)
        top = math.ceil(g.bitmap_top / SCALE)
        w = math.ceil((g.bitmap_left + bm.width) / SCALE) - left
        h = top - math.floor((g.bitmap_top - bm.rows) / SCALE)
        inside = np.zeros(((h + 2 * BORDER) * SCALE, (w + 2 * BORDER) * SCALE), bool)
        ox = g.bitmap_left - (left - BORDER) * SCALE
        oy = (top + BORDER) * SCALE - g.bitmap_top
        inside[oy:oy + bm.rows, ox:ox + bm.width] = ink
        # signed distance to the outline, positive outside, sampled at the 24 px pixel centres
        d = np.where(inside, 0.5 - distance_transform_edt(inside), distance_transform_edt(~inside) - 0.5)
        c = np.arange(SCALE // 2, inside.shape[0], SCALE), np.arange(SCALE // 2, inside.shape[1], SCALE)
        d = d[np.ix_(*c)] / SCALE
        sdf = np.clip(np.round(255 - 255 * (d / RADIUS + CUTOFF)), 0, 255).astype(np.uint8)
        return (field(1, cp) + field(2, sdf.tobytes()) + field(3, w) + field(4, h) + field(5, zigzag(left))
                + field(6, zigzag(top - self.ascender)) + field(7, advance))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pack, index = bytearray(), {}
    for name, file in FONTS.items():
        font = Font(font_file(file))
        for a, b in RANGES:
            glyphs = [g for g in (font.glyph(cp) for cp in range(a, b + 1)) if g]
            stack = field(1, name.encode()) + field(2, f"{a}-{b}".encode()) + b"".join(field(3, g) for g in glyphs)
            pbf = field(1, stack)
            index[f"{name}/{a}-{b}"] = [len(pack), len(pbf)]
            pack += pbf
        print(f"{name}: ascender {font.ascender} px")
    (OUT / "glyphs.webp").write_bytes(pack)
    (OUT / "index.json").write_text(json.dumps(index))
    print(f"glyphs.webp: {len(index)} ranges, {len(pack) / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
