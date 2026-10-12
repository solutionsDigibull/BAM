"""Skeleton render + slot assembly for Spike E0.11 (#29), TD §12.3-12.4.

render_skeleton() renders every file listed in template.yaml (slots keep their placeholder).
assemble() puts validated CodeBodies into the slots: each body is re-indented to its marker,
so it can never escape the function it belongs to. Black then formats the result.
write_bundle() writes the files plus bundle.json (sha256 per file and for the whole bundle).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import black
import yaml
from black.mode import TargetVersion

from codegen.bodies import CodeBodies
from codegen.render import make_env, render_file, sha256_text

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "fastmcp-loopy" / "0.1.0"
SLOT_OPEN = re.compile(r"# >>> LLM_BODY:(\w+)")
BLACK_MODE = black.Mode(line_length=100, target_versions={TargetVersion.PY312})


def load_spec(template_dir: Path = TEMPLATE_DIR) -> dict[str, Any]:
    spec: dict[str, Any] = yaml.safe_load((template_dir / "template.yaml").read_text("utf-8"))
    return spec


def slot_re(slot: str) -> re.Pattern[str]:
    """Matches one slot: (indent)(open marker line)(content)(close marker line)."""
    return re.compile(
        rf"^(?P<indent>[ ]*)# >>> LLM_BODY:{slot}\n(?P<body>.*?)^(?P=indent)# <<< LLM_BODY:{slot}$",
        re.M | re.S,
    )


def slot_contents(code: str, slot: str) -> list[str]:
    return [m.group("body") for m in slot_re(slot).finditer(code)]


def replace_slot(code: str, slot: str, body: str) -> str:
    """Replace the content of `slot` with `body`, indented to the marker. Exactly one slot."""
    matches = list(slot_re(slot).finditer(code))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {slot} slot, found {len(matches)}")
    m = matches[0]
    indent = m.group("indent")
    text = "".join(indent + line if line.strip() else line for line in body.splitlines(True))
    if text and not text.endswith("\n"):
        text += "\n"
    return code[: m.start("body")] + text + code[m.end("body") :]


def render_skeleton(ctx: dict[str, Any], template_dir: Path = TEMPLATE_DIR) -> dict[str, str]:
    env = make_env(template_dir)
    return {
        f["output"]: render_file(env, f["template"], ctx) for f in load_spec(template_dir)["files"]
    }


def _chunks(text: str, width: int = 88) -> list[str]:
    """Split at spaces into pieces of <= width chars; "".join(pieces) == text exactly."""
    pieces: list[str] = []
    for word in re.findall(r"\S+\s*|\s+", text):
        if pieces and len(pieces[-1]) + len(word) <= width:
            pieces[-1] += word
        else:
            pieces.append(word)
    return pieces


def _queries(queries: dict[str, str]) -> str:
    """{name: SQL} -> a dict literal. The LLM supplies data only, never Python source here.
    Long SQL becomes implicitly concatenated literals (same string value) so lines stay <= 100."""
    lines = []
    for name, sql in queries.items():
        one_line = f"    {name!r}: {sql!r},\n"
        if len(one_line) <= 101:
            lines.append(one_line)
            continue
        parts = "".join(f"        {p!r}\n" for p in _chunks(sql))
        lines.append(f"    {name!r}: (\n{parts}    ),\n")
    return "QUERIES: dict[str, str] = {\n" + "".join(lines) + "}\n"


def assemble(
    ctx: dict[str, Any], bodies: CodeBodies, template_dir: Path = TEMPLATE_DIR
) -> dict[str, str]:
    """Skeleton + bodies -> formatted files. `bodies` is already schema-validated."""
    skeleton = render_skeleton(ctx, template_dir)
    values = {
        "action_body": bodies.action_body,
        "check_body": bodies.check_body,
        "state_queries": _queries(bodies.state_queries),
        "helpers": bodies.helpers,
    }
    files = {}
    for f in load_spec(template_dir)["files"]:
        code = skeleton[f["output"]]
        for slot, field in f["slots"].items():
            code = replace_slot(code, slot, values[field])
        files[f["output"]] = black.format_str(code, mode=BLACK_MODE)
    return files


def package_name(ctx: dict[str, Any]) -> str:
    return re.sub(r"[^0-9a-z]+", "_", ctx["skill_id"].lower()).strip("_")


def write_bundle(files: dict[str, str], out: Path, ctx: dict[str, Any]) -> Path:
    """Write <out>/<package>/{files, __init__.py} and <out>/bundle.json. Replaces <out>."""
    if out.exists():
        shutil.rmtree(out)
    pkg = out / package_name(ctx)
    pkg.mkdir(parents=True)
    all_files = {"__init__.py": f'"""Sub-Skill {ctx["skill_id"]} (generated)."""\n', **files}
    for name, code in all_files.items():
        (pkg / name).write_text(code, encoding="utf-8", newline="\n")
    hashes = {name: sha256_text(code) for name, code in sorted(all_files.items())}
    bundle = {
        "skill_id": ctx["skill_id"],
        "template": {"name": "fastmcp-loopy", "version": ctx["template_version"]},
        "package": pkg.name,
        "files": hashes,
        "bundle_sha256": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    }
    (out / "bundle.json").write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return pkg
