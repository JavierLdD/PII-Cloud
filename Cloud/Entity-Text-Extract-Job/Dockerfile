FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    TRANSFORMERS_CACHE=/opt/huggingface/hub \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PII_ENTITY_SPACY_MODEL=es_core_news_lg \
    PII_ENTITY_GLINER2_MODEL=/opt/pii-models/gliner2 \
    PII_ENTITY_MEDICAL_MODEL=/opt/pii-models/humadex \
    PII_ENTITY_MODEL_BATCH_SIZE=8 \
    PII_ENTITY_ZERO_SHOT_LOCAL_DIR=/tmp/pii-models/zero-shot

ARG GLINER2_MODEL_NAME=fastino/gliner2-privacy-filter-PII-multi
ARG MEDICAL_MODEL_NAME=HUMADEX/spanish_medical_ner

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY Entity_Text_Extract/requirements.txt /tmp/entity_requirements.txt
COPY Cloud/Entity-Text-Extract-Job/requirements-cloud.txt /tmp/cloud_requirements.txt

RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/entity_requirements.txt \
    && python -m pip install -r /tmp/cloud_requirements.txt \
    && python -m spacy download es_core_news_lg

RUN python - <<PY
from huggingface_hub import snapshot_download

models = [
    ("${GLINER2_MODEL_NAME}", "/opt/pii-models/gliner2"),
    ("${MEDICAL_MODEL_NAME}", "/opt/pii-models/humadex"),
]
for repo_id, local_dir in models:
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        local_dir_use_symlinks=False,
    )
PY

COPY Entity_Text_Extract/ /app/Entity_Text_Extract/
COPY Entity_Text_Filter/ /app/Entity_Text_Filter/
COPY Cloud/Text-Extract-Job-Common/src/ /app/Cloud/Text-Extract-Job-Common/src/
COPY Cloud/Entity-Text-Extract-Job/src/ /app/Cloud/Entity-Text-Extract-Job/src/

ENV PYTHONPATH=/app/Entity_Text_Extract:/app:/app/Cloud/Text-Extract-Job-Common/src:/app/Cloud/Entity-Text-Extract-Job/src

ENTRYPOINT ["python", "-m", "cloud_entity_text_extract_job.main"]
