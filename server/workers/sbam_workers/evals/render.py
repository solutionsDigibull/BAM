"""Draw an OCR golden item's page from its `RenderSpec` (E15.5).

Plain Pillow, no randomness beyond the spec's seed, so the same spec gives the same page on one
machine. The repo keeps specs, not images; the gate draws the page when it runs.
"""

import io
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from sbam_workers.evals.golden import RenderSpec

PAGE = (827, 1169)  # A4 at 100 dpi, like the repo's scanned fixtures
MARGIN = 60


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> list[str]:
    rows: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > width:
            rows.append(current)
            current = word
        else:
            current = trial
    return [*rows, current] if current else rows


def render_page(spec: RenderSpec) -> tuple[bytes, str]:
    """The page as image bytes and its media type (`image/png` or `image/jpeg`)."""
    image = Image.new("RGB", PAGE, "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=spec.font_size)
    y = MARGIN
    for line in spec.lines:
        for row in _wrap(draw, line, font, PAGE[0] - 2 * MARGIN):
            draw.text((MARGIN, y), row, fill="black", font=font)
            y += int(spec.font_size * 1.5)
        y += spec.font_size // 2
    if spec.rotate:
        image = image.rotate(
            spec.rotate, expand=True, fillcolor=(235, 235, 235), resample=Image.Resampling.BICUBIC
        )
    if spec.blur:
        image = image.filter(ImageFilter.GaussianBlur(spec.blur))
    if spec.speckle:
        _speckle(image, spec.speckle, spec.seed)
    if spec.shade:
        image = _shade(image, spec.shade)
    if spec.scale < 1.0:
        size = (round(image.width * spec.scale), round(image.height * spec.scale))
        image = image.resize(size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if spec.jpeg_quality is None:
        image.save(out, format="PNG")
        return out.getvalue(), "image/png"
    image.save(out, format="JPEG", quality=spec.jpeg_quality)
    return out.getvalue(), "image/jpeg"


def _speckle(image: Image.Image, per_thousand: int, seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - repeatable image noise, nothing to do with security
    pixels = image.load()
    if pixels is None:
        raise RuntimeError("the image has no pixel access")
    width, height = image.size
    for _ in range(width * height * per_thousand // 1000):
        x, y = rng.randrange(width), rng.randrange(height)
        darker = rng.randrange(0, 90)
        r, g, b = pixels[x, y]  # type: ignore[misc]
        pixels[x, y] = (max(r - darker, 0), max(g - darker, 0), max(b - darker, 0))


def _shade(image: Image.Image, strength: float) -> Image.Image:
    """Uneven light: up to `strength` darker towards the bottom right corner. A gradient is
    multiplied in, which Pillow does in C (a per-pixel Python loop took seconds a page)."""
    ramp = Image.linear_gradient("L")  # black at the top, white at the bottom
    down = ramp.resize(image.size)
    across = ramp.transpose(Image.Transpose.ROTATE_90).resize(image.size)  # black left, white right
    towards_corner = ImageChops.add(down, across, scale=2)
    light = towards_corner.point(lambda v: round(255 - strength * v))
    return ImageChops.multiply(image, Image.merge("RGB", (light, light, light)))
