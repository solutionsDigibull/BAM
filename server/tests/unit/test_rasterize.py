"""E4.8 page images: size limits, orientation, frames, and files that are not what they claim.

Real pdfium and Pillow, no model. The image loader (which only counts frames) is tested here too.
"""

import io
import random

import pytest
from fixtures import fixture_path
from PIL import Image, ImageFile
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import MAX_OCR_PAGES, OCR_MAX_IMAGE_BYTES, OCR_MAX_SIDE_PX
from sbam_workers.ingest import rasterize
from sbam_workers.ingest.loaders import ParseError, loader_for, no_heartbeat
from sbam_workers.ingest.rasterize import PageUnreadable, fit, open_source
from test_loaders_office import make_pdf

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(png: bytes) -> tuple[int, int]:
    image = Image.open(io.BytesIO(png))
    return image.size


def encoded(image: Image.Image, fmt: str, **kw: object) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kw)
    return out.getvalue()


# --- PDF pages ------------------------------------------------------------------------------------


def test_e4_8_a_pdf_page_is_a_png_at_200_dpi() -> None:
    source = open_source(make_pdf(["a", "b"]), "pdf")
    try:
        assert source.pages == 2
        png = source.render(1)
    finally:
        source.close()
    assert png.startswith(PNG_MAGIC)
    assert png_size(png) == (1700, 2200)  # US Letter, 8.5 x 11 in at 200 DPI


def test_e4_8_the_golden_scanned_a4_page_is_about_1654_by_2339() -> None:
    source = open_source(fixture_path("documents", "scanned_full.pdf").read_bytes(), "pdf")
    try:
        width, height = png_size(source.render(2))
    finally:
        source.close()
    assert (width, height) == (1654, 2339)


def test_e4_8_a_very_large_page_is_scaled_to_the_longest_side_limit() -> None:
    big = make_pdf([""]).replace(b"[0 0 612 792]", b"[0 0 1800 900]")  # 25 x 12.5 inches
    source = open_source(big, "pdf")
    try:
        width, height = png_size(source.render(1))
    finally:
        source.close()
    assert max(width, height) == OCR_MAX_SIDE_PX and width > height


@pytest.mark.parametrize("data", [b"", b"%PDF-1.4 nothing", b"plain text", b"\x00" * 100])
def test_e4_8_a_pdf_pdfium_cannot_open_is_corrupt(data: bytes) -> None:
    with pytest.raises(ParseError) as caught:
        open_source(data, "pdf")
    assert caught.value.code is ErrorCode.CORRUPT


def test_e4_8_a_type_with_no_page_images_is_refused() -> None:
    with pytest.raises(ParseError) as caught:
        open_source(b"x", "docx")
    assert caught.value.code is ErrorCode.PARSE_ERROR


# --- fitting an image -----------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "CMYK", "1", "LA"])
def test_e4_8_any_colour_mode_comes_out_as_an_rgb_png(mode: str) -> None:
    png = fit(Image.new(mode, (50, 40)))
    assert png.startswith(PNG_MAGIC) and Image.open(io.BytesIO(png)).mode == "RGB"


def test_e4_8_a_wide_image_is_scaled_down_keeping_its_shape() -> None:
    png = fit(Image.new("RGB", (6000, 1500), (200, 200, 200)))
    assert png_size(png) == (OCR_MAX_SIDE_PX, 750)


def test_e4_8_a_small_image_is_not_scaled_up() -> None:
    assert png_size(fit(Image.new("RGB", (64, 48)))) == (64, 48)


def test_e4_8_a_noisy_image_is_shrunk_until_the_png_is_within_5_mb() -> None:
    rng = random.Random(3)  # noqa: S311 - test data
    noise = Image.frombytes("RGB", (2400, 2400), rng.randbytes(2400 * 2400 * 3))  # ~17 MB of PNG
    png = fit(noise)
    assert len(png) <= OCR_MAX_IMAGE_BYTES
    assert max(png_size(png)) < 2400  # it had to give up resolution


def test_e4_8_an_image_that_cannot_be_made_small_enough_is_an_unreadable_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rasterize, "OCR_MAX_IMAGE_BYTES", 10)
    with pytest.raises(PageUnreadable):
        fit(Image.new("RGB", (400, 400), (1, 2, 3)))


# --- image files ----------------------------------------------------------------------------------


def test_e4_8_the_golden_photo_and_scans_are_one_frame_each() -> None:
    for name in ("photo.jpg", "scan.png", "scan.tiff"):
        source = open_source(fixture_path("documents", name).read_bytes(), name.rsplit(".")[1])
        try:
            assert source.pages >= 1 and source.render(1).startswith(PNG_MAGIC)
        finally:
            source.close()


def test_e4_8_a_multi_page_tiff_has_a_page_per_frame_and_each_frame_renders() -> None:
    frames = [Image.new("RGB", (100, 80), (n * 50, 0, 0)) for n in range(4)]
    data = encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    source = open_source(data, "tiff")
    try:
        assert source.pages == 4
        frames = [Image.open(io.BytesIO(source.render(n))).convert("RGB") for n in (1, 2, 3, 4)]
    finally:
        source.close()
    reds: list[int] = []
    for frame in frames:
        pixel = frame.getpixel((1, 1))
        assert isinstance(pixel, tuple)
        reds.append(pixel[0])
    assert reds == [0, 50, 100, 150]  # in order, none repeated


