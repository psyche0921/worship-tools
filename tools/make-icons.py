# -*- coding: utf-8 -*-
"""사이트 아이콘 만들기: 주황 둥근 사각 박스 + 흰 BBC 글자 (drdr.ppsoft.kr 과 같은 톤).

    pip install pillow fonttools
    python tools/make-icons.py

web/ 의 icon.svg, icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png,
favicon.ico 를 다시 만든다. NanumGothic-Bold(OFL)의 B/C 글리프 외곽선을 그대로 써서
SVG 와 PNG 가 같은 모양이 되게 한다.
"""
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "web"
FONT = REPO / "fonts" / "NanumGothic-Bold.ttf"

BG = "#d4652a"
FG = "#ffffff"
TEXT = "BBC"
TRACK = 0.02      # 글자 사이 (em)
BOX = 64.0        # SVG viewBox
RADIUS = 0.25     # 둥근 정도 (drdr 과 같은 8/32)
WIDTH_ANY = 0.70  # 둥근 사각 안에서 글자가 차지하는 너비
WIDTH_MASK = 0.56 # maskable: 가운데 80% 안에 들어가야 해서 더 작게
STROKE = 0.022    # 작은 크기에서도 또렷하게 글자를 조금 두껍게 (em)


def glyph_names(font):
    cmap = font.getBestCmap()
    return [cmap[ord(c)] for c in TEXT]


def text_path(font, scale, dx, dy):
    """BBC 를 하나의 SVG path 로. (y 는 아래로 자라는 화면 좌표)"""
    gs = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    pen = SVGPathPen(gs, ntos=lambda v: f"{v:.2f}")
    x = 0.0
    for name in glyph_names(font):
        t = Transform(scale, 0, 0, -scale, dx + x * scale, dy)
        gs[name].draw(TransformPen(pen, t))
        x += gs[name].width + TRACK * upem
    return pen.getCommands()


def layout(font, width_ratio):
    """글자를 박스 가운데에 놓는 scale/dx/dy 를 구한다."""
    gs = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    names = glyph_names(font)
    advance = sum(gs[n].width for n in names) + TRACK * upem * (len(names) - 1)
    # 실제 잉크 범위로 가운데를 맞춘다
    from fontTools.pens.boundsPen import BoundsPen
    x0, y0, x1, y1 = None, None, None, None
    x = 0.0
    for n in names:
        bp = BoundsPen(gs)
        gs[n].draw(bp)
        if bp.bounds:
            a, b, c, d = bp.bounds
            a, c = a + x, c + x
            x0 = a if x0 is None else min(x0, a)
            x1 = c if x1 is None else max(x1, c)
            y0 = b if y0 is None else min(y0, b)
            y1 = d if y1 is None else max(y1, d)
        x += gs[n].width + TRACK * upem
    ink_w, ink_h = x1 - x0, y1 - y0
    scale = BOX * width_ratio / ink_w
    dx = (BOX - ink_w * scale) / 2 - x0 * scale
    dy = (BOX + ink_h * scale) / 2 + y0 * scale   # baseline (y 아래로)
    return scale, dx, dy


def svg(width_ratio, rounded=True):
    font = TTFont(FONT)
    scale, dx, dy = layout(font, width_ratio)
    d = text_path(font, scale, dx, dy)
    rx = f' rx="{BOX * RADIUS:.0f}"' if rounded else ""
    sw = STROKE * font["head"].unitsPerEm * scale
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            f'<rect width="64" height="64"{rx} fill="{BG}"/>'
            f'<path fill="{FG}" stroke="{FG}" stroke-width="{sw:.2f}" '
            f'stroke-linejoin="round" d="{d}"/></svg>\n')


def png(size, rounded=True, width_ratio=WIDTH_ANY):
    ss = 8  # 안티앨리어싱용 확대
    big = size * ss
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    if rounded:
        dr.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * RADIUS, fill=BG)
    else:
        dr.rectangle([0, 0, big - 1, big - 1], fill=BG)
    # SVG 와 같은 자리에 같은 크기로 글자를 그린다
    font = TTFont(FONT)
    upem = font["head"].unitsPerEm
    scale, dx, dy = layout(font, width_ratio)
    px = big / BOX
    # ImageFont 의 size 는 em 한 칸의 픽셀 수. scale 은 폰트 단위 → SVG 단위라서 em 과 px 를 곱한다.
    f = ImageFont.truetype(str(FONT), max(1, round(scale * upem * px)))
    gs = font.getGlyphSet()
    x = dx * px
    for ch, name in zip(TEXT, glyph_names(font)):
        dr.text((x, dy * px), ch, font=f, fill=FG, anchor="ls",
                stroke_width=round(STROKE * upem * scale * px / 2), stroke_fill=FG)
        x += (gs[name].width + TRACK * upem) * scale * px
    return im.resize((size, size), Image.LANCZOS)


def main():
    (WEB / "icon.svg").write_text(svg(WIDTH_ANY), encoding="utf-8")
    png(192).save(WEB / "icon-192.png")
    png(512).save(WEB / "icon-512.png")
    png(512, rounded=False, width_ratio=WIDTH_MASK).save(WEB / "icon-maskable-512.png")
    png(180, rounded=False).save(WEB / "apple-touch-icon.png")
    ico = png(256)
    ico.save(WEB / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    for p in sorted(WEB.glob("*icon*")) + [WEB / "favicon.ico"]:
        print(p.name, p.stat().st_size)


main()
