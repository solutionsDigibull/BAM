"""Shared helpers for the OCR comparison (#318). Nothing here prints page text."""
import io
from pathlib import Path

from PIL import Image

EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".pdf"}


def FILES(folder: Path):  # noqa: N802
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in EXT)


def ids_of(path: Path) -> list[str]:
    key = path.parent / f"{path.stem}.ids.txt"
    if not key.exists():
        return []
    return [line.strip() for line in key.read_text(encoding="utf-8").splitlines() if line.strip()]


def page_png(path: Path) -> bytes:
    """The first page as PNG bytes (a PDF is rendered by the repo's rasteriser)."""
    if path.suffix.lower() == ".pdf":
        from sbam_workers.ingest.rasterize import open_source

        source = open_source(path.read_bytes(), "pdf")
        try:
            return source.render(1)
        finally:
            source.close()
    out = io.BytesIO()
    Image.open(path).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def scaled(path: Path, factor: float | None) -> bytes:
    data = page_png(path)
    if factor is None:
        return data
    image = Image.open(io.BytesIO(data))
    image = image.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def score_ids(text: str, key: list[str]) -> tuple[int, int, list[str]]:
    flat = "".join(text.split())
    missing = [i for i in key if "".join(i.split()) not in flat]
    return len(key) - len(missing), len(key), missing
