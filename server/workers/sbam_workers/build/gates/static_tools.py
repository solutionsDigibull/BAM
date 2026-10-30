"""The four static tools of the `static_gate` (E12.4): Ruff, Black, MyPy --strict and Bandit.

Every tool reads the bundle's text and nothing runs it: Black is the library called in process,
the others run through an executor (`toolchain.py`): a separate Python process on a scratch copy of
the files, or the network-less `sbam-toolchain` container (E12.4b). A tool that cannot give a
verdict (it timed out, crashed, or printed something that is not its JSON) raises `ToolFailure`: the
activity retries it and, once retries are spent, the build stops as a platform defect. It is never a
pass and never a correction.

Findings are normalised to `{code, file, line, severity, message}`, sorted, so a second run on the
same bytes gives the same list. Each tool's version and the sha256 of its pinned config are
recorded with the verdict so a result can be reproduced (the configs below are the pins).
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Final

import black
from black.parsing import InvalidInput
from sbam_contracts.build_dashboard import GateName, GateStatus, Severity

from sbam_workers.build.codegen.bundle import BLACK_CONFIG_SHA256, BLACK_MODE, BLACK_VERSION
from sbam_workers.build.gates.toolchain import Executor, LocalExecutor, ToolFailure

PACKAGE: Final = "skill"
MAX_FINDINGS: Final = 200
MAX_MESSAGE: Final = 500

RUFF_TOML: Final = """\
line-length = 100
target-version = "py312"

[lint]
select = ["E", "F", "W", "B", "UP", "I"]
ignore = ["E501"]
"""
MYPY_INI: Final = """\
[mypy]
python_version = 3.12
strict = True
ignore_missing_imports = True
disallow_subclassing_any = False
disallow_untyped_decorators = False
"""
CONFIGS: Final = {"RUFF": RUFF_TOML, "MYPY": MYPY_INI}
BANDIT_BLOCKING: Final = frozenset({"HIGH"})


@dataclass(frozen=True)
class ToolResult:
    gate: GateName
    status: GateStatus
    findings: list[dict[str, Any]]
    tool_version: str
    config_sha256: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def finding(
    gate: GateName, code: str, file: str, line: int | None, severity: Severity, message: str
) -> dict[str, Any]:
    return {
        "gate": gate.value,
        "code": code,
        "file": file,
        "line": line,
        "severity": severity.value,
        "message": message[:MAX_MESSAGE],
    }


def _ordered(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = lambda f: (f["file"], f["line"] or 0, f["code"], f["message"])  # noqa: E731
    return sorted(found, key=key)[:MAX_FINDINGS]


def python_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    return {name: data for name, data in files.items() if name.endswith(".py")}


def _relative(path: str) -> str:
    name = Path(path.replace("\\", "/")).as_posix()
    marker = f"{PACKAGE}/"
    return name.split(marker, 1)[1] if marker in name else name


def run_ruff(files: Mapping[str, bytes], executor: Executor | None = None) -> ToolResult:
    ran = (executor or LocalExecutor()).run("ruff", python_files(files), RUFF_TOML)
    out = ran.stdout
    try:
        rows = json.loads(out or "[]")
        found = [
            finding(
                GateName.RUFF,
                str(r["code"] or "RUFF"),
                _relative(str(r["filename"])),
                (r.get("location") or {}).get("row"),
                Severity.HIGH,
                str(r["message"]),
            )
            for r in rows
        ]
    except (ValueError, KeyError, TypeError):
        raise ToolFailure("ruff printed something that is not its JSON") from None
    return _result(GateName.RUFF, found, ran.version, _sha(RUFF_TOML))


def run_black(files: Mapping[str, bytes]) -> ToolResult:
    found: list[dict[str, Any]] = []
    for name, data in sorted(python_files(files).items()):
        text = data.decode("utf-8", "replace")
        try:
            formatted = black.format_str(text, mode=BLACK_MODE)
        except InvalidInput:  # the AST gate already judged the syntax; no diff to report
            continue
        if formatted != text:
            old, new = text.splitlines(), formatted.splitlines()
            line = next(
                (i + 1 for i, (a, b) in enumerate(zip(old, new, strict=False)) if a != b),
                min(len(old), len(new)) + 1,
            )
            found.append(
                finding(
                    GateName.BLACK,
                    "BLACK_DIFF",
                    name,
                    line,
                    Severity.HIGH,
                    "would be reformatted by black",
                )
            )
    return _result(GateName.BLACK, found, BLACK_VERSION, BLACK_CONFIG_SHA256)


def run_mypy(files: Mapping[str, bytes], executor: Executor | None = None) -> ToolResult:
    ran = (executor or LocalExecutor()).run("mypy", python_files(files), MYPY_INI)
    out = ran.stdout
    found: list[dict[str, Any]] = []
    try:
        for row in (json.loads(line) for line in out.splitlines() if line.startswith("{")):
            if row.get("severity") != "error":
                continue
            found.append(
                finding(
                    GateName.MYPY,
                    str(row.get("code") or "mypy"),
                    _relative(str(row["file"])),
                    row.get("line"),
                    Severity.HIGH,
                    str(row["message"]),
                )
            )
    except (ValueError, KeyError, TypeError):
        raise ToolFailure("mypy printed something that is not its JSON") from None
    return _result(GateName.MYPY, found, ran.version, _sha(MYPY_INI))


def run_bandit(files: Mapping[str, bytes], executor: Executor | None = None) -> ToolResult:
    ran = (executor or LocalExecutor()).run("bandit", python_files(files), None)
    out = ran.stdout
    try:
        rows = json.loads(out)["results"]
        found = [
            finding(
                GateName.BANDIT,
                str(r["test_id"]),
                _relative(str(r["filename"])),
                r.get("line_number"),
                Severity(str(r["issue_severity"]).upper()),
                str(r["issue_text"]),
            )
            for r in rows
        ]
    except (ValueError, KeyError, TypeError):
        raise ToolFailure("bandit printed something that is not its JSON") from None
    blocking = [f for f in found if f["severity"] in BANDIT_BLOCKING]
    status = GateStatus.FAILED if blocking else GateStatus.PASSED
    # MEDIUM and LOW are reported on a passing verdict; only HIGH blocks
    return ToolResult(
        GateName.BANDIT, status, _ordered(found), ran.version, _sha("bandit:high-blocks")
    )


def _result(
    gate: GateName, found: list[dict[str, Any]], tool_version: str, config_sha: str
) -> ToolResult:
    status = GateStatus.FAILED if found else GateStatus.PASSED
    return ToolResult(gate, status, _ordered(found), tool_version, config_sha)


def make_tools(
    executor: Executor,
) -> tuple[Callable[[Mapping[str, bytes]], ToolResult], ...]:
    """The four tools, with Ruff, MyPy and Bandit running through `executor`."""
    return (
        partial(run_ruff, executor=executor),
        run_black,
        partial(run_mypy, executor=executor),
        partial(run_bandit, executor=executor),
    )


TOOLS: Final = make_tools(LocalExecutor())
