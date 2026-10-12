# 0003: Database access with sync SQLAlchemy 2 and psycopg 3

- Status: Accepted
- Date: 2026-10-12
- Related: issue #36 (E1.5), TD §7.1, TD §16; consumed by #41, #48, #54 and every module with tables

## Context

TD §7.1 fixes the conventions: PostgreSQL 16, schema `sbam`, UUIDv7, tenant-scoped repository base, `row_version` and frozen triggers. It doesn't say whether SQLAlchemy runs sync or async, which driver to use, or where UUIDv7 comes from. Every API module and Temporal activity will build on this choice.

## Decision

1. **Sync SQLAlchemy 2 (`Session`) on psycopg 3** (`postgresql+psycopg`).
   - FastAPI handlers that touch the database are plain `def` and run in its threadpool.
   - Long work already runs in Temporal activities (golden rule), so API handlers are short CRUD.
   - Alembic, activities and unit tests (in-memory SQLite, no extra dependency) share the same code.
2. **Optimistic locking uses SQLAlchemy's `version_id_col`** on `row_version` instead of hand-written `WHERE` clauses. The repository maps `StaleDataError` to `StaleRowVersion` (412).
3. **UUIDv7 is generated in-process** (`sbam_api/core/ids.py`, RFC 9562 §6.2 method 1 with a 12-bit counter). That avoids a dependency, and ids stay ordered within a millisecond and across clock steps. Python 3.12 has no `uuid.uuid7`.
4. **Isolation is enforced in the repository, not with Postgres RLS** (TD §16). A lint rule keeps raw queries inside `sbam_api/db/`, migrations and `*repository.py` files.

## Consequences

- A busy API process is bounded by its threadpool (40 threads by default) rather than by an event loop. If profiling shows that matters, a module can move to `AsyncSession` on the same psycopg driver without changing the models.
- Async code that needs the database (such as the readiness check) calls it through `asyncio.to_thread`.
