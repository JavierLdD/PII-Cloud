FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY Cloud/File-Discovery-Router-Job/requirements-cloud.txt /tmp/cloud_requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/cloud_requirements.txt

COPY Cloud/File-Discovery-Router-Job/src/ /app/Cloud/File-Discovery-Router-Job/src/

ENV PYTHONPATH=/app/Cloud/File-Discovery-Router-Job/src

ENTRYPOINT ["python", "-m", "cloud_file_router_job.main"]
