"""Startup self-check of the knowledge worker (E5.1, ADR 0001).

Cognee reads its configuration from the environment when it is imported, so the check reads the
same variables. A failure stops `worker-knowledge` before it polls `sbam-knowledge`:

* `ENABLE_BACKEND_ACCESS_CONTROL` must be `true`, else `COGNEE_ACL_DISABLED` (E5.1-T04);
* the LLM and embedding endpoints must be Bifrost, with a key (a Bifrost key, never OpenRouter's);
* the Cognee stores must live on the `cognee_data` volume;
* `COGNEE_USER_SECRET` (derives the per-project Cognee passwords) must be set.
Everything else is `COGNEE_MISCONFIGURED`. Messages name variables, never their values.
"""

import os
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from sbam_api.core.settings import PLACEHOLDER, deployed_env
from sbam_contracts.api import ErrorCode
from sbam_contracts.runs import TaskQueue

from sbam_workers.registry import PreflightError, preflight_for

MISCONFIGURED = "COGNEE_MISCONFIGURED"
GATEWAY_HOST = "bifrost"  # the only host Cognee may call (TD §20.3.3)
VOLUME_ROOT = PurePosixPath("/data/cognee")  # the cognee_data mount
MIN_SECRET_LENGTH = 32


def check_cognee_env(env: Mapping[str, str]) -> None:
    if env.get("ENABLE_BACKEND_ACCESS_CONTROL", "").strip().lower() != "true":
        raise PreflightError(
            ErrorCode.COGNEE_ACL_DISABLED.value, "ENABLE_BACKEND_ACCESS_CONTROL must be true"
        )
    problems = []
    for name in ("LLM_ENDPOINT", "EMBEDDING_ENDPOINT"):
        if urlsplit(env.get(name, "")).hostname != env.get("COGNEE_GATEWAY_HOST", GATEWAY_HOST):
            problems.append(f"{name} must point at Bifrost")
    for name in ("LLM_API_KEY", "EMBEDDING_API_KEY", "LLM_MODEL", "EMBEDDING_MODEL"):
        if not env.get(name, "").strip():
            problems.append(f"{name} is required")
    if env.get("OPENROUTER_API_KEY"):
        problems.append("OPENROUTER_API_KEY must not reach the knowledge worker")
    for name in ("DATA_ROOT_DIRECTORY", "SYSTEM_ROOT_DIRECTORY"):
        path = PurePosixPath(env.get(name, ""))
        if not path.is_absolute() or VOLUME_ROOT not in (path, *path.parents) or ".." in path.parts:
            problems.append(f"{name} must be under {VOLUME_ROOT}")
    if len(env.get("COGNEE_USER_SECRET", "")) < MIN_SECRET_LENGTH:
        problems.append(f"COGNEE_USER_SECRET must be at least {MIN_SECRET_LENGTH} characters")
    if PLACEHOLDER in env.get("COGNEE_USER_SECRET", "").lower() and deployed_env(
        env.get("SBAM_ENV") or "development"
    ):
        problems.append(f"COGNEE_USER_SECRET still holds the `{PLACEHOLDER}` template value")
    if problems:
        raise PreflightError(MISCONFIGURED, "; ".join(problems))


@preflight_for(TaskQueue.KNOWLEDGE)
def cognee_preflight() -> None:
    check_cognee_env(os.environ)
