"""What `sbam-sow` reads (E6.2, ADR 0001 §6): the project's OKF bundle, whole or ranked per section.

- FULL: every chunk, entity (with its relations) and the masters fit in the budget, so all of it
  is sent.
- RANKED: the bundle is too big. For each of the nine sections, chunks are scored by embedding
  similarity to a rich description of that section (not its title), plus a bonus for sharing an
  entity with a chunk already picked (graph proximity), minus a length penalty, and packed into the
  section's share of the budget. The chunks picked for any section are sent once, with the
  entities they mention. Which chunks each section got goes to `llm_calls.context_manifest`.

Every chunk and entity is sent with a header carrying the ids the model must cite, and `citable`
holds exactly those ids, so a citation of anything not sent is refused (`UNKNOWN_CITATION`).
Pure apart from `embed`, which the caller passes (Bifrost `sbam-embed`; a fake in tests).
"""

import heapq
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from sbam_contracts.okf import Location, OkfChunk, OkfEntity, OkfRelation
from sbam_contracts.sow import SECTIONS

CHARS_PER_TOKEN: Final = 4  # the same rough measure as the LLM client's size guard
# What the prompt, the JSON Schema and the answer need besides the documents.
RESERVED_TOKENS: Final = 40_000
GRAPH_BONUS: Final = 0.15
LENGTH_PENALTY: Final = 0.05
MAX_RELATIONS_PER_ENTITY: Final = 20

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]
Mode = Literal["FULL", "RANKED"]

# What each section is about, in words documents use. Ranking against these rather than the bare
# section titles is what ADR 0001 §6 asks for.
SECTION_QUERIES: Final[Mapping[str, str]] = {
    "overview": "client company customer project objective goal background problem today "
    "current process history why the project exists summary",
    "users_authority": "users roles operators planners engineers managers approvers who decides "
    "responsibility authority sign-off permissions escalation",
    "scope": "in scope out of scope included excluded boundaries phase what the system will and "
    "will not do requirements",
    "modules": "functional areas agents processes stages departments quoting planning "
    "scheduling inspection procurement production steps",
    "features": "when triggered the system shall check validate calculate generate send retry "
    "input output tool table success criteria handoff notification",
    "data_integrations": "data sources systems ERP MES database API integration import export "
    "file spreadsheet table fields interface",
    "rules_constraints": "rule constraint must not tolerance limit policy standard compliance "
    "approval threshold regulation RULE",
    "deliverables_acceptance": "deliverables acceptance criteria test sign-off go-live "
    "performance target measure success report",
    "assumptions_open_points": "assumption open question unknown to be confirmed TBD risk "
    "dependency pending decision",
}


@dataclass(frozen=True)
class Bundle:
    """The parts of a knowledge version the SOW is written from."""

    chunks: list[OkfChunk]
    entities: list[OkfEntity]
    relations: list[OkfRelation]
    masters: str = ""  # masters.jsonl as stored


@dataclass(frozen=True)
class SowContext:
    mode: Mode
    blocks: dict[str, str]  # data blocks for LlmClient.run
    citable: dict[str, frozenset[UUID]]  # okf_ref_id -> the document versions it may cite
    locations: dict[tuple[str, UUID], dict[str, Any]]  # the real location of each citable pair
    manifest: dict[str, Any] = field(default_factory=dict)  # LlmClient.run(manifest_extra=...)


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def citation_location(location: Location) -> dict[str, Any]:
    """The SowDocument location of an OKF location. A whole-document chunk has none, so it is
    cited as its first line."""
    out = {k: v for k, v in location.model_dump(exclude={"scope"}).items() if v is not None}
    return out or {"line": 1}


def _where(location: Location) -> str:
    return " ".join(f"{k}={v}" for k, v in citation_location(location).items())


def render_chunk(chunk: OkfChunk) -> str:
    return (
        f"[chunk {chunk.id} doc={chunk.document_version_id} {_where(chunk.location)}]\n"
        f"{chunk.text.strip()}"
    )


def render_entity(entity: OkfEntity, relations: list[str]) -> str:
    ref = entity.source_refs[0]
    head = (
        f"[entity {entity.id} doc={ref.document_version_id} {_where(ref.location)}] "
        f"{entity.type}: {entity.name}"
    )
    return "\n".join([head, *(f"  {r}" for r in relations[:MAX_RELATIONS_PER_ENTITY])])


def _relation_lines(bundle: Bundle) -> dict[str, list[str]]:
    names = {e.id: e.name for e in bundle.entities}
    lines: dict[str, list[str]] = {}
    for r in sorted(bundle.relations, key=lambda r: r.id):
        if r.from_ in names and r.to in names:
            lines.setdefault(r.from_, []).append(f"-[{r.type}]-> {names[r.to]}")
            lines.setdefault(r.to, []).append(f"<-[{r.type}]- {names[r.from_]}")
    return lines


def _usable(bundle: Bundle) -> list[OkfChunk]:
    """Chunks with text, in bundle order (an unreadable page has none to cite)."""
    return [c for c in bundle.chunks if c.text.strip() and not c.unreadable]


