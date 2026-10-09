# Spike E0.12 (#30): stands in for the worker container that runs a FastMCP tool and its loop harness.
FROM python:3.12-slim

RUN pip install --no-cache-dir "fastmcp==4.1.0" && useradd --uid 1000 --create-home spike
WORKDIR /spike
COPY ms01_tools.py harness.py /spike/tool/
ENV PYTHONUNBUFFERED=1 OUT_DIR=/spike/out
USER spike
CMD ["python", "/spike/tool/harness.py"]
