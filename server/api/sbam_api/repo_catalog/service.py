"""The project's repository catalog: the Agents `repo` and Tooling `links_path` columns, parsed.

Reads the entries the project's masters snapshot would pin (the tenant's and the project's own;
ILLUSTRATIVE and RETIRED never, Restricted only when the project allows it) at their current
version. Nothing is written and nothing is fetched. Another tenant's or an unassigned project is
a 404 before this runs (`active_project`), and every query goes through the tenant-scoped
repositories.
"""

import uuid
from typing import Any

from sbam_contracts.masters import STRUCTURE_VERSION, MasterStatus, MasterType
from sbam_contracts.repo_catalog import (
    CatalogCandidate,
    CatalogRejection,
    RejectReason,
    RepoCatalog,
)
from sbam_contracts.repo_decision import RepoSource
from sqlalchemy.orm import Session

from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.masters import snapshot
from sbam_api.masters.models import MasterEntry
from sbam_api.masters.repository import MasterEntryRepository
from sbam_api.projects.repository import ProjectRepository
from sbam_api.repo_catalog.parse import candidate_id, parse_cell

# (master type, payload column, source) of the two columns the catalog reads.
COLUMNS: tuple[tuple[MasterType, str, RepoSource], ...] = (
    (MasterType.AGENTS, "repo", RepoSource.ADMIN_AGENTS),
    (MasterType.TOOLING, "links_path", RepoSource.ADMIN_TOOLING),
)


def build(session: Session, user: CurrentUser, project_id: uuid.UUID) -> RepoCatalog:
    ctx = user.context()
    project = ProjectRepository(session, ctx).get(project_id)
    if project is None:
        raise not_found()
    entries_repo = MasterEntryRepository(session, ctx)
    entries: list[MasterEntry] = entries_repo.for_project(project_id)
    versions = entries_repo.current_versions(entries)
    by_id = {e.id: e for e in entries}
    candidates = [
        snapshot.Candidate(
            entry_id=e.id,
            project_id=e.project_id,
            master_type=e.master_type,
            stable_id=e.stable_id,
            status=e.status,
            version=versions[e.id].version,
            payload=versions[e.id].payload,
        )
        for e in entries
        if e.id in versions
    ]
    pinned = snapshot.resolve(project_id, candidates)
    payloads = {c.entry_id: c.payload for c in candidates}

    found: list[CatalogCandidate] = []
    rejected: list[CatalogRejection] = []
    seen: set[tuple[str, str]] = set()  # (source, url): one candidate per row and URL
    for pin in pinned:
        if pin.status in (MasterStatus.ILLUSTRATIVE, MasterStatus.RETIRED):
            continue
        if pin.restricted and not project.allow_restricted:
            continue
        for master_type, column, source in COLUMNS:
            if pin.master_type is not master_type:
                continue
            payload: Any = payloads[pin.entry_id]
            entry = by_id[pin.entry_id]
            cell = payload.get(column) or ""
            for item in parse_cell(str(cell)):
                if item.url is None or item.reason is not None:
                    rejected.append(
                        CatalogRejection(
                            value=item.value or "(empty)",
                            reason=item.reason or RejectReason.INVALID_URL,
                            source=source,
                            master_entry_id=str(entry.id),
                            stable_id=entry.stable_id,
                        )
                    )
                    continue
                key = (str(entry.id) + source.value, item.url)
                if key in seen:
                    continue
                seen.add(key)
                name = payload.get("agent_name") or None
                found.append(
                    CatalogCandidate(
                        id=candidate_id(str(entry.id), source.value, item.url),
                        url=item.url,
                        ref=item.ref,
                        notes=item.notes,
                        source=source,
                        master_entry_id=str(entry.id),
                        stable_id=entry.stable_id,
                        row_label=entry.row_label or None,
                        agent_name=str(name) if name else None,
                    )
                )
    found.sort(key=lambda c: (c.source.value, c.stable_id, c.url))
    rejected.sort(key=lambda r: (r.source.value, r.stable_id, r.value))
    return RepoCatalog(
        project_id=str(project_id),
        structure_version=STRUCTURE_VERSION,
        candidates=found,
        rejected=rejected,
    )
