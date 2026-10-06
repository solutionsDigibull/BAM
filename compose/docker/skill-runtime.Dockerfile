# syntax=docker/dockerfile:1.7
# skill-runtime BASE (TD §20.2): Python 3.12 + PostgreSQL 16 server binaries for ephemeral test databases,
# non-root, with a runtime_registry.json generated at build time.
# #67 (E12.1) EXTENDS this image with FastMCP, the allowed libraries and the loop harness. No DuckDB, ever.
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="skill-runtime"
ARG PG_MAJOR=16
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/usr/lib/postgresql/16/bin:$PATH \
    PGDATA=/tmp/pgdata
# PostgreSQL 16 from the official PGDG apt repository (Debian bookworm ships 15).
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends "postgresql-${PG_MAJOR}" \
 && apt-get purge -y curl gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --create-home --shell /usr/sbin/nologin sbam
COPY --chmod=0755 compose/docker/skill-runtime/registry.py /usr/local/bin/sbam-runtime-registry
RUN sbam-runtime-registry > /opt/runtime_registry.json
WORKDIR /work
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=10s CMD ["python", "-c", "import json; json.load(open('/opt/runtime_registry.json'))"]
CMD ["python", "--version"]
