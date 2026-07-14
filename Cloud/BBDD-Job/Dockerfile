FROM python:3.11-slim

ARG ZERO_SHOT_MODEL_NAME="MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
ARG PRELOAD_ZERO_SHOT_MODEL="false"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    TRANSFORMERS_CACHE=/opt/huggingface/hub \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TABLE_EXTRACT_ZERO_SHOT_MODEL=${ZERO_SHOT_MODEL_NAME}

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

RUN if [ "${PRELOAD_ZERO_SHOT_MODEL}" = "true" ]; then \
        python -c "import os; from huggingface_hub import snapshot_download; model = os.environ['TABLE_EXTRACT_ZERO_SHOT_MODEL']; snapshot_download(repo_id=model); print(f'Cached zero-shot model: {model}')" \
        && python -c "import os; from transformers import AutoModelForSequenceClassification, AutoTokenizer; model = os.environ['TABLE_EXTRACT_ZERO_SHOT_MODEL']; AutoTokenizer.from_pretrained(model, local_files_only=True); AutoModelForSequenceClassification.from_pretrained(model, local_files_only=True); print(f'Verified local zero-shot model: {model}')" ; \
    else \
        echo "Skipping zero-shot model preload. Build with --build-arg PRELOAD_ZERO_SHOT_MODEL=true to enable it." ; \
    fi

COPY Table_Extract/ /app/Table_Extract/
COPY Cloud/BBDD-Job/src/ /app/Cloud/BBDD-Job/src/

RUN test -f /app/Table_Extract/table_extract/profile_artifacts.py

ENV PYTHONPATH=/app/Table_Extract:/app/Cloud/BBDD-Job/src

ENTRYPOINT ["python", "-m", "cloud_bbdd_job.main"]
