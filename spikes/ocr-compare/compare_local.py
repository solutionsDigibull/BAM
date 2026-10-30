"""Local engine (RapidOCR) over a folder of scans. Prints counts only, never the page text.

    uv run --with rapidocr-onnxruntime python compare_local.py <folder> <results.json>

For each `name.(png|jpg|jpeg|tif|tiff|pdf)` the answer key is `name.ids.txt` (one exact string per
line). A PDF is rendered at its first page through the repo's own rasteriser.
"""
import io
import json
import sys
import time
from pathlib import Path

from PIL import Image
from rapidocr_onnxruntime import RapidOCR

sys.path.insert(0, str(Path(__file__).parent))
from common import FILES, ids_of, scaled, score_ids  # noqa: E402

folder, out = Path(sys.argv[1]), Path(sys.argv[2])
engine = RapidOCR()
rows = []
for path in FILES(folder):
    key = ids_of(path)
    for variant, image in (("full", None), ("half", 0.5)):
        data = scaled(path, image)
        t0 = time.time()
        found, _ = engine(data)
        seconds = time.time() - t0
        text = " ".join(r[1] for r in (found or []))
        hit, total, missing = score_ids(text, key)
        rows.append({"file": path.name, "engine": "rapidocr", "variant": variant,
                     "ids_found": hit, "ids_total": total, "seconds": round(seconds, 2),
                     "chars": len(text)})
        print(path.name, variant, f"ids {hit}/{total}", f"{seconds:.1f}s", "missing:", missing, flush=True)
out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
