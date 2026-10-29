"""The child side of `isolation.run_isolated`: `python -I -m sbam_workers.ingest.isolated_child
<loader> <result.json>`, the file on stdin.

Limits first, then the file: nothing below runs without them. Whatever happens, the result file is
either a complete JSON document (`parsed` or `error`) or absent; the parent treats absent as a stop
by a limit or a signal."""

import importlib
import json
import resource
import sys
import types
from pathlib import Path
from typing import Any

# `sbam_workers.ingest.loaders/__init__` imports every loader, and the archive one pulls in all of
# the API router: about 2 s and 400 MB the child never needs. Register the package as an empty shell
# that only knows its directory, so `loaders.base` and `loaders.pdf` import without it.
_shell = types.ModuleType("sbam_workers.ingest.loaders")
_shell.__path__ = [str(Path(__file__).parent / "loaders")]
sys.modules.setdefault("sbam_workers.ingest.loaders", _shell)

from sbam_contracts.api import ErrorCode  # noqa: E402

from sbam_workers.ingest import isolation  # noqa: E402
from sbam_workers.ingest.loaders.base import ParseError  # noqa: E402


def _limit(which: int, value: int) -> None:
    resource.setrlimit(which, (value, value))


def _address_space() -> int:
    """Bytes of address space this process has mapped now (`VmSize`)."""
    with open("/proc/self/statm", encoding="ascii") as handle:
        return int(handle.read().split()[0]) * resource.getpagesize()


def main(argv: list[str]) -> int:
    name, out = argv[1], argv[2]
    resource.setrlimit(
        resource.RLIMIT_CPU, (isolation.CPU_SECONDS, isolation.CPU_SECONDS + 5)
    )  # soft: SIGXCPU
    _limit(resource.RLIMIT_FSIZE, isolation.RESULT_MAX_BYTES)
    _limit(resource.RLIMIT_CORE, 0)
    module, function = isolation.ISOLATED_LOADERS[name]
    loader = getattr(importlib.import_module(module), function)
    _limit(resource.RLIMIT_AS, _address_space() + isolation.MEMORY_BYTES)
    data = sys.stdin.buffer.read()
    result: dict[str, Any]
    try:
        parsed = loader(data, lambda: None)
        markdown = parsed.markdown if isinstance(parsed.markdown, str) else "".join(parsed.markdown)
        result = {
            "parsed": {
                "blocks": [b.model_dump(mode="json") for b in parsed.blocks],
                "tables": [t.model_dump(mode="json") for t in parsed.tables],
                "markdown": markdown,
                "report": parsed.report.model_dump(mode="json"),
            }
        }
    except ParseError as exc:
        result = {"error": {"code": exc.code.value, "message": str(exc)[:200]}}
    except MemoryError:
        result = {
            "error": {"code": ErrorCode.PARSE_ERROR.value, "message": "the file is too big to read"}
        }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
