"""Tiny SVG toolkit: text baked to paths (so GitHub renders the same everywhere) + shared theme."""

from __future__ import annotations

import functools
from html import escape

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

INTER = "/usr/share/fonts/opentype/inter/"
FONTS = {
    "display-black": INTER + "InterDisplay-Black.otf",
    "display-bold": INTER + "InterDisplay-ExtraBold.otf",
    "display-semi": INTER + "InterDisplay-SemiBold.otf",
    "display": INTER + "InterDisplay-Regular.otf",
    "bold": INTER + "Inter-Bold.otf",
    "semi": INTER + "Inter-SemiBold.otf",
    "medium": INTER + "InterDisplay-Medium.otf",
    "regular": INTER + "Inter-Regular.otf",
    "light": INTER + "Inter-Light.otf",
    "mono": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
}

# Theme: same surface as the Fly-in assets, so both repos read as one family.
BG = "#0E1111"
CARD = "#141A1A"
CARD2 = "#182020"
LINE = "#232C2C"
GRID = "#1E2626"
TXT = "#EEF2FA"
TXT2 = "#A9B3BA"
MUTED = "#6E7A80"
# Categorical, dark steps from the dataviz reference palette (validated on #0E1111).
BLUE = "#3987e5"
ORANGE = "#d95926"
AQUA = "#199e70"
YELLOW = "#c98500"
MAGENTA = "#d55181"
VIOLET = "#9085e9"
RED = "#e66767"
CLAUDE = "#d97757"


@functools.lru_cache(maxsize=None)
def _font(key: str):
    path = FONTS[key]
    tt = TTFont(path)
    with open(path, "rb") as fh:
        blob = hb.Blob(fh.read())
    face = hb.Face(blob)
    hbfont = hb.Font(face)
    upm = face.upem
    return tt, tt.getGlyphSet(), hbfont, upm, tt.getGlyphOrder()


def _shape(text: str, key: str, tracking: float = 0.0):
    tt, gs, hbfont, upm, order = _font(key)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {"kern": True, "liga": True})
    out, x = [], 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        out.append((order[info.codepoint], x + pos.x_offset, pos.y_offset))
        x += pos.x_advance + tracking * upm
    return out, x, upm


def width(text: str, size: float, font: str = "regular", tracking: float = 0.0) -> float:
    _, adv, upm = _shape(text, font, tracking)
    return adv * size / upm


_REG: dict[tuple[str, str], tuple[str, str]] = {}


def _glyph_id(font: str, name: str) -> str | None:
    key = (font, name)
    if key not in _REG:
        gs = _font(font)[1]
        pen = SVGPathPen(gs, ntos=lambda v: f"{v:.0f}")
        gs[name].draw(pen)
        _REG[key] = (f"g{len(_REG)}", pen.getCommands())
    gid, d = _REG[key]
    return gid if d else None


def text(
    s: str,
    x: float,
    y: float,
    size: float,
    fill: str = TXT,
    font: str = "regular",
    anchor: str = "start",
    tracking: float = 0.0,
    opacity: float | None = None,
    extra: str = "",
) -> str:
    """Text baked to glyph outlines: each glyph is defined once per file and placed with <use>."""
    glyphs, adv, upm = _shape(s, font, tracking)
    k = size / upm
    w = adv * k
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    uses = []
    for name, gx, gy in glyphs:
        gid = _glyph_id(font, name)
        if gid:
            uses.append(f'<use href="#{gid}" x="{gx:.0f}"' + (f' y="{-gy:.0f}"' if gy else "") + "/>")
    if not uses:
        return ""
    op = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<g fill="{fill}"{op}{extra} transform="translate({x:.1f} {y:.1f}) scale({k:.5f} {-k:.5f})">{"".join(uses)}</g>'


def svg(w: int, h: int, body: str, label: str, display_w: int | None = None, style: str = "") -> str:
    dw = display_w or w
    dh = round(h * dw / w)
    st = f"<style>{style}</style>" if style else ""
    defs = "".join(f'<path id="{gid}" d="{d}"/>' for gid, d in _REG.values() if d)
    _REG.clear()
    st += f"<defs>{defs}</defs>"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{dw}" height="{dh}" '
        f'role="img" aria-label="{escape(label)}"><title>{escape(label)}</title>{st}{body}</svg>\n'
    )


def card(w: int, h: int, rx: int = 22) -> str:
    return f'<rect width="{w}" height="{h}" rx="{rx}" fill="{BG}"/>'


class GlyphSet:
    """Glyphs defined once in <defs>, placed with <use>: for text that repeats (counters, axes)."""

    def __init__(self, prefix: str, size: float, font: str = "regular"):
        self.prefix, self.size, self.font = prefix, size, font
        self.defs: dict[str, str] = {}

    def _gid(self, name: str) -> str:
        if name not in self.defs:
            gs = _font(self.font)[1]
            upm = _font(self.font)[3]
            k = self.size / upm
            pen = SVGPathPen(gs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
            gs[name].draw(TransformPen(pen, (k, 0, 0, -k, 0, 0)))
            self.defs[name] = pen.getCommands()
        return f"{self.prefix}{list(self.defs).index(name)}"

    def text(self, s: str, x: float, y: float, fill: str, anchor: str = "start") -> str:
        glyphs, adv, upm = _shape(s, self.font)
        k = self.size / upm
        if anchor == "middle":
            x -= adv * k / 2
        elif anchor == "end":
            x -= adv * k
        out = []
        for name, gx, _ in glyphs:
            gid = self._gid(name)
            if self.defs[name]:
                out.append(f'<use href="#{gid}" x="{x + gx * k:.1f}" y="{y:.1f}"/>')
        return f'<g fill="{fill}">{"".join(out)}</g>'

    def render_defs(self) -> str:
        return "".join(f'<path id="{self.prefix}{i}" d="{d}"/>' for i, d in enumerate(self.defs.values()) if d)
