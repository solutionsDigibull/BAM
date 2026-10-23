"""A 15 MB workbook of unique text (800,000 cells), the largest an upload may be (E4.5-T08).

Built with openpyxl in write-only mode, which takes about 15 seconds. Shared by the unit perf test
and the stack tests.
"""

import io
import random

from openpyxl import Workbook

ROWS, COLUMNS = 200_000, 4  # 800,000 cells of unique text: about 15 MB


def big_workbook() -> bytes:
    rng = random.Random(7)  # noqa: S311 - test data, not a secret
    book = Workbook(write_only=True)
    sheet = book.create_sheet("Big")
    for n in range(ROWS):
        sheet.append([f"{rng.getrandbits(80):020x}-{n}" for _ in range(COLUMNS)])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
