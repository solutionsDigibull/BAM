"""E4.5-T08 (perf): a 15 MB workbook parses within the 10-minute limit and under the 1 GiB cap.

Slow (a minute or two to build and parse the file), so it runs only on request:
`SBAM_PERF=1 pytest tests/unit/test_loaders_perf.py`. Measured results go in the PR.
"""

import os
import sys
import time

import pytest
from bigxlsx import COLUMNS, ROWS, big_workbook
from sbam_contracts.documents import MAX_FILE_BYTES, PARSE_MEMORY_LIMIT_BYTES, PARSE_TIMEOUT_SECONDS
from sbam_workers.ingest import parsing
from test_parsing import Harness, root  # noqa: F401 - the short-path fixture

pytestmark = pytest.mark.skipif(not os.environ.get("SBAM_PERF"), reason="SBAM_PERF=1 to run")


def test_e4_5_t08_a_15_mb_workbook_parses_in_time_and_within_the_memory_cap(
    root: object,  # noqa: F811
) -> None:
    data = big_workbook()
    assert MAX_FILE_BYTES * 0.9 < len(data) <= MAX_FILE_BYTES  # a real 15 MB upload
    h = Harness(root)  # type: ignore[arg-type]
    ref = h.add("big.xlsx", data, detected="xlsx", filetype_row_label="Filetype #23")
    started = time.monotonic()
    outcome = parsing.parse_version(h.deps, ref)
    elapsed = time.monotonic() - started
    assert outcome.parsed and outcome.report is not None and outcome.report.sheets == 1
    assert elapsed < PARSE_TIMEOUT_SECONDS
    key = h.version(ref).derived_json_key
    assert key is not None
    assert h.storage.get(key).count(b'"type":"cell"') == ROWS * COLUMNS  # raw count: no 86 MB model
    if sys.platform != "win32":  # ru_maxrss is in KiB; Windows has no `resource` module
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        assert peak < PARSE_MEMORY_LIMIT_BYTES
