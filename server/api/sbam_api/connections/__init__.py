"""Project connections and secrets (ADR 0030, issue #764): an encrypted, write-only store.

The crypto, the cleaning and name rules, the expected names and the `.env.example` text are plain
modules; the store, the service, the SSRF-guarded test call and the routes build on them. Nothing in
here may return, log or audit a stored value.

`router` is found by the app's route discovery (`main.discover_routers`), which reads the package
attribute. It lives in `routes.py` (a module called `router` would replace this attribute with
itself on import) and is loaded on first use, not here: `core.settings` imports
`connections.crypto`, and an eager import of the routes, which import `core.auth` and so the
settings, would be a cycle.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "router":
        from sbam_api.connections.routes import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
