"""The health record of a repository: last commit, last release, archived (WG-7).

The host's API is asked when the host has one (GitHub), through the injectable `JsonHttp`. The last
commit date also comes from the pinned commit itself, so it is known when the host says nothing. A
repository that is archived, or has had no commit for two years, is flagged: a flag is evidence for
the FDE, not a refusal.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sbam_contracts.repo_scan import Health

from sbam_workers.repo_fetch.http import JsonHttp

STALE_AFTER = timedelta(days=730)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _github_slug(url: str) -> str | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() != "github.com":
        return None
    path = [p for p in parts.path.split("/") if p]
    if len(path) < 2:
        return None
    return f"{path[0]}/{path[1].removesuffix('.git')}"


def health_of(
    url: str,
    commit_time: str | None,
    http: JsonHttp | None,
    now: datetime,
) -> Health:
    last_commit = _parse(commit_time)
    last_release: datetime | None = None
    archived: bool | None = None
    slug = _github_slug(url)
    if slug and http is not None:
        repo = http.get(f"https://api.github.com/repos/{slug}") or {}
        if isinstance(repo.get("archived"), bool):
            archived = repo["archived"]
        pushed = _parse(repo.get("pushed_at"))
        if pushed and (last_commit is None or pushed > last_commit):
            last_commit = pushed
        release = http.get(f"https://api.github.com/repos/{slug}/releases/latest") or {}
        last_release = _parse(release.get("published_at"))
    flags: list[str] = []
    if archived:
        flags.append("ARCHIVED")
    if last_commit is not None and now - last_commit > STALE_AFTER:
        flags.append("STALE")
    return Health(
        last_commit_at=last_commit.isoformat() if last_commit else None,
        last_release_at=last_release.isoformat() if last_release else None,
        archived=archived,
        flags=flags,
    )
