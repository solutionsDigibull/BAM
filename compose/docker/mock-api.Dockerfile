# syntax=docker/dockerfile:1.7
# sbam-mock-api (TD §20.2): health-only stub so the Compose core profile runs.
# ponytail: #126 (E13.5) replaces server.py with the real mock APIs for sandbox tests.
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-mock-api"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --no-create-home --shell /usr/sbin/nologin sbam
COPY --chmod=0755 compose/docker/mock-api/server.py /app/server.py
USER 10001:10001
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=2)"]
CMD ["python", "/app/server.py"]
