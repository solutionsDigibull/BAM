# LLM golden sets (E15.5)

Golden sets for the six LLM tasks (TD §19.1). A set is synthetic data plus deterministic checks on a
model's answer. Nothing in this folder calls a model.

| Path | What |
|---|---|
| `golden/tasks.json` | every task, its `sbam-*` alias, the prompt ids it uses, and whether its set is `ready` or `todo` |
| `golden/<task>.json` | the items of a ready task (at least 20) |
| `baselines/<task>.json` | the last accepted score of a task, with the prompt versions it was measured on |
| `gate.json` | the threshold (the largest drop that still passes) and the runs averaged per item |

## Status

| Task | Set |
|---|---|
| `ocr` | **ready**, 20 items, baseline recorded |
| `testgen` | **ready**, 20 items, **no baseline yet** (see below) |
| `sow`, `isatvon`, `codegen`, `correction` | `todo`: each lands in its own change |

## Running the gate

```
uv run python -m sbam_workers.evals.gate --task ocr            # compare with the baseline
uv run python -m sbam_workers.evals.gate --task ocr --record   # write a baseline, to be reviewed
uv run python -m sbam_workers.evals.gate --task testgen        # the same for test generation
```

It sends each page (OCR) or Sub-Skill (test generation) through the production path (`ocr.read_page`
or `testgen.generate`, each over `LlmClient`, through Bifrost) and needs a reachable Bifrost with a working provider key. A full run is 60 calls
(20 items x 3 runs), about $0.30 with the current OCR model. Exit codes: **0** PASS, **1** REGRESSED
(the score fell by more than `gate.json`'s threshold), **2** INFRA_ERROR (the provider failed, or the
baseline could not be written: never a pass), **3** NO_BASELINE (a new task: record and review one).

The first OCR baseline (score 1.0, 20 items, 3 runs, prompt `ocr-page/1.0.0`) was recorded on
2026-10-30. Not built yet: pushing the scores to Langfuse datasets, and the CI job.

### Test generation: no baseline yet, on purpose

An item scores three checks: the model produced cases the API accepts, it needed no re-ask, and the
cases use every required input. Each of the 20 items is a complete invented Sub-Skill (five domains
at four iteration bounds); every part of a variant describes its own domain, and a test checks that
no login text is left in the others.

The gate found three problems before any baseline could be accepted, and recording a score now
would make it guard a broken state:

1. **The set itself** (fixed with this set): its first version swapped only the tables and inputs of
   the login sample, so the model read two stories and wrote seed SQL into a table the Sub-Skill did
   not declare.
2. **The prompt** (`testgen/1.0.0`) never lists the allowed assertion types, so the model invents
   one and its answer is refused; most failures on the corrected set involve `/assertions/N/type`.
3. **The seed-SQL check** refuses an ordinary `WHERE a = 'x' AND b = 'y'` as a "function call".

The first testgen baseline is recorded once the fixes for 2 and 3 are in.

## Rules

- **Synthetic only.** An item has `"synthetic": true` and nothing else is accepted. Never put a real
  document, tenant, person or credential in a set.
- **At least 20 items per ready task**, with unique ids. `check_golden` (run by
  `tests/unit/test_e15_5_golden_sets.py`) fails the build for a ready task with fewer, and for a task
  marked ready whose items cannot be loaded.
- **Deterministic checks first.** An item names what must hold (for OCR: every identifier comes back
  exactly as printed). A rubric judged by a model comes after, never instead.
- **A new task has no baseline.** Its first run records one, and a person reviews it before it
  counts. Changing a threshold also needs review.

## OCR set

`golden/ocr.json` is written by `uv run python -m sbam_workers.evals.ocr_set`; a test fails if the
file and the generator disagree. An item holds a render spec (text lines and how the page is spoiled:
rotation, blur, speckle, uneven light, half resolution, JPEG), not an image. `evals/render.py` draws
the page when the gate runs, so the repo holds no binaries.

## Add a task

1. Add the items in `golden/<task>.json` and a loader and checks in `sbam_workers/evals/`.
2. Set the task to `ready` in `tasks.json`. `check_golden` then holds it to 20 items.
3. Tests for the loader and the checks go next to `test_e15_5_golden_sets.py`.
