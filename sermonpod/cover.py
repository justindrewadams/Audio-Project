"""Podcast cover art: the preacher's photo, full-bleed, with the show name set over a
dark gradient at the bottom. 3000x3000 JPEG, as Apple Podcasts asks for."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

SIZE = 3000
FONTS = Path(__file__).resolve().parent.parent / "cloud" / "fonts"
GOLD = (222, 178, 118)
CREAM = (247, 240, 230)
SHADOW = (12, 9, 7)


def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / name), size)
    try:
        font.set_variation_by_axes([weight])
    except (OSError, AttributeError):  # static font or no variation support
        pass
    return font


def _fit_photo(photo: Image.Image, focus_y: float) -> Image.Image:
    """Crop to a square that fills the canvas. focus_y (0 = top, 1 = bottom) picks which
    part of a tall photo to keep; portraits usually want the upper part, where the face is."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    w, h = photo.size
    side = min(w, h)
    left = (w - side) // 2
    top = int((h - side) * focus_y)
    return photo.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)


def _spaced(draw: ImageDraw.ImageDraw, text: str, font, tracking: float) -> float:
    return sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def _draw_spaced(draw, xy, text, font, fill, tracking):
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + tracking


def make_cover(photo_path: Path, out_path: Path, *, kicker: str = "SERMONS", name: str = "Joshua Adams",
               footer: str = "MOUNTAIN HOME CHURCH OF CHRIST", focus_y: float = 0.2) -> Path:
    base = _fit_photo(Image.open(photo_path), focus_y)
    # quiet, warm photo so the type carries the design
    base = ImageEnhance.Color(base).enhance(0.85)
    base = ImageEnhance.Contrast(base).enhance(1.05)
    warm = Image.new("RGB", base.size, (60, 40, 25))
    base = Image.blend(base, warm, 0.10)

    # bottom gradient for the type, plus a soft vignette
    shade = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        t = min(1.0, max(0.0, (y - SIZE * 0.36) / (SIZE * 0.40)))
        shade.putpixel((0, y), int(255 * 0.97 * t * t * (3 - 2 * t)))  # smoothstep
    shade = shade.resize((SIZE, SIZE))
    vignette = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(vignette).ellipse((-SIZE * 0.25, -SIZE * 0.3, SIZE * 1.25, SIZE * 1.15), fill=255)
    vignette = ImageOps.invert(vignette.filter(ImageFilter.GaussianBlur(SIZE * 0.12))).point(lambda v: v * 0.55)
    mask = Image.fromarray(np.maximum(np.asarray(shade), np.asarray(vignette)))
    img = Image.composite(Image.new("RGB", base.size, SHADOW), base, mask)

    draw = ImageDraw.Draw(img)
    margin = 200
    name_font = _font("PlayfairDisplay.ttf", 400, 700)
    while draw.textlength(name, font=name_font) > SIZE - 2 * margin:
        name_font = _font("PlayfairDisplay.ttf", name_font.size - 10, 700)
    kicker_font = _font("Montserrat.ttf", 120, 600)
    footer_font = _font("Montserrat.ttf", 82, 500)

    footer_y = SIZE - margin - 82
    rule_y = footer_y - 110
    name_box = draw.textbbox((0, 0), name, font=name_font)
    name_y = rule_y - 90 - name_box[3]
    kicker_y = name_y + name_box[1] - 70 - 120

    _draw_spaced(draw, (margin, kicker_y), kicker, kicker_font, GOLD, tracking=48)
    draw.text((margin - name_box[0] * 0.5, name_y), name, font=name_font, fill=CREAM)
    draw.line([(margin, rule_y), (margin + 360, rule_y)], fill=GOLD, width=10)
    _draw_spaced(draw, (margin, footer_y), footer, footer_font, (*CREAM,), tracking=16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90, optimize=True, progressive=True)
    return out_path
