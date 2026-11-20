"""End-to-end chain eval (#926, WG-39): score a build's structure against an answer key.

No model is called here. A finished (or parked) build is reduced to a small JSON document, the
build's Sub-Skills in order with their output field names, and compared with an answer key written
by a person. The score is the mean of four ratios in [0, 1]:

- `steps`: the key's steps that the build has (names compared lower-case, letters and digits only);
- `order`: how much of the key's order the build keeps (longest common subsequence);
- `outputs`: the key's output fields that some Sub-Skill of the build declares;
- `precision`: the build's steps that the key knows (a build that invents steps scores lower).

The repository carries only a small synthetic key (`golden/chain-answer-key.json`). A real customer
key is private: pass its path with `--key`; it is read, never copied or logged. Only names and
ratios are printed.

    python -m sbam_workers.evals.chain --build build.json [--key /private/key.json] [--min 0.8]

Exit codes: 0 the score reached `--min`, 1 below it, 2 a file could not be read.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SYNTHETIC_KEY = Path(__file__).resolve().parents[3] / "evals" / "golden" / "chain-answer-key.json"


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass(frozen=True)
class ChainScore:
    steps: float
    order: float
    outputs: float
    precision: float
    missing_steps: tuple[str, ...]
    extra_steps: tuple[str, ...]
    missing_outputs: tuple[str, ...]

    @property
    def total(self) -> float:
        return round((self.steps + self.order + self.outputs + self.precision) / 4, 4)


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            table[i + 1][j + 1] = table[i][j] + 1 if x == y else max(table[i][j + 1], table[i + 1][j])
    return table[len(a)][len(b)]


def _ratio(hit: int, total: int) -> float:
    return 1.0 if total == 0 else hit / total


def score(build: dict[str, Any], key: dict[str, Any]) -> ChainScore:
    wanted = [norm(s["name"]) for s in sorted(key["steps"], key=lambda s: s["order"])]
    skills = sorted(build.get("sub_skills", []), key=lambda s: s["order"])
    got = [norm(s["name"]) for s in skills]
    have, known = set(got), set(wanted)
    outputs = {norm(o) for s in skills for o in s.get("outputs", [])}
    wanted_outputs = [norm(o) for o in key.get("outputs", [])]
    return ChainScore(
        steps=_ratio(sum(n in have for n in wanted), len(wanted)),
        order=_ratio(_lcs(wanted, got), len(wanted)),
        outputs=_ratio(sum(o in outputs for o in wanted_outputs), len(wanted_outputs)),
        precision=_ratio(sum(n in known for n in got), len(got)) if got else 0.0,
        missing_steps=tuple(n for n in wanted if n not in have),
        extra_steps=tuple(n for n in got if n not in known),
        missing_outputs=tuple(o for o in wanted_outputs if o not in outputs),
    )


def render(result: ChainScore) -> str:
    lines = [
        f"chain score {result.total:.4f}  steps {result.steps:.2f}  order {result.order:.2f}"
        f"  outputs {result.outputs:.2f}  precision {result.precision:.2f}"
    ]
    for label, names in (
        ("missing steps", result.missing_steps),
        ("extra steps", result.extra_steps),
        ("missing outputs", result.missing_outputs),
    ):
        if names:
            lines.append(f"{label}: {', '.join(names)}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a build against an answer key")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--key", type=Path, default=SYNTHETIC_KEY, help="a private key path")
    parser.add_argument("--min", type=float, default=0.8, dest="minimum")
    args = parser.parse_args(argv)
    try:
        build = json.loads(args.build.read_text(encoding="utf-8"))
        key = json.loads(args.key.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"could not read an input: {type(exc).__name__}", file=sys.stderr)
        return 2
    result = score(build, key)
    print(render(result))
    return 0 if result.total >= args.minimum else 1


if __name__ == "__main__":
    raise SystemExit(main())
