"""T07: Bifrost stopped mid-build. Run from the host while `docker compose stop bifrost` fires ~8 s in.

Expected (issue #26): cognify fails with a retryable error, and no bundle is marked complete.
Prints one JSON line per observation so the host script can capture it.
"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import cognee

from spike import okf
from spike.build import dataset_name, load_docs, masters_context
from spike.gen_fixtures import PROJECTS, TENANT_ID

OUT = Path("/data/cognee/work/t07-bundle")


def emit(**kw) -> None:
    print("T07 " + json.dumps(kw, default=str), flush=True)


async def main() -> None:
    from cognee.modules.users.methods import get_default_user

    user = await get_default_user()
    ds = dataset_name(TENANT_ID, PROJECTS["A"]).replace("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                                                        "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    docs = load_docs("A")
    stage = Path("/tmp/stage_t07")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    for dvid, d in docs.items():
        (stage / f"{dvid}.md").write_bytes(d.text.encode("utf-8"))
        await cognee.add(str(stage / f"{dvid}.md"), dataset_name=ds, user=user)
    shutil.rmtree(OUT, ignore_errors=True)
    emit(event="cognify_start", t=time.time())
    t0 = time.time()
    outcome = {"completed": False}
    try:
        await cognee.cognify(datasets=[ds], user=user)
        outcome["completed"] = True
    except BaseException as e:  # noqa: BLE001 - we want to record whatever the failure looks like
        outcome.update(error_type=type(e).__name__, error=str(e)[:300], seconds_to_fail=round(time.time() - t0, 1))
    emit(event="cognify_end", **outcome)
    # A bundle may only be written after a successful cognify (the build.py order). Mirror that rule here.
    if outcome["completed"]:
        snap, rows, _ = masters_context(PROJECTS["A"])
        okf.write_bundle(OUT, project_id=PROJECTS["A"], workflow_id=None, knowledge_version=1, docs=docs,
                         graph_nodes=[], graph_edges=[], master_rows=rows, master_snapshot_ref=snap.ref())
    emit(event="bundle_written", written=OUT.exists())
    try:
        status = await cognee.datasets.get_status([(await _dataset_id(ds, user))])
        emit(event="pipeline_status", status=status)
    except Exception as e:  # noqa: BLE001
        emit(event="pipeline_status_unavailable", error=f"{type(e).__name__}: {e}"[:200])


async def _dataset_id(name, user):
    from cognee.modules.data.methods import get_authorized_existing_datasets
    return (await get_authorized_existing_datasets([name], "read", user))[0].id


if __name__ == "__main__":
    asyncio.run(main())
