import asyncio, os, sys, uuid, tempfile
from pathlib import Path
for k, v in dict(ENABLE_BACKEND_ACCESS_CONTROL="true", DATA_ROOT_DIRECTORY="/tmp/audit-cognee/data", SYSTEM_ROOT_DIRECTORY="/tmp/audit-cognee/system",
    LLM_ENDPOINT="http://127.0.0.1:8091/v1", LLM_MODEL="openai/sbam-knowledge-primary", LLM_API_KEY="sbam-local", EMBEDDING_PROVIDER="openai_compatible",
    EMBEDDING_ENDPOINT="http://127.0.0.1:8091/v1", EMBEDDING_MODEL="sbam-embed-primary", EMBEDDING_API_KEY="sbam-local", EMBEDDING_DIMENSIONS="1536",
    HF_HUB_OFFLINE="1", COGNEE_SKIP_CONNECTION_TEST="true", TELEMETRY_DISABLED="1", COGNEE_USER_SECRET="change-me-cognee-user-secret-0000000000").items(): os.environ.setdefault(k, v)
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import ItemSource, KnowledgeItem, ProjectScope
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, dataset_name, DatasetNotFound
from sbam_workers.knowledge.cognee_backend import CogneeLibrary
class Allow:
    def project_in_tenant(self, s): return True
    def eligible_versions(self, s, ids): return ids
    def eligible_master_documents(self, s, ids): return ids
T = uuid.uuid4(); P1, P2 = uuid.uuid4(), uuid.uuid4()
S1, S2 = ProjectScope(tenant_id=T, project_id=P1), ProjectScope(tenant_id=T, project_id=P2)
st = LocalDiskBackend(Path(tempfile.mkdtemp(dir="/tmp/audit-cognee/store")))
lib = CogneeLibrary(); ad = CogneeAdapter(lib, Allow(), st, os.environ["COGNEE_USER_SECRET"])
def item(sc, text):
    o = st.put(text.encode(), StorageScope(sc.tenant_id, sc.project_id), Kind.DERIVED, "text/markdown")
    return KnowledgeItem(source=ItemSource.DOCUMENT, source_id=uuid.uuid4(), sha256=o.sha256, storage_key=o.storage_key)
def say(*a): print(*a, flush=True)
async def main():
    A = item(S1, "Document A. Alice Johnson earns a salary of 250000 dollars at Acme-Corp. Alice leads the Zephyr-Project.")
    B = item(S1, "Document B. Bob Smith manages the Orion-Platform at Acme-Corp and reports to Carol Diaz.")
    C = item(S1, "Document C. Dana Lee owns the Vega-Service at Acme-Corp.")
    await ad.ensure_dataset(S1); r = await ad.add(S1, [A, B]); say("build1 add:", r)
    await ad.cognify(S1) if hasattr(ad, "cognify") else None
    n1, e1 = await lib.graph(ad._principal(S1), dataset_name(S1)); say("build1 graph nodes/edges:", len(n1), len(e1))
    hits = await ad.search(S1, "Alice salary", 5) if hasattr(ad, "search") else []
    say("search 'Alice salary' hits:", len(hits), "| mentions Alice:", any("Alice" in (getattr(h, 'text', '') or str(h)) for h in hits))
    # build 2: document A deleted by the FDE, C added
    r2 = await ad.add(S1, [B, C]); say("build2 add (A removed from scope, C new):", r2)
    await ad.cognify(S1)
    names = await lib.item_names(ad._principal(S1), dataset_name(S1)); say("items in dataset after build2:", len(names))
    hits2 = await ad.search(S1, "Alice salary", 5)
    say("F-27 LIVE: after A was removed from scope, search still returns A:", any("Alice" in (getattr(h, 'text', '') or str(h)) for h in hits2))
    n2, _ = await lib.graph(ad._principal(S1), dataset_name(S1)); say("graph still has Alice nodes:", any("Alice" in str(n) for n in n2))
    # isolation: project 2's principal reads project 1's dataset
    await ad.ensure_dataset(S2)
    try:
        got = await lib.item_names(ad._principal(S2), dataset_name(S1)); say("ISOLATION VULN: project 2 user read project 1 items:", len(got))
    except DatasetNotFound: say("OK isolation: project 2 user cannot read project 1's dataset (DatasetNotFound)")
    except Exception as e: say("isolation: refused with", type(e).__name__)
    # default-secret derivation
    p1 = ad._principal(S1); other = CogneeAdapter(lib, Allow(), st, "change-me-cognee-user-secret-0000000000")._principal(S1)
    say("default secret derives the same project password as the deployed adapter:", p1.password == other.password, "| email:", p1.email)
    # purge cleanup (F-26): delete_dataset removes content? and what remains
    await ad.delete_dataset(S1)
    try: left = await lib.item_names(ad._principal(S1), dataset_name(S1)); say("after delete_dataset items left:", len(left))
    except DatasetNotFound: say("after delete_dataset: dataset not found")
asyncio.run(main())
