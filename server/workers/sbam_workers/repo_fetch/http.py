"""The only HTTP the fetcher makes: JSON from a package index, the vulnerability database, a host
API.

Every request goes through the `HostPolicy` first, so an unlisted host is refused before a
connection.
A redirect is never followed (it could lead to another host), the answer is size-capped, and a
missing
object (404) is `None`, not an error.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from sbam_workers.repo_fetch.hosts import HostPolicy

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20.0


class HttpFailure(Exception):  # noqa: N818
    """The service could not be reached or answered with an error: retryable, not a verdict."""


class JsonHttp(Protocol):
    def get(self, url: str) -> dict[str, Any] | None: ...

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any] | None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class PolicyHttp:
    """`JsonHttp` over urllib, behind a host policy."""

    def __init__(self, policy: HostPolicy, timeout: float = TIMEOUT) -> None:
        self.policy, self.timeout = policy, timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _call(self, url: str, data: bytes | None) -> dict[str, Any] | None:
        self.policy.check(url)  # HostRefused: a refusal, never a connection
        request = urllib.request.Request(  # noqa: S310 - https and a listed host, checked above
            url,
            data=data,
            headers={"accept": "application/json", "content-type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as res:
                raw = res.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise HttpFailure(f"{exc.code} from {self.policy.check(url)}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HttpFailure(
                f"cannot reach {self.policy.check(url)}: {type(exc).__name__}"
            ) from None
        if len(raw) > MAX_BYTES:
            raise HttpFailure("the answer is too large")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HttpFailure("the answer is not JSON") from None
        return body if isinstance(body, dict) else None

    def get(self, url: str) -> dict[str, Any] | None:
        return self._call(url, None)

    def post(self, url: str, body: dict[str, Any]) -> dict[str, Any] | None:
        return self._call(url, json.dumps(body).encode())
