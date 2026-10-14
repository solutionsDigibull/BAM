# syntax=docker/dockerfile:1.7
# sbam-llm-mock (E1.15): deterministic OpenAI-compatible upstream that Bifrost calls when LLM_PROVIDER=mock.
# Fixtures are mounted at /fixtures (server/tests/llm-mock); the alias list is baked in from aliases.json.
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
LABEL org.opencontainers.image.source="https://github.com/solutionsDigibull/BAM" \
      org.opencontainers.image.vendor="Digibull" \
      org.opencontainers.image.title="sbam-llm-mock"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 sbam \
 && useradd --uid 10001 --gid sbam --no-create-home --shell /usr/sbin/nologin sbam
COPY compose/docker/llm-mock/server.py /app/server.py
COPY compose/bifrost/aliases.json /app/aliases.json
USER 10001:10001
EXPOSE 8091
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=2)"]
CMD ["python", "/app/server.py"]
