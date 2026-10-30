"""Spike E0.9 test runner: T01, T01b, T03, T04, T05 (T02 is verify_bundle; T06/T07 run from the host).

    python -m spike.tests          (after python -m spike.build)
Writes /spike/out/results.json (numbers + method) used by docs/spikes/cognee-okf.md.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import urllib.request
from pathlib import Path

import cognee
from cognee import SearchType

from spike import okf
from spike.build import (ROOT_ID, WORK, dataset_graph, dataset_name, load_docs)
from spike.gen_fixtures import PROJECTS, TENANT_ID

OUT = Path("/spike/out")
MARK = {"A": "ZEBRA-7731", "B": "QUOKKA-4419"}
SECTIONS = ["overview", "users and authority", "scope", "modules", "features", "data and integrations",
            "rules and constraints", "deliverables and acceptance", "assumptions and open points"]


def hits(res, marker: str) -> int:
    return sum(marker.lower() in json.dumps(r, default=str).lower() for r in res)


async def search_chunks(q: str, ds: str, user, top_k: int = 10):
    out = await cognee.search(q, query_type=SearchType.CHUNKS, datasets=[ds], user=user, top_k=top_k)
    return [item for r in out for item in (r.get("search_result", []) if isinstance(r, dict) else [r])]


async def t01_isolation(user) -> dict:
    a, b = (dataset_name(TENANT_ID, PROJECTS[k]) for k in "AB")
    res = {}
    for scope, other in (("A", "B"), ("B", "A")):
        ds = a if scope == "A" else b
        own = await search_chunks(MARK[scope], ds, user)
        cross = await search_chunks(MARK[other], ds, user)
        res[f"{scope}_sees_own_marker"] = hits(own, MARK[scope])
        res[f"{scope}_sees_{other}_marker"] = hits(cross, MARK[other])
    _, nodes_b, _ = await dataset_graph(b, user)
    a_ids = set(load_docs("A"))
    res["B_graph_nodes_with_A_document_id"] = sum(any(d in json.dumps(p, default=str) for d in a_ids) for _, p in nodes_b)
    # Weak-isolation probe: a search with NO dataset restriction by a user that owns both datasets.
    unscoped = await cognee.search(MARK["B"], query_type=SearchType.CHUNKS, user=user, top_k=10)
    res["UNSCOPED_search_for_B_marker_hits"] = hits(unscoped, MARK["B"])
    res["pass"] = (res["A_sees_B_marker"] == 0 and res["B_sees_A_marker"] == 0
                   and res["B_graph_nodes_with_A_document_id"] == 0 and res["A_sees_own_marker"] > 0)
    return res


async def t01b_per_project_users() -> dict:
    """Stronger isolation: each project has its own Cognee user; cross-user dataset access must fail."""
    from cognee.modules.users.methods import create_user
    p1 = await create_user("proj-a@example.com", "spike-pass-1", is_verified=True)
    p2 = await create_user("proj-b@example.com", "spike-pass-2", is_verified=True)
    d1, d2 = "sbam_spike_iso_a", "sbam_spike_iso_b"
    await cognee.add("Isolation canary alpha PINEAPPLE-1111.", dataset_name=d1, user=p1)
    await cognee.add("Isolation canary beta MANGO-2222.", dataset_name=d2, user=p2)
    await cognee.cognify(datasets=[d1], user=p1)
    await cognee.cognify(datasets=[d2], user=p2)
    res = {"p1_own": hits(await search_chunks("PINEAPPLE-1111", d1, p1), "PINEAPPLE-1111"),
           "p2_own": hits(await search_chunks("MANGO-2222", d2, p2), "MANGO-2222")}
    for who, user, ds, marker in (("p2_reads_p1_dataset", p2, d1, "PINEAPPLE-1111"),
                                  ("p1_reads_p2_dataset", p1, d2, "MANGO-2222")):
        try:
            out = await search_chunks(marker, ds, user)
            res[who] = f"returned {hits(out, marker)} hits"
        except Exception as e:  # permission error expected
            res[who] = f"DENIED {type(e).__name__}"
    unscoped = await cognee.search("PINEAPPLE-1111", query_type=SearchType.CHUNKS, user=p2, top_k=10)
    res["p2_UNSCOPED_search_for_p1_marker_hits"] = hits(unscoped, "PINEAPPLE-1111")
    def no_leak(outcome: str) -> bool:
        return outcome.startswith("DENIED") or outcome == "returned 0 hits"

    res["pass"] = (res["p1_own"] > 0 and res["p2_own"] > 0
                   and res["p2_UNSCOPED_search_for_p1_marker_hits"] == 0
                   and no_leak(res["p2_reads_p1_dataset"]) and no_leak(res["p1_reads_p2_dataset"]))
    return res


def t03_provenance() -> dict:
    d = OUT / "okf-bundle"
    ents = [json.loads(x) for x in (d / "entities.jsonl").read_text(encoding="utf-8").splitlines()]
    rels = [json.loads(x) for x in (d / "relations.jsonl").read_text(encoding="utf-8").splitlines()]

    def ok(r):
        return bool(r["source_refs"]) and all(s.get("document_version_id") and s.get("location") for s in r["source_refs"])
    doc_level = sum(any(s["location"].get("scope") == "document" for s in e["source_refs"]) for e in ents)
    return {"entities": len(ents), "relations": len(rels),
            "entities_with_valid_ref": sum(map(ok, ents)), "relations_with_valid_ref": sum(map(ok, rels)),
            "entities_with_document_level_fallback_ref": doc_level,
            "pass": all(map(ok, ents)) and all(map(ok, rels))}


def t04_master_injection() -> dict:
    g = json.loads((WORK / "graph_A.json").read_text())
    nodes = {n[0]: n[1] for n in g["nodes"]}
    names = {i: p.get("name", "") for i, p in nodes.items() if p.get("type") == "Entity"}
    org = [i for i, n in names.items() if ROOT_ID.lower() in n.lower()]
    root = org[0] if org else None
    masters = [json.loads(x) for x in (OUT / "okf-bundle" / "masters.jsonl").read_text(encoding="utf-8").splitlines()]
    node_ids = [m["node_id"].lower() for m in masters if m["master_type"] != "ORGANIZATION"]
    present = sum(any(nid in n.lower() for n in names.values()) for nid in node_ids)
    edge_pairs = {(a, b) for a, b, lab, *_ in g["edges"] if lab not in okf.STRUCTURAL_EDGES}
    to_root = sum(1 for a, b in edge_pairs if b == root) + sum(1 for a, b in edge_pairs if a == root)
    return {"root_entity_found": bool(root), "root_entity_names": [names[i] for i in org][:3],
            "master_nodes_expected": len(node_ids), "master_nodes_found_as_graph_entities": present,
            "non_structural_edges_touching_root": to_root,
            "illustrative_excluded": [x for x in ("ORG-CORE", "RULE-CORE", "SK-CORE")
                                      if any(x.lower() in n.lower() for n in names.values())],
            "pass": bool(root) and to_root > 0}


def embed(texts: list[str]) -> list[list[float]]:
    req = urllib.request.Request("http://bifrost:8080/v1/embeddings", json.dumps(
        {"model": "openrouter/openai/text-embedding-3-small", "input": texts}).encode(),
        {"Content-Type": "application/json", "Authorization": "Bearer ."})
    with urllib.request.urlopen(req, timeout=120) as r:
        return [d["embedding"] for d in json.loads(r.read())["data"]]


def cos(a, b) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def t05_size_vs_context() -> dict:
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    d = OUT / "okf-bundle"
    parts = {n: len(enc.encode((d / n).read_text(encoding="utf-8"))) for n in okf.PARTS}
    chunk_rows = [json.loads(x) for x in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    text_chars = sum(len(c["text"]) for c in chunk_rows)
    chunk_tokens = sum(len(enc.encode(c["text"])) for c in chunk_rows)
    tok_per_char = chunk_tokens / text_chars
    # Fixed JSON wrapper cost per chunk (id, document_version_id, location), measured on the fixture chunks.
    wrapper_per_chunk = (parts["chunks.jsonl"] - chunk_tokens) / len(chunk_rows)
    # Graph records are NOT scaled: on tiny fixtures their size is dominated by fixed per-record cost (ids,
    # source_refs), so any ratio to text size would be meaningless. Report measured size per record instead.
    n_graph_records = max(1, sum(1 for n in ("entities.jsonl", "relations.jsonl")
                                 for _ in (d / n).read_text(encoding="utf-8").splitlines()))
    graph_tokens = parts["entities.jsonl"] + parts["relations.jsonl"]
    masters_tokens = parts["masters.jsonl"]  # constant per tenant, independent of corpus size
    # Nothing is extrapolated to a larger corpus (E0.18-T03): sizing needs a real corpus, not a ratio from one fixture.
    # exercise ranking path: force a tiny budget on the real fixture and record the selection per section
    budget = 100  # must be below the whole fixture corpus (~300 tokens) or ranking is not actually forced
    cvecs = embed([c["text"] for c in chunk_rows])
    selection = {}
    for s, qv in zip(SECTIONS, embed(SECTIONS), strict=True):
        ranked = sorted(zip(chunk_rows, cvecs), key=lambda cv: (-cos(qv, cv[1]), cv[0]["id"]))
        used, picked = 0, []
        for c, _ in ranked:
            t = len(enc.encode(c["text"]))
            if used + t <= budget:
                picked.append(c["id"]); used += t
        selection[s] = {"chunks": picked, "tokens": used}
    return {"method": "tiktoken cl100k_base on the bundle files and on the chunk text of this one fixture; measured "
                      "values only, nothing extrapolated to a larger corpus",
            "bundle_tokens_by_part": parts, "bundle_tokens_total": sum(parts.values()),
            "chunk_text_characters": text_chars, "chunk_text_tokens": chunk_tokens,
            "tokens_per_char": round(tok_per_char, 4), "wrapper_tokens_per_chunk": round(wrapper_per_chunk, 1),
            "graph_tokens_measured": graph_tokens,
            "graph_tokens_per_record_measured": round(graph_tokens / n_graph_records, 1),
            "masters_tokens": masters_tokens,
            "ranking_budget_tokens": budget, "ranking_selection": selection}


async def main() -> None:
    from cognee.modules.users.methods import get_default_user
    user = await get_default_user()
    results = {"T01": await t01_isolation(user), "T01b": await t01b_per_project_users(),
               "T03": t03_provenance(), "T04": t04_master_injection(), "T05": t05_size_vs_context()}
    (OUT / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for k, v in results.items():
        brief = {kk: vv for kk, vv in v.items() if kk not in ("ranking_selection", "bundle_tokens_by_part")}
        print(f"\n== {k}:", json.dumps(brief, ensure_ascii=False)[:900])


if __name__ == "__main__":
    asyncio.run(main())
