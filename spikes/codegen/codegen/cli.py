"""CLI for Spike E0.11 (#29). Run from server/ so sbam_contracts is importable:

  PYTHONPATH=../spikes/codegen uv run --with ... python -m codegen.cli render --manifest M --out D
  PYTHONPATH=../spikes/codegen uv run --with ... python -m codegen.cli generate --manifest M \
      --out D [--bodies fixtures/mock_bodies/x.yaml] [--action-step TEXT] [--no-static]
  (`make -C spikes/codegen help` lists ready-made targets)

render    skeleton only (slots keep their placeholder); prints sha256 per file (T01, T03)
generate  render -> LLM bodies (mock, or Bifrost if BIFROST_URL is set) -> schema validation ->
          assemble -> AST gates -> ruff / black --check / mypy --strict. Writes <out>/gates.json.
Exit codes: 0 all gates pass, 1 a gate failed, 2 LLM output invalid after retries (nothing written).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic_ai.exceptions import UnexpectedModelBehavior

from codegen import assemble as asm
from codegen import ast_gates, bodies
from codegen.context import build_context, load_manifest

SERVER_PYPROJECT = Path(__file__).resolve().parents[3] / "server" / "pyproject.toml"


def _context(args: argparse.Namespace) -> dict[str, Any]:
    ctx = build_context(load_manifest(args.manifest), asm.load_spec()["version"])
    if args.action_step is not None:  # T03: put hostile text where manifest prose goes
        ctx["action_step"] = args.action_step
    return ctx


def cmd_render(args: argparse.Namespace) -> int:
    ctx = _context(args)
    files = asm.render_skeleton(ctx)
    asm.write_bundle(files, args.out, ctx)
    bundle = json.loads((args.out / "bundle.json").read_text(encoding="utf-8"))
    for name, digest in bundle["files"].items():
        print(f"{digest}  {name}")
    print(f"bundle_sha256 {bundle['bundle_sha256']}")
    return 0


def static_gates(pkg: Path) -> list[dict[str, Any]]:
    """ruff, black --check and mypy --strict on the written package (TD §12.6 static gate)."""
    py = sys.executable
    cfg = str(SERVER_PYPROJECT)
    cache = str(pkg.parent / ".mypy_cache")
    runs = {
        "RUFF": [py, "-m", "ruff", "check", "--no-cache", "--config", cfg, pkg.name],
        "BLACK": [py, "-m", "black", "--check", "-q", "--config", cfg, pkg.name],
        "MYPY": [py, "-m", "mypy", "--strict", "--cache-dir", cache, "-p", pkg.name],
    }
    out = []
    for check, cmd in runs.items():
        # cmd is built above from fixed arguments and our own output path, never from user text
        p = subprocess.run(cmd, cwd=pkg.parent, capture_output=True, text=True)  # noqa: S603
        text = (p.stdout + p.stderr).strip()
        findings = [] if p.returncode == 0 else [{"file": "", "line": 0, "message": text}]
        out.append({"check": check, "passed": p.returncode == 0, "findings": findings})
    return out


def cmd_generate(args: argparse.Namespace) -> int:
    ctx = _context(args)
    skeleton = asm.render_skeleton(ctx)
    mock = None
    if os.environ.get("BIFROST_URL") and args.bodies is None:
        model, model_name = bodies.bifrost_model(), f"bifrost:{bodies.ALIAS}"
    else:
        if args.bodies is None:
            print("no --bodies fixture and BIFROST_URL not set", file=sys.stderr)
            return 2
        mock = bodies.MockModel(bodies.load_responses(args.bodies))
        model, model_name = mock.model(), f"mock:{args.bodies.name}"
    try:
        code_bodies = bodies.generate(model, ctx, skeleton)
    except UnexpectedModelBehavior as e:
        calls = mock.calls if mock else "?"
        print(f"LLM output invalid after {calls} attempts ({e}); no files assembled")
        return 2
    attempts = mock.calls if mock else None

    files = asm.assemble(ctx, code_bodies)
    pkg = asm.write_bundle(files, args.out, ctx)
    results = ast_gates.run_all(files, skeleton, ctx, asm.load_spec())
    if not args.no_static:
        results += static_gates(pkg)
    report = {"skill_id": ctx["skill_id"], "model": model_name, "attempts": attempts}
    report["gates"] = results
    (args.out / "gates.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(
        f"{ctx['skill_id']}: {len(files)} files -> {pkg}  (model {model_name}, attempts {attempts})"
    )
    for r in results:
        print(f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['check']}")
        for f in r["findings"]:
            print(f"         {f['file']}:{f['line']}  {f['message'][:300]}")
    return 0 if all(r["passed"] for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codegen", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("render", "generate"):
        p = sub.add_parser(name)
        p.add_argument("--manifest", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--action-step", help="override a_automation.action_step (T03)")
        if name == "generate":
            p.add_argument("--bodies", type=Path, help="mock LLM fixture (YAML)")
            p.add_argument("--no-static", action="store_true", help="skip ruff/black/mypy")
    args = parser.parse_args(argv)
    return cmd_render(args) if args.cmd == "render" else cmd_generate(args)


if __name__ == "__main__":
    sys.exit(main())
