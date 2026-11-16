"""Free text -> repository candidates. Pure: no I/O, no DNS, no clock.

A master cell may hold one URL, several, or prose around them ("see https://github.com/a/b, main
branch"). Every token that looks like a URL is judged on its own:

- https only; `http`, `ssh`, `git@host:path`, `file` and the rest are `NOT_HTTPS`;
- a user or token in the URL (`https://user:secret@host/...`) is `HAS_CREDENTIALS`, and the
  secret never reaches the output (`safe_value` removes it);
- a query string is `HAS_QUERY`;
- `localhost`, any IP address (a literal is never a repository host), and names that only exist on a
  private network (`.local`, `.internal`, `.lan`, `.corp`, no dot at all) are `PRIVATE_ADDRESS`;
- anything the contract's `RepoUrl` does not accept is `INVALID_URL`.

A name that merely resolves to a private address cannot be seen without a lookup; the fetcher
(WG-7) checks that when it connects. A branch or tag is written after `#`: `https://host/o/r#v1.2`.
"""

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError
from sbam_contracts.isatvon14 import RepoUrl
from sbam_contracts.repo_catalog import NOTES_MAX, RejectReason

_URL_ADAPTER: TypeAdapter[str] = TypeAdapter(RepoUrl)
# What looks like a URL: a scheme, `git@host:path`, `www.…`, or `host.tld/path`.
_TOKEN = re.compile(
    r"""(?:[A-Za-z][A-Za-z0-9+.\-]*://\S+|git@[^\s:]+:\S+|www\.\S+|(?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,}/\S+)"""
)
_TRAILING = ".,;:)]}>'\"!"
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".corp", ".home", ".intranet")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,119}$")
MAX_TOKENS = 50  # per cell: a cell with more is not a list of repositories


@dataclass(frozen=True)
class Found:
    """One URL-like token: a candidate (`url` set) or a rejection (`reason` set)."""

    url: str | None
    ref: str | None
    notes: str
    value: str  # what was written, without any user or token part
    reason: RejectReason | None

    @property
    def candidate_key(self) -> str:
        return self.url or self.value


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        pass
    # 2130706433, 0x7f000001, 0177.0.0.1: forms a resolver would read as an address
    return bool(re.fullmatch(r"(0x[0-9a-fA-F]+|\d+)(\.(0x[0-9a-fA-F]+|\d+)){0,3}", host)) or (
        ":" in host
    )


def _private_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host == "localhost" or "." not in host or _is_ip(host):
        return True
    return host.endswith(_PRIVATE_SUFFIXES)


def safe_value(token: str) -> str:
    """The token as shown to the Admin: no user or password, clipped."""
    shown = re.sub(r"^([A-Za-z][A-Za-z0-9+.\-]*://)[^/@\s]*@", r"\1", token)
    shown = re.sub(r"^git@", "", shown)
    return shown[:NOTES_MAX]


def _judge(token: str, notes: str) -> Found:
    token = token.rstrip(_TRAILING)
    shown = safe_value(token)

    def reject(reason: RejectReason) -> Found:
        return Found(None, None, notes, shown, reason)

    if not re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", token):
        if token.startswith("www.") or re.match(r"^([A-Za-z0-9\-]+\.)+[A-Za-z]{2,}/", token):
            token = "https://" + token  # a bare host/path is read as https
        else:
            return reject(RejectReason.NOT_HTTPS)  # git@host:path
    if not token.lower().startswith("https://"):
        return reject(RejectReason.NOT_HTTPS)
    try:
        parts = urlsplit(token)
        host = parts.hostname or ""
        parts.port  # noqa: B018  (raises for a bad port)
    except ValueError:
        return reject(RejectReason.INVALID_URL)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return reject(RejectReason.HAS_CREDENTIALS)
    if parts.query or "?" in token:
        return reject(RejectReason.HAS_QUERY)
    if not host or _private_host(host):
        return reject(RejectReason.PRIVATE_ADDRESS)
    ref = parts.fragment or None
    if ref is not None and not _REF.fullmatch(ref):
        return reject(RejectReason.INVALID_URL)
    url = f"https://{parts.netloc.lower()}{parts.path}".rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    try:
        _URL_ADAPTER.validate_python(url)
    except ValidationError:
        return reject(RejectReason.INVALID_URL)
    return Found(url, ref, notes, shown, None)


def parse_cell(text: str) -> list[Found]:
    """Every URL-like token of a cell, in order. Duplicates of one URL and ref are dropped."""
    found: list[Found] = []
    seen: set[tuple[str, str | None]] = set()
    for line in text.splitlines():
        tokens = _TOKEN.findall(line)[:MAX_TOKENS]
        if not tokens:
            continue
        rest = _TOKEN.sub(" ", line)
        notes = re.sub(r"\s+", " ", rest).strip(" -:,;()")[:NOTES_MAX]
        for token in tokens:
            item = _judge(token, notes)
            key = (item.candidate_key, item.ref)
            if key in seen:
                continue
            seen.add(key)
            found.append(item)
    return found


def candidate_id(master_entry_id: str, source: str, url: str) -> str:
    """Stable for one master row and one canonical URL (the ref is not part of the identity)."""
    digest = hashlib.sha256(f"{master_entry_id}|{source}|{url}".encode()).hexdigest()
    return f"rc-{digest[:16]}"
