# TEST ONLY (WG-31, #908): the stand-in Workflow server of the Staging proof (see workflow_server.py).
# docker build --build-arg PROOF_VERSION=1.0.0 -f server/tests/staging_proof/Dockerfile -t sbam-staging-proof:1.0.0 server/tests/staging_proof
FROM python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG PROOF_VERSION=unknown
ENV PROOF_VERSION=${PROOF_VERSION} PYTHONUNBUFFERED=1
LABEL org.opencontainers.image.title="sbam-staging-proof (test only)" proof.version=${PROOF_VERSION}
COPY workflow_server.py /app/workflow_server.py
USER 10001:10001
EXPOSE 8000
CMD ["python", "/app/workflow_server.py"]
