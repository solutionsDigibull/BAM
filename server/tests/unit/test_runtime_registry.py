"""E12.1 runtime registry: the in-image generator, the contract and the lock policy check.

The generator is stdlib-only and normally runs inside skill-runtime; here it runs in the server
venv, which is a real Python environment with real distributions (psycopg among them).
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.runtime_registry import RuntimeRegistry, registry_sha256

REPO = Path(__file__).resolve().parents[3]
DIGEST = "sha256:" + "ab" * 32
ALLOWED_IN = str(REPO / "compose/docker/skill-runtime/requirements.in")


def load(path: str, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = load("compose/docker/skill-runtime/registry.py", "skill_runtime_registry")
lock_check = load("scripts/check_runtime_lock.py", "check_runtime_lock")


def test_e12_1_t02_same_environment_same_registry_except_generated_at() -> None:
    first = generator.build(DIGEST, "2026-10-13T00:00:00+00:00", ALLOWED_IN)
    second = generator.build(DIGEST, "2026-10-14T09:30:00+00:00", ALLOWED_IN)
    assert first["content_sha256"] == second["content_sha256"]
    assert {**first, "generated_at": None} == {**second, "generated_at": None}
    assert json.dumps(first["packages"]) == json.dumps(second["packages"])  # byte-stable order


def test_e12_1_generator_output_is_the_contract_and_hashes_agree() -> None:
    raw = generator.build(DIGEST, allowed_file=ALLOWED_IN)
    registry = RuntimeRegistry.model_validate(raw)
    assert registry.content_sha256 == registry_sha256(registry)  # stdlib hash == RFC 8785 hash
    names = [p.name for p in registry.packages]
    assert names == sorted(names) and "pip" not in names
    assert all(n == n.lower() and "_" not in n for n in names)  # PEP 503


@pytest.mark.parametrize(
    ("dist", "module"),
    [("psycopg", "psycopg"), ("python-dateutil", "dateutil"), ("pyjwt", "jwt")],
)
def test_e12_1_t07_import_names_differ_from_dist_names(dist: str, module: str) -> None:
    registry = RuntimeRegistry.model_validate(generator.build(DIGEST, allowed_file=ALLOWED_IN))
    package = registry.package(dist)
    assert package is not None, dist
    assert module in package.top_level_modules


def test_e12_1_digest_is_validated() -> None:
    with pytest.raises(ValueError, match="sha256"):
        generator.build("latest", allowed_file=ALLOWED_IN)
    assert generator.main(["--image-digest", "sha256:short"]) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r["packages"][0].update(version="0.0.0-tampered"),
        lambda r: r["packages"].reverse(),
        lambda r: r.update(image_digest="sha256:" + "cd" * 32),
        lambda r: r["packages"][0].update(allowed=not r["packages"][0]["allowed"]),
    ],
    ids=["tampered-version", "unsorted", "other-digest", "flipped-allowed"],
)
def test_e12_1_contract_rejects_tampered_or_unsorted_content(change: Any) -> None:
    raw = generator.build(DIGEST, allowed_file=ALLOWED_IN)
    change(raw)
    with pytest.raises(ValidationError):
        RuntimeRegistry.model_validate(raw)


def test_e12_1_real_lock_is_pinned_and_hashed() -> None:
    text = (REPO / "compose/docker/skill-runtime/requirements.lock").read_text(encoding="utf-8")
    assert lock_check.problems(text) == []
    names = {entry.split("==")[0] for _, entry in lock_check.requirements(text)}
    assert {"fastmcp", "mcp", "pydantic", "psycopg", "sqlalchemy"} <= names


@pytest.mark.parametrize(
    ("lock", "problem"),
    [
        ("foo>=1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n", "not pinned with =="),
        ("foo==1.0\n", "no --hash"),
        ("-e git+https://example.com/x.git#egg=x\n", "not a pinned requirement"),
        ("foo==1.0 --index-url https://x --hash=sha256:" + "a" * 64 + "\n", "unsupported"),
        ("# only comments\n", "no requirements"),
    ],
    ids=["unpinned", "no-hash", "editable", "index-url", "empty"],
)
def test_e12_1_lock_policy_rejects(lock: str, problem: str) -> None:
    assert any(problem in p for p in lock_check.problems(lock))


# --- E3.12: which packages Sub-Skills may import ----------------------------------------------


def test_e3_12_direct_libraries_are_allowed_and_transitive_ones_are_not() -> None:
    registry = RuntimeRegistry.model_validate(generator.build(DIGEST, allowed_file=ALLOWED_IN))
    allowed = {p.name for p in registry.packages if p.allowed}
    # requirements.in libraries present in this environment are allowed ...
    assert {"pydantic", "httpx", "psycopg", "sqlalchemy"} <= allowed
    # ... their dependencies are installed but not allowed
    for transitive in ("pydantic-core", "annotated-types", "anyio", "httpcore"):
        package = registry.package(transitive)
        assert package is not None and package.allowed is False, transitive


def test_e3_12_allowed_names_normalise_extras_versions_and_comments(tmp_path: Path) -> None:
    req = tmp_path / "requirements.in"
    lines = [
        "# header",
        "fastmcp>=4.0,<4.1  # FastMCP",
        "psycopg[binary]",
        "Python_Dateutil ; python_version >= '3.12'",
        "",
        "PyJWT==2.*",
    ]
    req.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert generator.allowed_names(str(req)) == {
        "fastmcp",
        "psycopg",
        "python-dateutil",
        "pyjwt",
    }


def test_e3_12_the_real_allowed_list_is_the_documented_libraries() -> None:
    names = generator.allowed_names(ALLOWED_IN)
    assert {"fastmcp", "mcp", "pydantic", "httpx", "psycopg", "sqlalchemy", "tenacity"} <= names
    assert "psycopg-binary" not in names  # an extra's implementation, not imported by Sub-Skills


def test_e3_12_missing_or_empty_allowed_file_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        generator.build(DIGEST, allowed_file=str(tmp_path / "missing.in"))
    empty = tmp_path / "empty.in"
    empty.write_text("# nothing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no allowed libraries"):
        generator.build(DIGEST, allowed_file=str(empty))
    assert generator.main(["--allowed", str(tmp_path / "missing.in")]) == 2
