# syntax=docker/dockerfile:1.7
# sbam-promptfoo (TD §20.2): pinned Promptfoo CLI. #127 (E13.6) extends this image.
FROM node:22.23-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-promptfoo"
ENV PROMPTFOO_DISABLE_TELEMETRY=1 \
    PROMPTFOO_DISABLE_UPDATE=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false
# Pull Debian security fixes for the base (e.g. perl-base CVEs fixed after the node image was cut).
# hadolint ignore=DL3005
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*
RUN npm install --global --no-audit --no-fund promptfoo@0.124.0 \
 && npm cache clean --force \
 && groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --create-home --shell /usr/sbin/nologin sbam
WORKDIR /work
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=20s CMD ["promptfoo", "--version"]
CMD ["promptfoo", "--version"]
