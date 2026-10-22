# syntax=docker/dockerfile:1.7
# sbam-api and sbam-worker images (TD §20.2). One uv workspace, built per package:
#   docker build -f compose/docker/server.Dockerfile --build-arg PACKAGE=sbam-api     --build-arg ROLE=api    --target prod -t sbam-api .
#   docker build -f compose/docker/server.Dockerfile --build-arg PACKAGE=sbam-workers --build-arg ROLE=worker --target prod -t sbam-worker .
# Targets: `prod` (code baked in, non-editable) and `dev` (source bind-mounted by compose.override.yaml, reload).

FROM ghcr.io/astral-sh/uv:0.12.0@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 AS uv

FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS base
ARG PACKAGE=sbam-api
ARG ROLE=api
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-${ROLE}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/root/.cache/uv \
    PATH=/opt/venv/bin:$PATH \
    SBAM_ROLE=${ROLE}
COPY --from=uv /uv /usr/local/bin/uv
RUN groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --no-create-home --shell /usr/sbin/nologin sbam \
 && mkdir -p /data/storage /data/cognee /tmp/sbam \
 && chown -R sbam:sbam /data /tmp/sbam
# SOW export (#88): WeasyPrint prints PDFs through Pango, and every font a PDF uses is embedded from
# the Noto families here (Latin, CJK, Arabic, mono). Pandoc ships inside the pypandoc-binary wheel.
# Debian's versions follow the pinned base image digest.
# hadolint ignore=DL3008
RUN if [ "$ROLE" = worker ]; then \
      apt-get update \
   && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        fonts-noto-core fonts-noto-cjk fonts-noto-mono \
   && rm -rf /var/lib/apt/lists/*; \
    fi
COPY --chmod=0755 compose/docker/server/healthcheck.py /usr/local/bin/sbam-healthcheck
COPY --chmod=0755 compose/docker/server/migrate.sh /usr/local/bin/sbam-migrate
WORKDIR /app/server
# Lockfile + every workspace member's pyproject first, so dependency layers cache across code changes.
COPY server/pyproject.toml server/uv.lock server/.python-version ./
COPY server/api/pyproject.toml api/
COPY server/workers/pyproject.toml workers/
COPY server/contracts/pyproject.toml contracts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package "${PACKAGE}"
# Cognee (worker-knowledge) downloads two things on first use: tiktoken's encoding and the JSON extension of
# its graph store (Ladybug, into ~/.lbdb). Workers have no internet route, so the worker image fetches both
# here, at build time, and gives the sbam user a home for them and for ~/.cognee (#259).
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    HOME=/home/sbam
RUN mkdir -p /home/sbam \
 && if [ "$ROLE" = worker ]; then \
      python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" \
   && python -c "import ladybug, os, tempfile; db = ladybug.Database(os.path.join(tempfile.mkdtemp(), 'db')); c = ladybug.Connection(db); c.execute('INSTALL JSON;'); c.execute('LOAD EXTENSION JSON;')"; \
    fi \
 && chown -R sbam:sbam /home/sbam
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 CMD ["sbam-healthcheck"]

# --- dev: dev tools installed; compose.override.yaml mounts server/ over /app/server ----------------
FROM base AS dev
COPY server/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package "${PACKAGE}" \
 && chown -R sbam:sbam /opt/venv
USER 10001:10001
CMD ["sh", "-c", "if [ \"$SBAM_ROLE\" = api ]; then exec uvicorn sbam_api.main:create_app --factory --host 0.0.0.0 --port 8000 --reload --reload-dir /app/server; else exec python -m sbam_workers.bootstrap; fi"]

# --- prod: code baked in, no dev tools, non-editable install ----------------------------------------
FROM base AS prod
COPY server/api api/
COPY server/workers workers/
COPY server/contracts contracts/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package "${PACKAGE}"
USER 10001:10001
EXPOSE 8000
# api: the FastAPI app (#34). worker: the Temporal bootstrap (#40); queues from $SBAM_QUEUES.
CMD ["sh", "-c", "if [ \"$SBAM_ROLE\" = api ]; then exec uvicorn sbam_api.main:create_app --factory --host 0.0.0.0 --port 8000; else exec python -m sbam_workers.bootstrap; fi"]
