"""Live check for #291: a master document reaches the real Cognee (worker-knowledge) as master context.

Run inside worker-knowledge so Cognee uses its real config, volume and Bifrost gateway:

    docker compose --env-file compose/.env -f compose/compose.yaml -f compose/compose.override.yaml \
        exec -T worker-knowledge python - < scripts/live_master_document_cognee.py

It adds one tenant-wide and one project master document to a throwaway project, cognifies, searches for
a canary in each, checks another project of the same tenant does not see them, then deletes its datasets.
The database guard is replaced by an allow-all stub (it is covered by the Postgres tests); everything
else is the real adapter and the real Cognee."""

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import ItemSource, KnowledgeItem, ProjectScope
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter
from sbam_workers.knowledge.cognee_backend import CogneeLibrary

TENANT, MINE, OTHER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
TENANT_WIDE = "Company policy: every quote is valid for 14 days. Canary WOMBAT-5521."
PROJECT_OWN = "Project rule: only the approved vendor list applies. Canary NARWHAL-8830."


class Allow:
    def project_in_tenant(self, scope: ProjectScope) -> bool:
        return True

    def eligible_versions(self, scope: ProjectScope, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return ids

    def eligible_master_documents(self, scope: ProjectScope, ids: list[uuid.UUID]) -> list[uuid.UUID]:
        return ids


def scope(project: uuid.UUID) -> ProjectScope:
    return ProjectScope(tenant_id=TENANT, project_id=project)


async def main() -> int:
    storage = LocalDiskBackend(Path(tempfile.mkdtemp()))
    adapter = CogneeAdapter(CogneeLibrary(), Allow(), storage, os.environ["COGNEE_USER_SECRET"])
    items = []
    for text, where in ((TENANT_WIDE, uuid.UUID(int=0)), (PROJECT_OWN, MINE)):  # tenant-wide, own project
        obj = storage.put(text.encode(), StorageScope(TENANT, where), Kind.DERIVED, "text/markdown")
        items.append(
            KnowledgeItem(
                source=ItemSource.MASTER_DOCUMENT,
                source_id=uuid.uuid4(),
                sha256=obj.sha256,
                storage_key=obj.storage_key,
            )
        )
    results: dict[str, bool] = {}
    try:
        report = await adapter.add(scope(MINE), items)
        print("add:", json.dumps(report.model_dump(), default=str))
        await adapter.cognify(scope(MINE))
        # the other project holds a different, cognified document, so searching it is allowed (an empty
        # dataset answers 404) and still must not return this project's canaries
        stranger = storage.put(
            b"Another project: cable harness supplier is Arrow. Canary OTTER-1177.",
            StorageScope(TENANT, OTHER),
            Kind.DERIVED,
            "text/markdown",
        )
        await adapter.add(
            scope(OTHER),
            [
                KnowledgeItem(
                    source=ItemSource.MASTER_DOCUMENT,
                    source_id=uuid.uuid4(),
                    sha256=stranger.sha256,
                    storage_key=stranger.storage_key,
                )
            ],
        )
        await adapter.cognify(scope(OTHER))
        own = [h.text for h in await adapter.search(scope(OTHER), "OTTER-1177", 10)]
        results["the other project finds its own OTTER-1177"] = any("OTTER-1177" in h for h in own)
        for canary in ("WOMBAT-5521", "NARWHAL-8830"):
            hits = [h.text for h in await adapter.search(scope(MINE), canary, 10)]
            results[f"{canary} found in the project"] = any(canary in h for h in hits)
            other = [h.text for h in await adapter.search(scope(OTHER), canary, 10)]
            results[f"{canary} NOT visible to another project"] = not any(canary in h for h in other)
        graph = await adapter.graph_page(scope(MINE), None, 500)
        results["the graph has nodes"] = graph.total_nodes > 0
    finally:
        for project in (MINE, OTHER):
            try:
                await adapter.delete_dataset(scope(project))
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the result
                print("cleanup:", type(exc).__name__)
    for name, ok in results.items():
        print(("PASS " if ok else "FAIL ") + name)
    return 0 if results and all(results.values()) else 1


raise SystemExit(asyncio.run(main()))
