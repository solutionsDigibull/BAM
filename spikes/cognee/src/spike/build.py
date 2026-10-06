"""Build both spike projects in Cognee through Bifrost and export OKF bundles (issue #26).

    python -m spike.build            # prune spike stores, ingest A and B, write bundles + metrics
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path

import cognee

from spike import okf
from spike.gen_fixtures import PROJECTS, TENANT_ID
from spike.masters import WorkbookMockSource, dump_masters_jsonl, to_master_context

FIXTURES = Path("/spike/fixtures")
OUT = Path("/spike/out")
WORK = Path("/data/cognee/work")
WORKBOOK = "/spike/reference/BuLLM - Admin Masters (One Sheet) - Consolidated.xlsx"
DATASET_RE = re.compile(r"^sbam_[0-9a-f-]{36}_[0-9a-f-]{36}$")
ROOT_ID = "ORG-EMS"


class ScopeError(ValueError):
    pass


def dataset_name(tenant_id: str, project_id: str) -> str:
    """E5.1: derived server-side from validated UUIDs only, never from user input."""
    name = f"sbam_{tenant_id}_{project_id}"
    if not DATASET_RE.fullmatch(name):
        raise ScopeError(f"invalid tenant/project id: {tenant_id!r}/{project_id!r}")
    return name


def load_docs(key: str) -> dict[str, okf.Document]:
    folders = sorted((FIXTURES / f"project_{key.lower()}").iterdir())
    docs = [okf.Document.load(f) for f in folders]
    return {d.document_version_id: d for d in docs}


def masters_context(project_id: str):
    snap = WorkbookMockSource(WORKBOOK).get_snapshot(TENANT_ID, project_id)
    rows, report = to_master_context(snap, root_id=ROOT_ID)
    return snap, rows, report


def masters_text(rows: list[dict], report: dict) -> str:
    """Master context as a Cognee document. Parentless entries are stated as belonging to the root org
    (the NO_PARENT policy under test; recorded as a recommendation for #59)."""
    lines = ["# Organization master context", f"Organization {ROOT_ID} is the root organization of this tenant graph."]
    for r in rows:
        p = r["payload"]
        label = next((v for v in (p.get("Org Name"), p.get("Skill"), p.get("Agent Name"), p.get("Domain Rule")) if v), "")
        parent = r["parent_id"] or (ROOT_ID if r["master_type"] != "ORGANIZATION" else None)
        line = f"{r['node_type']} {r['node_id']} ({r['master_type'].lower()}): {label[:140]}"
        lines.append(line + (f" It belongs to organization {parent}." if parent else " It is an organization root."))
    return "\n\n".join(lines)


async def dataset_graph(name: str, user):
    from cognee.context_global_variables import set_database_global_context_variables
    from cognee.infrastructure.databases.graph import get_graph_engine
    from cognee.modules.data.methods import get_authorized_existing_datasets

    ds = (await get_authorized_existing_datasets([name], "read", user))[0]
    async with set_database_global_context_variables(ds.id, ds.owner_id):
        engine = await get_graph_engine()
        nodes, edges = await engine.get_graph_data()
    return ds, nodes, edges


async def ingest_project(key: str, user) -> dict:
    project_id = PROJECTS[key]
    ds_name = dataset_name(TENANT_ID, project_id)
    docs = load_docs(key)
    stage = Path("/tmp/stage") / key
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    failed: dict[str, str] = {}
    t0 = time.time()
    for dvid, doc in docs.items():
        f = stage / f"{dvid}.md"  # Cognee document name == file stem == document_version_id
        f.write_bytes(doc.text.encode("utf-8"))
        try:
            await cognee.add(str(f), dataset_name=ds_name, user=user)
        except Exception as e:  # E5.2: one failing document must not abort the others
            failed[dvid] = f"{type(e).__name__}: {e}"[:200]
    snap, rows, report = masters_context(project_id)
    mfile = stage / "masters-context.md"
    mfile.write_text(masters_text(rows, report), encoding="utf-8")
    await cognee.add(str(mfile), dataset_name=ds_name, user=user)
    await cognee.cognify(datasets=[ds_name], user=user)
    return {"key": key, "dataset": ds_name, "docs": docs, "failed": failed, "snap": snap, "rows": rows,
            "report": report, "seconds": round(time.time() - t0, 1)}


async def main() -> None:
    from cognee.modules.engine.operations.setup import setup
    from cognee.modules.users.methods import get_default_user

    await cognee.prune.prune_data()
    await cognee.prune.prune_system(metadata=True)
    await setup()
    user = await get_default_user()
    WORK.mkdir(parents=True, exist_ok=True)
    summary = {}
    for key in ("A", "B"):
        r = await ingest_project(key, user)
        ds, nodes, edges = await dataset_graph(r["dataset"], user)
        (WORK / f"graph_{key}.json").write_text(json.dumps({"nodes": nodes, "edges": edges}, default=str))
        out = OUT / "okf-bundle" if key == "A" else WORK / "okf-bundle-B"
        shutil.rmtree(out, ignore_errors=True)
        res = okf.write_bundle(out, project_id=PROJECTS[key], workflow_id=None, knowledge_version=1, docs=r["docs"],
                               graph_nodes=nodes, graph_edges=edges, master_rows=r["rows"],
                               master_snapshot_ref=r["snap"].ref(), failed=r["failed"])
        problems = okf.verify_bundle(out, r["docs"])
        summary[key] = {"dataset": r["dataset"], "seconds": r["seconds"], "graph_nodes": len(nodes),
                        "graph_edges": len(edges), "counts": res["counts"], "graph_stats": res["graph_stats"],
                        "failed": r["failed"], "okf_sha256": res["manifest"]["okf_sha256"], "problems": problems,
                        "coverage": {k: v["status"] for k, v in res["coverage"]["documents"].items()}}
    (WORK / "build_summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    sys.exit(0 if all(not s["problems"] for s in summary.values()) else 1)


if __name__ == "__main__":
    asyncio.run(main())
