"""Measure real slot-fill failure rates (E0.16, #196): N runs per skill through the spike pipeline.

Each run: render -> model bodies (Pydantic AI; schema re-asks counted) -> assemble -> the AST
gates and ruff / black / mypy. When a gate fails, up to `MAX_CORRECTIONS` correction rounds send
the findings back to the model in the same conversation (the design's correction budget, TD
§13.2), and the gates run again. Every run records requests, re-asks, tokens, model and total
latency, cost, the first-pass verdict of each gate and the corrections used.

Only aggregates and gate names leave this module: no generated code, no prompt text and no raw
model output are written to the report (the spike's `out/` folder is git-ignored).

  PYTHONPATH=../spikes/codegen uv run ... python -m codegen.measure --runs 20 --report <json> \
      --manifest <ms01> --manifest <bom>      (`make -C spikes/codegen live-measure`)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model

from codegen import assemble as asm
from codegen import ast_gates, bodies
from codegen.cli import static_gates
from codegen.context import build_context, load_manifest

MAX_CORRECTIONS = 2  # TD §13.2: the assumption this spike measures
# OpenRouter list price of openai/gpt-4o (USD per million tokens) when no other price is given;
# override with PRICE_IN_PER_M / PRICE_OUT_PER_M for another model behind the alias.
PRICE_IN_PER_M = float(os.environ.get("PRICE_IN_PER_M", "2.50"))
PRICE_OUT_PER_M = float(os.environ.get("PRICE_OUT_PER_M", "10.00"))


@dataclass
class Run:
    skill_id: str
    run: int
    requests: int = 0  # model calls, re-asks included
    reasks: int = 0  # schema-validation re-asks (Pydantic AI retries)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0  # the whole run: model calls plus gates
    model_seconds: float = 0.0  # time spent waiting for the model only
    schema_invalid: bool = False  # still invalid after the re-asks: nothing assembled
    first_pass: dict[str, bool] = field(default_factory=dict)  # gate -> passed, before corrections
    corrections: int = 0
    passed: bool = False  # all gates green after at most MAX_CORRECTIONS
    error: str | None = None  # transport / unexpected error (the run is counted, not hidden)

    @property
    def cost(self) -> float:
        return (self.input_tokens * PRICE_IN_PER_M + self.output_tokens * PRICE_OUT_PER_M) / 1e6


def _gates(
    ctx: dict[str, Any], skeleton: dict[str, str], output: Any, static: bool
) -> list[dict[str, Any]]:
    files = asm.assemble(ctx, output)
    results = ast_gates.run_all(files, skeleton, ctx, asm.load_spec())
    if static:
        with tempfile.TemporaryDirectory(prefix="sbam-measure-") as tmp:
            pkg = asm.write_bundle(files, Path(tmp), ctx)
            results += static_gates(pkg)
    return results


def _findings(results: list[dict[str, Any]]) -> str:
    lines = []
    for r in results:
        if not r["passed"]:
            for f in r["findings"] or [{"file": "", "line": 0, "message": "failed"}]:
                lines.append(f"- {r['check']} {f['file']}:{f['line']} {f['message'][:400]}")
    return "\n".join(lines)


def _ask(run: Run, agent: Agent[None, Any], prompt: str, history: Any = None) -> Any:
    start = time.perf_counter()
    try:
        return agent.run_sync(prompt, message_history=history)
    finally:
        run.model_seconds = round(run.model_seconds + time.perf_counter() - start, 2)


def _count(run: Run, result: Any) -> None:
    usage = result.usage() if callable(result.usage) else result.usage  # a property in 2.x
    run.requests += usage.requests
    run.input_tokens += usage.input_tokens or 0
    run.output_tokens += usage.output_tokens or 0


def measure_one(model: Model, manifest: Path, n: int, static: bool = True) -> Run:
    ctx = build_context(load_manifest(manifest), asm.load_spec()["version"])
    skeleton = asm.render_skeleton(ctx)
    run = Run(skill_id=ctx["skill_id"], run=n)
    agent = Agent(
        model,
        output_type=bodies.CodeBodies,
        instructions=bodies.INSTRUCTIONS,
        retries=bodies.RETRIES,
    )
    start = time.perf_counter()
    try:
        result = _ask(run, agent, bodies.build_prompt(ctx, skeleton))
        _count(run, result)
        results = _gates(ctx, skeleton, result.output, static)
        run.first_pass = {r["check"]: r["passed"] for r in results}
        while not all(r["passed"] for r in results) and run.corrections < MAX_CORRECTIONS:
            run.corrections += 1
            feedback = (
                "The bodies failed these gates. Return corrected CodeBodies that keep everything "
                "else as it was:\n" + _findings(results)
            )
            result = _ask(run, agent, feedback, result.all_messages())
            _count(run, result)
            results = _gates(ctx, skeleton, result.output, static)
        run.passed = all(r["passed"] for r in results)
    except UnexpectedModelBehavior:
        run.schema_invalid = True
    except Exception as exc:  # a transport error is a result too: counted, never hidden
        run.error = type(exc).__name__
    run.seconds = round(time.perf_counter() - start, 2)
    # Pydantic AI makes one request per attempt: everything past one per round is a re-ask.
    run.reasks = max(0, run.requests - 1 - run.corrections)
    return run


def summarize(runs: list[Run]) -> dict[str, Any]:
    done = [r for r in runs if r.error is None]
    gates = sorted({g for r in done for g in r.first_pass})
    seconds = [r.seconds for r in done] or [0.0]
    model = [r.model_seconds for r in done] or [0.0]
    return {
        "runs": len(runs),
        "errors": [f"run {r.run}: {r.error}" for r in runs if r.error],
        "schema_invalid": sum(r.schema_invalid for r in done),
        "first_pass_all_green": sum(
            bool(r.first_pass) and all(r.first_pass.values()) for r in done
        ),
        "first_pass_failure_rate": {
            g: round(
                sum(1 for r in done if r.first_pass and not r.first_pass.get(g, True))
                / max(1, sum(1 for r in done if r.first_pass)),
                3,
            )
            for g in gates
        },
        "corrections_used": {k: sum(1 for r in done if r.corrections == k) for k in range(3)},
        "passed_within_budget": sum(r.passed for r in done),
        "failed_after_budget": sum(not r.passed and not r.schema_invalid for r in done),
        "reasks_total": sum(r.reasks for r in done),
        "requests_total": sum(r.requests for r in done),
        "model_latency_s": {
            "median": round(statistics.median(model), 2),
            "max": round(max(model), 2),
        },
        "latency_s": {
            "median": round(statistics.median(seconds), 2),
            "p90": round(sorted(seconds)[max(0, int(len(seconds) * 0.9) - 1)], 2),
            "max": round(max(seconds), 2),
        },
        "tokens": {
            "input": sum(r.input_tokens for r in done),
            "output": sum(r.output_tokens for r in done),
        },
        "cost_usd": {
            "total": round(sum(r.cost for r in done), 4),
            "per_run": round(sum(r.cost for r in done) / max(1, len(done)), 4),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codegen.measure", description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bodies", type=Path, help="mock fixture instead of Bifrost (dry run)")
    parser.add_argument("--no-static", action="store_true")
    args = parser.parse_args(argv)
    if args.bodies is None and not os.environ.get("BIFROST_URL"):
        print("set BIFROST_URL (the Bifrost OpenAI-compatible endpoint) or pass --bodies")
        return 2
    report: dict[str, Any] = {
        "model": "mock" if args.bodies else f"bifrost:{bodies.ALIAS}",
        "price_per_m_tokens_usd": {"input": PRICE_IN_PER_M, "output": PRICE_OUT_PER_M},
        "max_corrections": MAX_CORRECTIONS,
        "skills": {},
    }
    for manifest in args.manifest:
        runs = []
        for n in range(1, args.runs + 1):
            model: Model = (
                bodies.MockModel(bodies.load_responses(args.bodies)).model()
                if args.bodies
                else bodies.bifrost_model()
            )
            run = measure_one(model, manifest, n, static=not args.no_static)
            runs.append(run)
            green = "pass" if run.passed else ("schema-invalid" if run.schema_invalid else "fail")
            print(
                f"{run.skill_id} run {n}/{args.runs}: {green}, corrections {run.corrections}, "
                f"requests {run.requests}, {run.seconds}s{', ' + run.error if run.error else ''}",
                flush=True,
            )
        skill = runs[0].skill_id
        report["skills"][skill] = {
            "summary": summarize(runs),
            "runs": [{**asdict(r), "cost": round(r.cost, 5)} for r in runs],
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
