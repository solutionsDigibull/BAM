"""Alembic environment: online migrations against DATABASE_URL, version table in schema `sbam`."""

import importlib
import importlib.util
import os
import pkgutil

from alembic import context
from sqlalchemy import pool, text

import sbam_api
from sbam_api.db.base import SCHEMA, Base
from sbam_api.db.session import make_engine


def import_models() -> None:
    """Import each `sbam_api.<module>.models` so autogenerate and `alembic check` see its tables."""
    for info in pkgutil.iter_modules(sbam_api.__path__):
        if info.ispkg and importlib.util.find_spec(f"sbam_api.{info.name}.models"):
            importlib.import_module(f"sbam_api.{info.name}.models")


def include_name(name: str | None, type_: str, _parent: object) -> bool:
    return type_ != "schema" or name == SCHEMA  # compare only our schema


def run_migrations() -> None:
    url = context.config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]
    import_models()
    engine = make_engine(url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        # The version table lives in `sbam`, so the schema must exist before Alembic looks for it.
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        conn.commit()
        context.configure(
            connection=conn,
            target_metadata=Base.metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported; run against a database")
run_migrations()
