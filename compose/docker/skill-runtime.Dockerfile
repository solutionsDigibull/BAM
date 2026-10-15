# syntax=docker/dockerfile:1.7
# skill-runtime (TD §20.2): the image every generated Sub-Skill runs and is tested in, and the source of the
# CubeSandbox template. #38 laid the base (Python 3.12 + PostgreSQL 16, non-root); #67 (E12.1) adds:
#   * the allowed libraries + FastMCP (with its MCP client) from requirements.lock, installed with --require-hashes
#   * /opt/runtime_registry.json generated from what is installed (the image is the truth, not the lock);
#     CI regenerates it with the image digest and syncs it to the API's runtime registry
#   * sbam-pg-start: ephemeral PostgreSQL bound to 127.0.0.1
#   * /opt/sbam/harness: slot for the bounded loop harness (#124)
# Pinned by digest everywhere; tags are never trusted.

# --- libs: build the virtualenv from the hashed lock --------------------------------------------------------
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS libs
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY compose/docker/skill-runtime/requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock

# --- runtime ------------------------------------------------------------------------------------------------
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="skill-runtime" \
      org.opencontainers.image.description="SBAM Sub-Skill runtime: FastMCP, allowed libraries, PostgreSQL 16" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
ARG PG_MAJOR=16
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:/usr/lib/postgresql/16/bin:$PATH \
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
COPY --from=libs /opt/venv /opt/venv
COPY --chmod=0644 compose/docker/skill-runtime/requirements.in /opt/sbam/allowed-libraries.in
COPY --chmod=0755 compose/docker/skill-runtime/registry.py /usr/local/bin/sbam-runtime-registry
COPY --chmod=0755 compose/docker/skill-runtime/pg-start.sh /usr/local/bin/sbam-pg-start
RUN sbam-runtime-registry --output /opt/runtime_registry.json \
 && install -d -o 10001 -g 10001 /opt/sbam/harness
WORKDIR /work
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=10s CMD ["python", "-c", "import json; json.load(open('/opt/runtime_registry.json'))"]
CMD ["python", "--version"]
