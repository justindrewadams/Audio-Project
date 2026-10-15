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
SHADOW = (14, 26, 48)   # background and fade colour: deep navy


def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / name), size)
    try:
        font.set_variation_by_axes([weight])
    except (OSError, AttributeError):  # static font or no variation support
        pass
    return font


def _fit_photo(photo: Image.Image, focus_y: float, height: float) -> Image.Image:
    """Place the photo at `height` x canvas height, centred at the top, so a head-and-
    shoulders shot leaves room for the title below. Any space at the sides is filled
    with a blurred, darkened copy of the photo, feathered into the sharp one."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    w, h = photo.size

    # background: the photo cropped to fill the square, heavily blurred
    side = min(w, h)
    left, top = (w - side) // 2, int((h - side) * focus_y)
    bg = photo.crop((left, top, left + side, top + side)).resize((SIZE, SIZE), Image.LANCZOS)
    bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(SIZE * 0.03))).enhance(0.42)

    # foreground: the whole photo, scaled to the chosen height (cropped if wider than the canvas)
    fh = int(SIZE * height)
    fw = round(w * fh / h)
    fg = photo.resize((fw, fh), Image.LANCZOS)
    if w * fh / h > SIZE * 1.0:
        x0 = (fw - SIZE) // 2
        fg, fw = fg.crop((x0, 0, x0 + SIZE, fh)), SIZE
    fg = fg.filter(ImageFilter.UnsharpMask(radius=4, percent=60, threshold=2))

    # feather the left and right edges so the photo melts into the background
    feather = int(SIZE * 0.10)
    ramp = np.ones(fw, dtype=np.float32)
    if fw < SIZE:
        edge = np.linspace(0, 1, feather, dtype=np.float32)
        ramp[:feather], ramp[-feather:] = edge, edge[::-1]
    mask = Image.fromarray((np.tile(ramp, (fh, 1)) * 255).astype(np.uint8))
    bg.paste(fg, ((SIZE - fw) // 2, 0), mask)
    return bg


def _has_cutout(photo: Image.Image) -> bool:
    """True for a photo whose background has already been removed (transparent PNG)."""
    return photo.mode in ("RGBA", "LA") and photo.getchannel("A").getextrema()[0] < 16


def _place_cutout(photo: Image.Image, background: tuple, height: float) -> Image.Image:
    """Put a cut-out subject on a solid background, top-centred. Where the photo's
    own frame cuts him off (shoulders at the sides), feather it away so there are
    no hard edges."""
    photo = ImageOps.exif_transpose(photo).convert("RGBA")
    photo = photo.crop(photo.getchannel("A").getbbox())
    fh = int(SIZE * height)
    fw = min(round(photo.width * fh / photo.height), SIZE)
    subject = photo.resize((fw, fh), Image.LANCZOS)
    subject = subject.filter(ImageFilter.UnsharpMask(radius=4, percent=50, threshold=2))

    alpha = np.asarray(subject.getchannel("A"), dtype=np.float32)
    feather = int(SIZE * 0.08)
    edge = np.linspace(0, 1, feather, dtype=np.float32)
    ramp = np.ones(fw, dtype=np.float32)
    ramp[:feather], ramp[-feather:] = edge, edge[::-1]
    subject.putalpha(Image.fromarray((alpha * ramp).astype(np.uint8)))

    canvas = Image.new("RGB", (SIZE, SIZE), background)
    canvas.paste(subject, ((SIZE - fw) // 2, int(SIZE * 0.05)), subject)
    return canvas


def _spaced(draw: ImageDraw.ImageDraw, text: str, font, tracking: float) -> float:
    return sum(draw.textlength(c, font=font) for c in text) + tracking * (len(text) - 1)


def _draw_spaced(draw, xy, text, font, fill, tracking):
    x, y = xy
    for c in text:
        draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + tracking


def make_cover(photo_path: Path, out_path: Path, *, kicker: str = "SERMONS", name: str = "Joshua Adams",
               footer: str = "MOUNTAIN HOME CHURCH OF CHRIST", focus_y: float = 0.2,
               photo_height: float = 0.88, background: tuple = SHADOW) -> Path:
    photo = Image.open(photo_path)
    if _has_cutout(photo):
        # smaller, and faded out before the title so the type sits on solid colour
        base = _place_cutout(photo, background, photo_height * 0.9)
        fade_start, fade_len, fade_max = 0.48, 0.22, 1.0
    else:
        fade_start, fade_len, fade_max = 0.36, 0.40, 0.97
        base = _fit_photo(photo, focus_y, photo_height)
        # quiet, warm photo so the type carries the design
        base = ImageEnhance.Color(base).enhance(0.85)
        base = ImageEnhance.Contrast(base).enhance(1.05)
        base = Image.blend(base, Image.new("RGB", base.size, (60, 40, 25)), 0.10)

    # bottom gradient for the type, plus a soft vignette
    shade = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        t = min(1.0, max(0.0, (y - SIZE * fade_start) / (SIZE * fade_len)))
        shade.putpixel((0, y), int(255 * fade_max * t * t * (3 - 2 * t)))  # smoothstep
    shade = shade.resize((SIZE, SIZE))
    vignette = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(vignette).ellipse((-SIZE * 0.25, -SIZE * 0.3, SIZE * 1.25, SIZE * 1.15), fill=255)
    vignette = ImageOps.invert(vignette.filter(ImageFilter.GaussianBlur(SIZE * 0.12))).point(lambda v: v * 0.55)
    mask = Image.fromarray(np.maximum(np.asarray(shade), np.asarray(vignette)))
    img = Image.composite(Image.new("RGB", base.size, background), base, mask)

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
