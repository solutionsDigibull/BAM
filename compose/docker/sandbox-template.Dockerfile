# syntax=docker/dockerfile:1.7
# CubeSandbox template image (WG-1, ADR 0023 decisions 4, 5 and 7): the skill-runtime image every Sub-Skill is
# tested in, plus the in-VM test runner and the `envd` agent that CubeSandbox talks to. CubeSandbox turns this image
# into a template (a memory snapshot); `scripts/sandbox-template.sh` builds it, pushes it to a registry the host can
# reach and registers it under the alias `sbam-rt-<first 56 hex of the runtime digest>`.
#
# RUNTIME_IMAGE is the skill-runtime image (a digest in deployments, the local build on a laptop).
# RUNTIME_DIGEST is that image's own digest. It is written to /etc/sbam-runtime so the adapter can check, inside
# the VM, that the alias was not re-pointed at another image (an alias can be changed by anyone with API access).
ARG RUNTIME_IMAGE=skill-runtime:local
ARG CUBE_BASE_IMAGE=ghcr.io/tencentcloud/cubesandbox-base@sha256:a1dd972a4eef85448f967daed3bda42c6be2b7f42bf64c1f4d38e63b3e935472

# The in-VM runner and the harness it imports: copied (never installed) into the slot the runtime image reserves,
# so the runtime registry never lists platform code (ADR 0023, decision 7). Same staging as the local sandbox.
FROM ${RUNTIME_IMAGE} AS harness
COPY server/contracts/sbam_contracts /src/server/contracts/sbam_contracts
COPY server/workers/sbam_workers /src/server/workers/sbam_workers
COPY compose/sandbox-runner/harness /src/compose/sandbox-runner/harness
RUN python -I /src/compose/sandbox-runner/harness/stage.py /src /tmp/harness

# envd, its init and its entrypoint come from the pinned CubeSandbox base image, not from a build on the host.
FROM ${CUBE_BASE_IMAGE} AS cubebase

FROM ${RUNTIME_IMAGE}
ARG RUNTIME_DIGEST
# root: envd runs as root in the VM and the platform writes files such as /etc/hosts as root. Tests never run as
# root: the adapter always executes them as user `sbam` (uid 10001), which the runtime image already defines.
USER root
COPY --from=cubebase /usr/bin/envd /usr/bin/envd
COPY --from=cubebase /usr/bin/tini /usr/bin/tini
COPY --from=cubebase /usr/local/bin/cube-entrypoint.sh /usr/local/bin/cube-entrypoint.sh
# root-owned and not writable by the sandbox user (#625): the code under test runs as that user and must not be
# able to change the runner, its child entrypoint or the harness on disk. The base image owns /opt/sbam/harness
# as 10001, so ownership is taken back here.
COPY --from=harness /tmp/harness/ /opt/sbam/harness/
# /work is where a run's files go and where the runner recreates out/ before it writes the outputs (ADR 0027): it
# must belong to the run user. WORKDIR in the runtime image creates it as root, and the upload agent only
# creates the directories below it as the run user, so a root-owned /work made every run crash in write_outputs.
RUN test -n "${RUNTIME_DIGEST}" \
 && printf '%s\n' "${RUNTIME_DIGEST}" > /etc/sbam-runtime \
 && chmod 0444 /etc/sbam-runtime \
 && chown -R root:root /opt/sbam \
 && chmod -R go-w /opt/sbam \
 && install -d -o 10001 -g 10001 -m 0755 /work
ENV ENVD_PORT=49983
EXPOSE 49983
# envd starts in the background and the CMD keeps the VM alive; the platform runs the work through envd.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/cube-entrypoint.sh"]
CMD ["sleep", "infinity"]
