"""`python -m sbam_api.identity.seed`: the dev tenants behind the Keycloak seed orgs (#47, #49).

compose/keycloak/seed.sh creates organizations Tenant A and Tenant B (alias = these ids). Their
users only pass the tenant check (E2.3) if the tenants exist here too. Idempotent; never changes an
existing row. Local/CI only: compose.override.yaml runs it, staging and prod create tenants through
POST /admin/tenants.
"""

import os
import uuid

from sbam_api.core.logging import configure_logging
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant

DEV_TENANTS = {
    uuid.UUID("6a1f3e2c-0b7d-4c55-9a51-1c2f0a7e4b11"): "Tenant A",
    uuid.UUID("8d2b9c4e-5f60-4a18-b3c7-2e9d4f1a6c22"): "Tenant B",
}


def seed(database_url: str) -> int:
    added = 0
    with make_sessionmaker(make_engine(database_url))() as session:
        for tenant_id, name in DEV_TENANTS.items():
            if session.get(Tenant, tenant_id) is None:
                session.add(Tenant(id=tenant_id, name=name, created_by="seed"))
                added += 1
        session.commit()
    return added


if __name__ == "__main__":
    configure_logging()
    print(f"dev tenants added: {seed(os.environ['DATABASE_URL'])}")
