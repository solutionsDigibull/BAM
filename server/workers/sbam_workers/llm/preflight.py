"""Startup self-check of the workers that call the LLM gateway, against Langfuse (E16.2, TD §17.2).

One Langfuse project per environment (`sbam-dev`, `sbam-staging`, `sbam-prod`), each with its own
key pair. A failure stops the worker before it polls (exit 3, `LANGFUSE_MISCONFIGURED`):

* staging/prod: LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required, and the
  host must be internal (a Compose service name or a private address; the UI is Netbird only);
* prod: LANGFUSE_LOG_CONTENT must stay false, so prompts and answers go out as sizes and hashes;
* keys set: they must belong to this environment's project, so staging traces never land in prod
  (E16.2-T04). A Langfuse that is down only costs a warning: tracing tolerates outages (T06).
In development a wrong project is a warning, and no keys simply means no traces. Messages name
variables, never their values.
"""

import ipaddress
import logging
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from sbam_api.core.settings import MOCK_FORBIDDEN_ENVS, _set
from sbam_contracts.runs import TaskQueue

from sbam_workers.registry import PreflightError, preflight_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.llm.preflight")
MISCONFIGURED = "LANGFUSE_MISCONFIGURED"
TIMEOUT_SECONDS = 3.0
PROD_ENVS = frozenset({"production", "prod"})
_SHORT = {"development": "dev", "production": "prod"}

Fetch = Callable[[WorkerSettings], httpx.Response]


def project_name(env: str) -> str:
    env = env.lower()
    return f"sbam-{_SHORT.get(env, env)}"


def _internal(host: str) -> bool:
    name = urlsplit(host).hostname or ""
    try:
        return ipaddress.ip_address(name).is_private
    except ValueError:
        return bool(name) and "." not in name  # a Compose service name, e.g. langfuse-web


def _projects(s: WorkerSettings) -> httpx.Response:
    """The project(s) the key pair belongs to (Langfuse public API)."""
    secret = s.langfuse_secret_key.get_secret_value() if s.langfuse_secret_key else ""
    return httpx.get(
        f"{(s.langfuse_host or '').rstrip('/')}/api/public/projects",
        auth=(s.langfuse_public_key or "", secret),
        timeout=TIMEOUT_SECONDS,
    )


def check_langfuse(s: WorkerSettings, fetch: Fetch = _projects) -> None:
    env = s.sbam_env.lower()
    strict = env in MOCK_FORBIDDEN_ENVS
    keys = {
        "LANGFUSE_HOST": s.langfuse_host,
        "LANGFUSE_PUBLIC_KEY": s.langfuse_public_key,
        "LANGFUSE_SECRET_KEY": s.langfuse_secret_key,
    }
    missing = [name for name, value in keys.items() if not _set(value)]
    problems = []
    if strict and missing:
        problems.append(f"{', '.join(missing)} required when SBAM_ENV={s.sbam_env}")
    if strict and _set(s.langfuse_host) and not _internal(s.langfuse_host or ""):
        problems.append("LANGFUSE_HOST must be an internal address")
    if env in PROD_ENVS and s.langfuse_log_content:
        problems.append("LANGFUSE_LOG_CONTENT must be false in production")
    if problems:
        raise PreflightError(MISCONFIGURED, "; ".join(problems))
    if missing:
        return  # development without Langfuse: no traces, trace ids are still made

    expected = project_name(env)
    try:
        response = fetch(s)
        if response.status_code in (401, 403):
            problem = "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY were rejected"
        elif response.is_error:
            raise httpx.HTTPStatusError("langfuse", request=response.request, response=response)
        else:
            names = [p.get("name") for p in response.json().get("data", [])]
            if names == [expected]:
                return
            problem = f"the Langfuse keys are not those of project {expected}"
    except (httpx.HTTPError, ValueError) as exc:  # down or garbled: tracing tolerates it
        log.warning("langfuse project not checked (%s)", type(exc).__name__)
        return
    if strict:
        raise PreflightError(MISCONFIGURED, problem)
    log.warning("%s", problem)


@preflight_for(TaskQueue.INGEST, TaskQueue.LLM, TaskQueue.BUILD, TaskQueue.TEST)
def langfuse_preflight() -> None:
    check_langfuse(WorkerSettings())  # type: ignore[call-arg]
