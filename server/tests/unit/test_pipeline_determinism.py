"""E10.1-T09: workflow code under sbam_workers/pipeline is deterministic (TD §13.4).

A static scan, so a forbidden call fails CI before it can corrupt a running pipeline's replay.
"""

import ast
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[2] / "workers" / "sbam_workers" / "pipeline"
FORBIDDEN_CALLS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "sleep"),
    ("time", "monotonic"),
    ("uuid", "uuid1"),
    ("uuid", "uuid4"),
    ("os", "getenv"),
    ("asyncio", "sleep"),
}
FORBIDDEN_NAMES = {"open", "uuid4", "uuid1", "getenv", "print", "input"}
FORBIDDEN_MODULES = {"random", "secrets", "socket", "requests", "httpx", "subprocess", "threading"}


def violations(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            modules = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for module in modules:
                if module.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"line {node.lineno}: import {module}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if (func.value.id, func.attr) in FORBIDDEN_CALLS:
                    found.append(f"line {node.lineno}: {func.value.id}.{func.attr}()")
            elif isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
                found.append(f"line {node.lineno}: {func.id}()")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) == ("os", "environ"):
                found.append(f"line {node.lineno}: os.environ")
    return found


@pytest.mark.parametrize("path", sorted(PIPELINE.glob("*.py")), ids=lambda p: p.name)
def test_e10_1_t09_workflow_code_is_deterministic(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "snippet",
    [
        "import datetime\ndatetime.now()",
        "import random",
        "x = uuid4()",
        "import os\nos.environ['X']",
        "import time\ntime.sleep(1)",
        "open('f')",
    ],
)
def test_e10_1_t09_the_lint_catches_forbidden_calls(snippet: str) -> None:
    assert violations(snippet)
