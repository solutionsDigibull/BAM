# syntax=docker/dockerfile:1.7
# sandbox-runner (E13.2, ADR 0023 decision 6): the local-docker sandbox. The skill-runtime image (the
# runtime every Sub-Skill is tested in) plus the stdlib agent that gives the worker create / put / exec /
# get / destroy over HTTP, so the worker never needs a docker socket. Non-certifying by construction.
# RUNTIME_IMAGE is the skill-runtime image; compose passes the local build, deployments pass its digest.
ARG RUNTIME_IMAGE=skill-runtime:local

# The in-VM runner and the E13.3 harness it imports: copied (never installed) into the slot the runtime
# image reserves, so the runtime registry never lists platform code (ADR 0023, decision 7).
FROM ${RUNTIME_IMAGE} AS harness
COPY server/contracts/sbam_contracts /src/server/contracts/sbam_contracts
COPY server/workers/sbam_workers /src/server/workers/sbam_workers
COPY compose/sandbox-runner/harness /src/compose/sandbox-runner/harness
RUN python -I /src/compose/sandbox-runner/harness/stage.py /src /tmp/harness

FROM ${RUNTIME_IMAGE}
COPY --from=harness --chown=10001:10001 /tmp/harness/ /opt/sbam/harness/
COPY --chown=10001:10001 compose/sandbox-runner/agent.py /opt/sbam/agent.py
USER 10001:10001
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8091), 2)"]
CMD ["python", "-I", "/opt/sbam/agent.py"]