def _assemble(
    mode: Mode,
    chunks: list[OkfChunk],
    entities: list[OkfEntity],
    bundle: Bundle,
    hint: str | None,
    manifest: dict[str, Any],
) -> SowContext:
    relations = _relation_lines(bundle)
    blocks = {
        "chunks": "\n\n".join(render_chunk(c) for c in chunks) or "(none)",
        "entities": "\n".join(render_entity(e, relations.get(e.id, [])) for e in entities)
        or "(none)",
        "masters": bundle.masters.strip() or "(none)",
    }
    if hint:
        blocks["hint"] = hint
    citable: dict[str, set[UUID]] = {}
    locations: dict[tuple[str, UUID], dict[str, Any]] = {}
    for c in chunks:
        citable.setdefault(c.id, set()).add(c.document_version_id)
        locations[(c.id, c.document_version_id)] = citation_location(c.location)
    for e in entities:
        for ref in e.source_refs:
            citable.setdefault(e.id, set()).add(ref.document_version_id)
            locations.setdefault((e.id, ref.document_version_id), citation_location(ref.location))
    manifest = {
        "mode": mode,
        "chunks": len(chunks),
        "entities": len(entities),
        "tokens": sum(tokens(b) for b in blocks.values()),
        **manifest,
    }
    return SowContext(
        mode, blocks, {k: frozenset(v) for k, v in citable.items()}, locations, manifest
    )


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


async def build_context(
    bundle: Bundle, embed: Embed, *, budget_tokens: int, hint: str | None = None
) -> SowContext:
    """FULL when the whole bundle fits in `budget_tokens`, else RANKED per section."""
    chunks = _usable(bundle)
    full = _assemble("FULL", chunks, bundle.entities, bundle, hint, {})
    if full.manifest["tokens"] <= budget_tokens:
        return full

    vectors = await embed([SECTION_QUERIES[s] for s in SECTIONS] + [c.text for c in chunks])
    queries, chunk_vectors = vectors[: len(SECTIONS)], vectors[len(SECTIONS) :]
    entities_of: dict[str, set[str]] = {}
    chunks_of: dict[str, list[int]] = {}
    index = {c.id: i for i, c in enumerate(chunks)}
    for e in bundle.entities:
        for ref in e.source_refs:
            entities_of.setdefault(ref.chunk_id, set()).add(e.id)
            if ref.chunk_id in index:
                chunks_of.setdefault(e.id, []).append(index[ref.chunk_id])
    mean_len = sum(len(c.text) for c in chunks) / len(chunks)
    # Masters and the hint go whole; a quarter of the rest is for entities, the remainder is
    # shared equally between the sections.
    room = max(budget_tokens - tokens(full.blocks["masters"]) - tokens(hint or ""), len(SECTIONS))
    entity_room = room // 4
    share = (room - entity_room) // len(SECTIONS)

    picked_by_section: dict[str, list[str]] = {}
    picked: set[int] = set()
    for section, query in zip(SECTIONS, queries, strict=True):
        ids = _pick(chunks, chunk_vectors, query, entities_of, chunks_of, mean_len, share)
        picked_by_section[section] = [chunks[i].id for i in ids]
        picked.update(ids)

    selected = [chunks[i] for i in sorted(picked)]
    chosen = {c.id for c in selected}
    mentions = {e.id: sum(r.chunk_id in chosen for r in e.source_refs) for e in bundle.entities}
    relations = _relation_lines(bundle)
    entities: list[OkfEntity] = []
    used = 0
    for e in sorted(bundle.entities, key=lambda e: (-mentions[e.id], e.id)):
        if not mentions[e.id]:
            break
        cost = tokens(render_entity(e, relations.get(e.id, []))) + 1
        if used + cost <= entity_room:
            used += cost
            entities.append(e)
    entities.sort(key=lambda e: e.id)
    return _assemble(
        "RANKED",
        selected,
        entities,
        bundle,
        hint,
        {"budget_tokens": budget_tokens, "sections": picked_by_section},
    )


def _pick(
    chunks: list[OkfChunk],
    vectors: list[list[float]],
    query: list[float],
    entities_of: dict[str, set[str]],
    chunks_of: dict[str, list[int]],
    mean_len: float,
    share: int,
) -> list[int]:
    """Greedy: the best-scoring chunk that still fits, where a chunk sharing an entity with one
    already picked gets `GRAPH_BONUS`. Two heaps keep it O(n log n)."""
    base = [
        _cosine(query, v) - LENGTH_PENALTY * len(c.text) / mean_len
        for c, v in zip(chunks, vectors, strict=True)
    ]
    plain = [(-score, i) for i, score in enumerate(base)]
    heapq.heapify(plain)
    boosted: list[tuple[float, int]] = []
    near: set[int] = set()
    seen: set[str] = set()
    done: set[int] = set()
    chosen: list[int] = []
    used = 0
    while plain or boosted:
        heap = boosted if boosted and (not plain or boosted[0] <= plain[0]) else plain
        _, i = heapq.heappop(heap)
        if i in done:
            continue
        done.add(i)
        cost = tokens(render_chunk(chunks[i]))
        if used + cost > share:
            continue  # a shorter chunk may still fit
        used += cost
        chosen.append(i)
        for entity in entities_of.get(chunks[i].id, set()) - seen:
            seen.add(entity)
            for j in chunks_of.get(entity, []):
                if j not in done and j not in near:
                    near.add(j)
                    heapq.heappush(boosted, (-(base[j] + GRAPH_BONUS), j))
    return chosen
