"""E1.17: dev workers restart on code change; nothing of it reaches the deployed images or overlays.

The restart itself (T01) is manual and recorded in the PR; these pin the configuration.
"""

from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "compose"
WORKERS = ("ingest", "knowledge", "llm", "build", "test", "certify")
WATCH = [
    "watchfiles",
    "--filter",
    "python",
    "python -m sbam_workers.bootstrap",
    "/app/server/api",
    "/app/server/workers",
    "/app/server/contracts",
]


def override() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((COMPOSE / "compose.override.yaml").read_text("utf-8"))
    return data


def test_e1_17_every_dev_worker_runs_the_bootstrap_under_the_watcher() -> None:
    services = override()["services"]
    for queue in WORKERS:
        worker = services[f"worker-{queue}"]
        assert worker["command"] == WATCH, queue
        assert worker["volumes"] == ["../server:/app/server"], queue  # watches the mounted source
        assert worker["environment"]["WATCHFILES_FORCE_POLLING"] == "true", queue  # Docker Desktop
        assert worker["init"] is True, queue  # `docker compose stop` reaches the worker


def test_e1_17_the_api_and_web_keep_their_own_reload() -> None:
    services = override()["services"]
    assert "command" not in services["sbam-api"]  # uvicorn --reload (dev image target)
    assert "command" not in services["sbam-web"]  # next dev


def test_e1_17_t03_no_watcher_in_deployed_overlays_or_the_prod_image() -> None:
    for overlay in sorted(COMPOSE.glob("compose*.yaml")):
        if overlay.name == "compose.override.yaml":
            continue  # laptops only; `make up` loads it, deploys never do
        text = overlay.read_text("utf-8")
        assert "watchfiles" not in text, overlay.name
        assert "../server:/app/server" not in text, overlay.name  # no source mount
    dockerfile = (COMPOSE / "docker" / "server.Dockerfile").read_text("utf-8")
    prod = dockerfile.split("FROM base AS prod", 1)[1]
    assert "watchfiles" not in prod and "--reload" not in prod
