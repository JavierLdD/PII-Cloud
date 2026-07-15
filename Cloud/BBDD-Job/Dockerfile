FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    TRANSFORMERS_CACHE=/opt/huggingface/hub \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR=/tmp/pii-models/zero-shot

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY Table_Extract/requirements.txt /tmp/table_extract_requirements.txt
COPY Cloud/BBDD-Job/requirements-cloud.txt /tmp/cloud_requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install \
        -r /tmp/table_extract_requirements.txt \
        -r /tmp/cloud_requirements.txt

COPY Table_Extract/ /app/Table_Extract/
COPY Cloud/BBDD-Job/src/ /app/Cloud/BBDD-Job/src/

RUN test -f /app/Table_Extract/table_extract/profile_artifacts.py

ENV PYTHONPATH=/app/Table_Extract:/app/Cloud/BBDD-Job/src

ENTRYPOINT ["python", "-m", "cloud_bbdd_job.main"]
