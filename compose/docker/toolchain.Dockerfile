# syntax=docker/dockerfile:1.7
# sbam-toolchain (TD §20.2): pinned Ruff, Black, MyPy, Bandit for the build worker's static gate and CI.
# Versions match server/uv.lock. Runs compose/docker/toolchain/runner.py: jobs in, results out, no network (E12.4b).
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-toolchain"
ENV PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir \
      ruff==0.16.10 \
      black==26.10.1 \
      mypy==2.4.0 \
      bandit==1.9.4 \
 && groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --no-create-home --shell /usr/sbin/nologin sbam
COPY compose/docker/toolchain/runner.py /opt/sbam/runner.py
WORKDIR /work
USER 10001:10001
# The runner touches /tmp/heartbeat on every pass (a tmpfs); a stale one means it is stuck.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,sys,time; sys.exit(0 if time.time()-os.stat('/tmp/heartbeat').st_mtime < 30 else 1)"]
CMD ["python", "/opt/sbam/runner.py"]
