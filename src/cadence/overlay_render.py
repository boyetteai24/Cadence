"""Drawing for the on-screen overlay.

Everything is drawn with Pillow at 4x size and scaled down, which gives smooth (anti-aliased)
edges, and with real transparency per pixel, so the button can be see-through glass. On
Windows the image is handed straight to a layered window (`overlay._blit`). Sizes are in
"logical" pixels and multiplied by the display scale (125 %, 150 %, ...), so it stays crisp
on high-DPI screens. Pure: no Tk, no Windows calls, so it can be tested anywhere.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

SS = 4  # supersampling factor

# Light blue "glass" theme.
SKY = (125, 211, 252)  # light blue
SKY_DEEP = (56, 189, 248)
SKY_PALE = (224, 242, 254)
WHITE = (255, 255, 255)
PILL_BG = (17, 24, 39, 232)  # dark, slightly see-through
PILL_EDGE = (*SKY, 120)
RED = (248, 113, 113)

PRESETS = ("bottom", "top", "left", "right")  # snap spots, centered on each edge


@dataclass(frozen=True)
class Layout:
    """Overlay geometry at a given display scale (1.0 = 100 %)."""

    scale: float = 1.0

    def px(self, logical: float) -> int:
        return round(logical * self.scale)

    @property
    def width(self) -> int:
        return self.px(300)

    @property
    def height(self) -> int:
        return self.px(44)

    @property
    def edge_margin(self) -> int:  # gap between a snapped button and the screen edge
        return self.px(10)

    @property
    def snap_distance(self) -> int:  # drop this close to a spot and it snaps there
        return self.px(140)

    @property
    def bar(self) -> tuple[int, int]:  # idle bar
        return self.px(46), self.px(8)

    @property
    def button(self) -> tuple[int, int]:  # mic button shown on hover
        return self.px(64), self.px(30)

    def content_x(self, anchor: str | None) -> int:
        """Where the button sits inside the window. Against the left or right edge it hugs
        that side, so the button is right at the edge and the wider pill extends inwards."""
        half = self.button[0] // 2 + self.px(6)
        if anchor == "left":
            return half
        if anchor == "right":
            return self.width - half
        return self.width // 2

    def preset_positions(self, work: tuple[int, int, int, int]) -> dict[str, tuple[int, int]]:
        """Window position for each snap spot, given a monitor's work area (left, top, right,
        bottom; the taskbar excluded). Every spot is centered along its edge."""
        left, top, right, bottom = work
        mid_x = (left + right) // 2 - self.width // 2
        mid_y = (top + bottom) // 2 - self.height // 2
        m = self.edge_margin
        return {
            "bottom": (mid_x, bottom - self.height - m),
            "top": (mid_x, top + m),
            "left": (left + m, mid_y),
            "right": (right - self.width - m, mid_y),
        }

    def button_center(self, pos: tuple[int, int], anchor: str | None) -> tuple[int, int]:
        return pos[0] + self.content_x(anchor), pos[1] + self.height // 2

    def nearest_preset(self, center: tuple[int, int],
                       work: tuple[int, int, int, int]) -> str | None:
        """The snap spot whose button would be closest to `center`, if it's close enough."""
        best, best_d = None, float(self.snap_distance)
        for name, pos in self.preset_positions(work).items():
            bx, by = self.button_center(pos, name)
            d = ((bx - center[0]) ** 2 + (by - center[1]) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = name, d
        return best

    def is_hovering(self, cursor: tuple[int, int], window: tuple[int, int],
                    anchor: str | None) -> bool:
        """Is the cursor over the bar / button (with some slack, so it's easy to hit)?"""
        cx, cy = self.button_center(window, anchor)
        return (abs(cursor[0] - cx) <= self.button[0] // 2 + self.px(8)
                and abs(cursor[1] - cy) <= self.height // 2 + self.px(4))

    def on_button(self, cursor: tuple[int, int], window: tuple[int, int],
                  anchor: str | None) -> bool:
        """Is the cursor on the drawn mic button itself?"""
        cx, cy = self.button_center(window, anchor)
        w, h = self.button
        return abs(cursor[0] - cx) <= w // 2 and abs(cursor[1] - cy) <= h // 2


# --- fonts -------------------------------------------------------------------------------

_FONT_FILES = {
    False: ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"),
    True: ("seguisb.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"),
}


@functools.lru_cache(maxsize=16)
def font(size: int, bold: bool = False):
    for name in _FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


# --- drawing helpers (all coordinates already multiplied by SS) ----------------------------

def _rgba(color, alpha: int = 255) -> tuple[int, int, int, int]:
    if isinstance(color, str):
        color = ImageColor.getrgb(color)
    return (*color[:3], color[3] if len(color) == 4 else alpha)


def _capsule(draw, cx: float, cy: float, w: float, h: float, fill, outline=None,
             width: int = 0) -> None:
    draw.rounded_rectangle((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), radius=h / 2,
                           fill=fill, outline=outline, width=width)


def _shadow(size: tuple[int, int], box: tuple[float, float, float, float], radius: float,
            blur: float, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=(0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _mic(draw, cx: float, cy: float, s: float, color) -> None:
    """Microphone glyph; `s` = one logical pixel at the drawing size."""
    head_w, head_h = 6 * s, 10 * s
    draw.rounded_rectangle((cx - head_w / 2, cy - 8 * s, cx + head_w / 2, cy - 8 * s + head_h),
                           radius=head_w / 2, fill=color)
    stroke = max(1, round(1.6 * s))
    draw.arc((cx - 7 * s, cy - 9 * s, cx + 7 * s, cy + 5 * s), start=20, end=160,
             fill=color, width=stroke)
    draw.line((cx, cy + 5 * s, cx, cy + 8.5 * s), fill=color, width=stroke)
    draw.line((cx - 3.5 * s, cy + 8.5 * s, cx + 3.5 * s, cy + 8.5 * s), fill=color, width=stroke)


def _finish(big: Image.Image, layout: Layout) -> Image.Image:
    return big.resize((layout.width, layout.height), Image.LANCZOS)


# --- what's on screen ----------------------------------------------------------------------

def render(kind: str, text: str, color: str, layout: Layout, anchor: str | None,
           levels: Sequence[float] = (), now: float | None = None) -> Image.Image:
    """The overlay window's image (RGBA, layout.width x layout.height) for a view. The result
    may be shared (cached): don't modify it."""
    if kind in ("bar", "button"):
        return _render_button(kind, layout, anchor)
    now = time.monotonic() if now is None else now
    # The pill and its shadow never change: cached. Only the contents are drawn per frame,
    # at a lower supersampling, so the live mic meter stays cheap.
    img = _pill_background(layout).copy()
    ss = 2
    size = (layout.width * ss, layout.height * ss)
    s = layout.scale * ss  # one logical pixel
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cy = size[1] / 2
    f = font(round(14 * s))
    left = 2 * s + 16 * s
    if kind == "listening":
        r = (5 + 1.5 * (int(now * 3) % 2)) * s
        dot_x = left + 4 * s
        draw.ellipse((dot_x - r, cy - r, dot_x + r, cy + r), fill=(*RED, 255))
        draw.text((dot_x + 12 * s, cy), text, font=f, fill=WHITE, anchor="lm")
        # Live mic meter, so you can see that Cadence hears you.
        x = dot_x + 90 * s
        bar_w, gap = 3 * s, 3 * s
        for level in levels:
            bh = max(2.5 * s, min(24 * s, (level ** 0.5) * 80 * s))
            draw.rounded_rectangle((x, cy - bh / 2, x + bar_w, cy + bh / 2), radius=bar_w / 2,
                                   fill=(*SKY, 255))
            x += bar_w + gap
    elif kind == "working":
        dots = "." * (1 + int(now * 3) % 3)
        r = 5 * s
        dot_x = left + 4 * s
        draw.ellipse((dot_x - r, cy - r, dot_x + r, cy + r), fill=(*SKY, 255))
        draw.text((dot_x + 12 * s, cy), text + dots, font=f, fill=WHITE, anchor="lm")
    else:  # "message", "feedback_prompt"
        fill = _rgba(SKY if kind == "feedback_prompt" else color)
        draw.text((size[0] / 2, cy), text, font=f, fill=fill, anchor="mm")
    img.alpha_composite(layer.resize(img.size, Image.LANCZOS))
    return img


@functools.lru_cache(maxsize=32)
def _render_button(kind: str, layout: Layout, anchor: str | None) -> Image.Image:
    size = (layout.width * SS, layout.height * SS)
    s = layout.scale * SS  # one logical pixel
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = layout.content_x(anchor) * SS, size[1] / 2
    if kind == "bar":
        w, h = (v * SS for v in layout.bar)
        draw = ImageDraw.Draw(img)
        _capsule(draw, cx, cy, w + 2 * s, h + 2 * s, fill=(*SKY_DEEP, 210))
        _capsule(draw, cx, cy, w, h, fill=(*SKY, 165))
        return _finish(img, layout)
    w, h = (v * SS for v in layout.button)
    box = (cx - w / 2, cy - h / 2 + 2 * s, cx + w / 2, cy + h / 2 + 2 * s)
    img.alpha_composite(_shadow(size, box, h / 2, 3 * s, 90))
    draw = ImageDraw.Draw(img)
    _capsule(draw, cx, cy, w, h, fill=(*SKY_DEEP, 205), outline=(*SKY_PALE, 235),
             width=max(1, round(1.2 * s)))
    _mic(draw, cx, cy, s, WHITE)
    return _finish(img, layout)


@functools.lru_cache(maxsize=8)
def _pill_background(layout: Layout) -> Image.Image:
    """A dark see-through pill across the whole window, for status messages."""
    size = (layout.width * SS, layout.height * SS)
    s = layout.scale * SS
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    cy = size[1] / 2
    w, h = size[0] - 4 * s, size[1] - 6 * s
    box = (size[0] / 2 - w / 2, cy - h / 2 + 1.5 * s, size[0] / 2 + w / 2, cy + h / 2 + 1.5 * s)
    img.alpha_composite(_shadow(size, box, h / 2, 2.5 * s, 80))
    draw = ImageDraw.Draw(img)
    _capsule(draw, size[0] / 2, cy, w, h, fill=PILL_BG, outline=PILL_EDGE,
             width=max(1, round(1 * s)))
    return _finish(img, layout)


def render_marker(layout: Layout, anchor: str, active: bool) -> Image.Image:
    """Outline of a snap spot, shown while dragging; the one it would snap to is filled."""
    size = (layout.width * SS, layout.height * SS)
    s = layout.scale * SS
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    w, h = (v * SS for v in layout.button)
    cx, cy = layout.content_x(anchor) * SS, size[1] / 2
    if active:
        _capsule(draw, cx, cy, w, h, fill=(*SKY_DEEP, 110), outline=(*SKY_PALE, 240),
                 width=round(1.6 * s))
    else:
        _capsule(draw, cx, cy, w, h, fill=(*SKY, 40), outline=(*SKY, 170), width=round(1.2 * s))
    return _finish(img, layout)


def premultiplied_bgra(img: Image.Image) -> bytes:
    """Pixel bytes in the format Windows layered windows need (BGRA, premultiplied alpha)."""
    from PIL import ImageChops

    r, g, b, a = img.convert("RGBA").split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    return Image.merge("RGBA", (b, g, r, a)).tobytes()
