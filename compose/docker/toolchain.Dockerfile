# syntax=docker/dockerfile:1.7
# sbam-toolchain (TD §20.2): pinned Ruff, Black, MyPy, Bandit for the build worker's static gate and CI.
# Versions match server/uv.lock; #119 (E12.4) extends this image, it doesn't create another.
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
WORKDIR /work
USER 10001:10001
HEALTHCHECK --interval=60s --timeout=10s CMD ["ruff", "--version"]
CMD ["ruff", "--version"]
