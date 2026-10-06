FROM python:3.12-slim

WORKDIR /spike
RUN pip install --no-cache-dir "cognee==1.6.2" tiktoken openpyxl

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/spike/src
