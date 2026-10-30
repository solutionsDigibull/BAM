# syntax=docker/dockerfile:1.7
# sandbox-runner (E13.2, ADR 0021 decision 6): the local-docker sandbox. The skill-runtime image (the
# runtime every Sub-Skill is tested in) plus the stdlib agent that gives the worker create / put / exec /
# get / destroy over HTTP, so the worker never needs a docker socket. Non-certifying by construction.
# RUNTIME_IMAGE is the skill-runtime image; compose passes the local build, deployments pass its digest.
ARG RUNTIME_IMAGE=skill-runtime:local
FROM ${RUNTIME_IMAGE}
COPY --chown=10001:10001 compose/sandbox-runner/agent.py /opt/sbam/agent.py
USER 10001:10001
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8091), 2)"]
CMD ["python", "-I", "/opt/sbam/agent.py"]
