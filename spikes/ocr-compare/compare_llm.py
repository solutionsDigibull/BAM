"""The vision model (the production `ocr.read_page` path) over a folder of scans, in the worker
container. Prints counts, tokens and cost, never the page text.

    python compare_llm.py <folder> <results.json>
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import httpx
from common import FILES, ids_of, scaled, score_ids
from sbam_api.llm.models import LlmCall, LlmDerivative
from sbam_contracts.pipeline import StageRef
from sbam_workers.ingest import ocr
from sbam_workers.ingest.ocr import OcrDeps, OcrRun
from sbam_workers.ingest.rasterize import open_source
from sbam_workers.llm.client import LlmClient
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

folder, out = Path(sys.argv[1]), Path(sys.argv[2])
engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
                       execution_options={"schema_translate_map": {"sbam": None}})
LlmCall.metadata.create_all(engine, tables=[LlmCall.__table__, LlmDerivative.__table__])
sessions = sessionmaker(bind=engine)
conf = WorkerSettings(_env_file=None, database_url="sqlite://", llm_timeout_seconds=120)


async def main() -> None:
    client = LlmClient(conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient())
    deps = OcrDeps(sessions=sessions, storage=None, reader=client)
    rows = []
    for path in FILES(folder):
        key = ids_of(path)
        for variant, factor in (("full", None), ("half", 0.5)):
            png = scaled(path, factor)
            stage = StageRef(tenant_id=uuid.uuid4(), project_id=uuid.uuid4(), run_id=uuid.uuid4(),
                             correlation_id="ocr-real", ref_id=uuid.uuid4())
            run = OcrRun(f"wf-{uuid.uuid4()}", f"act-{uuid.uuid4().hex[:6]}", 1, "run-real")
            source = open_source(png, "png")
            before = {r.id for r in sessions().query(LlmCall).all()}
            t0 = time.time()
            try:
                page = await ocr.read_page(deps, stage, run, source, 1)
            finally:
                source.close()
            seconds = time.time() - t0
            with sessions() as s:
                new = [r for r in s.query(LlmCall).all() if r.id not in before and r.status == "SUCCEEDED"]
            tokens_in = sum(r.tokens_in or 0 for r in new)
            tokens_out = sum(r.tokens_out or 0 for r in new)
            cost = sum(float(r.cost_usd or 0) for r in new)
            hit, total, missing = score_ids(page.markdown, key)
            rows.append({"file": path.name, "engine": "vision", "variant": variant, "ids_found": hit,
                         "ids_total": total, "seconds": round(seconds, 2), "tokens_in": tokens_in,
                         "tokens_out": tokens_out, "cost_usd": round(cost, 5), "chars": len(page.markdown),
                         "readable": bool(page.markdown)})
            print(path.name, variant, f"ids {hit}/{total}", f"{seconds:.1f}s", f"in={tokens_in}",
                  f"cost=${cost:.4f}", "missing:", missing, flush=True)
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")


asyncio.run(main())
