# syntax=docker/dockerfile:1.7
# The Staging egress allowlist proxy (WG-31, #908, ADR 0036/0038): two stdlib-only modules, nothing else.
#   docker build -f compose/docker/egress-proxy.Dockerfile -t sbam-egress-proxy:local .
# It runs on the Staging network and the one network with a route out (compose.staging-runtime.yaml), reads the
# per-deployment allowlists the worker writes into /var/sbam/egress (read-only here) and logs every blocked request.
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-egress-proxy"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
# The package __init__ files of the app import FastAPI; the proxy needs neither, so they are empty here.
COPY server/api/sbam_api/staging/egress.py sbam_api/staging/egress.py
COPY server/workers/sbam_workers/staging/egress_proxy.py sbam_workers/staging/egress_proxy.py
RUN touch sbam_api/__init__.py sbam_api/staging/__init__.py sbam_workers/__init__.py sbam_workers/staging/__init__.py \
 && mkdir -p /var/sbam/egress
USER 10001:10001
EXPOSE 3128
HEALTHCHECK --interval=10s --timeout=3s --retries=10 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 3128), 2)"]
CMD ["python", "-m", "sbam_workers.staging.egress_proxy", "--listen", "0.0.0.0:3128", "--policy-dir", "/var/sbam/egress"]