def test_e4_8_a_frame_that_does_not_exist_is_an_unreadable_page() -> None:
    source = open_source(encoded(Image.new("RGB", (20, 20)), "PNG"), "png")
    try:
        with pytest.raises(PageUnreadable):
            source.render(5)
    finally:
        source.close()


def test_e4_8_an_exif_rotated_photo_comes_out_upright() -> None:
    wide = Image.new("RGB", (200, 100), (255, 255, 255))
    exif = Image.Exif()
    exif[0x0112] = 6  # the camera was turned: this image must be rotated 90 degrees clockwise
    data = encoded(wide, "JPEG", exif=exif.tobytes())
    source = open_source(data, "jpg")
    try:
        assert png_size(source.render(1)) == (100, 200)
    finally:
        source.close()


@pytest.mark.parametrize(
    "data", [b"", b"not an image", PNG_MAGIC + b"\x00" * 30, b"\xff\xd8\xff garbage"]
)
def test_e4_8_a_file_that_is_not_a_readable_image_is_corrupt(data: bytes) -> None:
    with pytest.raises(ParseError) as caught:
        open_source(data, "png")
    assert caught.value.code is ErrorCode.CORRUPT


def test_e4_8_a_truncated_image_is_corrupt_not_half_read() -> None:
    whole = encoded(Image.new("RGB", (300, 300), (9, 9, 9)), "PNG")
    with pytest.raises(ParseError) as caught:
        open_source(whole[: len(whole) // 2], "png")
    assert caught.value.code is ErrorCode.CORRUPT


def test_e4_8_a_truncated_image_is_corrupt_even_when_something_turned_truncated_loading_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WeasyPrint sets `ImageFile.LOAD_TRUNCATED_IMAGES` when it is imported, and the worker imports
    every module: the rasteriser must not depend on that process-wide switch (found in CI)."""
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    whole = encoded(Image.new("RGB", (300, 300), (9, 9, 9)), "PNG")
    with pytest.raises(ParseError) as caught:
        open_source(whole[: len(whole) // 2], "png")
    assert caught.value.code is ErrorCode.CORRUPT
    assert ImageFile.LOAD_TRUNCATED_IMAGES is True  # the switch is put back as it was


def test_e4_8_a_truncated_later_frame_is_an_unreadable_page_even_with_the_switch_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rng = random.Random(5)  # noqa: S311 - test data
    frames = [Image.frombytes("RGB", (200, 200), rng.randbytes(200 * 200 * 3)) for _ in range(2)]
    whole = encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:], compression="raw")
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    source = open_source(whole[: int(len(whole) * 0.75)], "tiff")
    try:
        assert source.render(1).startswith(PNG_MAGIC)  # the first frame is whole
        with pytest.raises(PageUnreadable):
            source.render(2)  # the second one is cut off
    finally:
        source.close()


def test_e4_8_an_image_with_too_many_pixels_is_refused_not_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = encoded(Image.new("RGB", (400, 400)), "PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10_000)  # Pillow's own bomb limit, much lower
    with pytest.raises(ParseError) as caught:
        open_source(data, "png")
    assert caught.value.code is ErrorCode.PARSE_ERROR


# --- the image loader -----------------------------------------------------------------------------


def load(kind: str, data: bytes) -> object:
    loader = loader_for(kind)
    assert loader is not None
    return loader(data, no_heartbeat)


@pytest.mark.parametrize("kind", ["png", "jpg", "jpeg", "tiff"])
def test_e4_8_every_image_type_has_a_loader_that_lists_its_frames_for_ocr(kind: str) -> None:
    frames = [Image.new("RGB", (30, 30), (n, n, n)) for n in range(3)]
    data = encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    parsed = load(kind, data) if kind == "tiff" else load(kind, encoded(frames[0], "PNG"))
    report = parsed.report  # type: ignore[attr-defined]
    assert report.ocr_needed_pages == ([1, 2, 3] if kind == "tiff" else [1])
    assert report.pages == len(report.ocr_needed_pages)
    assert list(parsed.blocks) == [] and parsed.markdown == ""  # type: ignore[attr-defined]


def test_e4_8_an_image_loader_refuses_a_file_that_is_not_an_image() -> None:
    for kind in ("png", "jpg", "tiff"):
        with pytest.raises(ParseError) as caught:
            load(kind, b"just text")
        assert caught.value.code is ErrorCode.CORRUPT


def test_e4_8_an_image_with_more_frames_than_the_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sbam_workers.ingest.loaders import image

    monkeypatch.setattr(image, "MAX_OCR_PAGES", 2)
    frames = [Image.new("RGB", (10, 10), (n, 0, 0)) for n in range(3)]
    data = encoded(frames[0], "TIFF", save_all=True, append_images=frames[1:])
    with pytest.raises(ParseError) as caught:
        load("tiff", data)
    assert caught.value.code is ErrorCode.PARSE_ERROR
    assert MAX_OCR_PAGES == 200  # the real limit is unchanged
