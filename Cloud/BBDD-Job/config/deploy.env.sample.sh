#!/usr/bin/env bash
# Source this file before build/deploy commands and override values locally.
# The deployed job is intentionally database-agnostic; pass SCAN_REQUEST_JSON
# when executing the job to choose the target database.

export PROJECT_ID="${PROJECT_ID:-tu-proyecto-gcp}"
export REGION="${REGION:-us-central1}"
export AR_REPOSITORY="${AR_REPOSITORY:-pii}"
export IMAGE_NAME="${IMAGE_NAME:-bbdd-pii-job}"
export JOB_NAME="${JOB_NAME:-bbdd-pii-job}"

export TASKS="${TASKS:-1}"
export PARALLELISM="${PARALLELISM:-1}"
export MAX_RETRIES="${MAX_RETRIES:-0}"
export TASK_TIMEOUT="${TASK_TIMEOUT:-3600s}"
export CPU="${CPU:-2}"
export MEMORY="${MEMORY:-8Gi}"

export IMAGE_TAG="${IMAGE_TAG:-amd64-$(date +%Y%m%d-%H%M%S)}"
export IMAGE_URI="${IMAGE_URI:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}}"

# Zero-Shot model files live in GCS and are copied to local scratch at runtime.
export TABLE_EXTRACT_ZERO_SHOT_MODEL_URI="${TABLE_EXTRACT_ZERO_SHOT_MODEL_URI:-gs://BUCKET/Models/zero-shot/mdeberta-xnli/v1}"
export TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR="${TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR:-/tmp/pii-models/zero-shot}"

# Attach the results Cloud SQL instance. This is independent of the target BBDD,
# whose connection string is supplied per execution.
export ATTACH_CLOUD_SQL="${ATTACH_CLOUD_SQL:-false}"
export CLEAR_CLOUD_SQL_INSTANCES="${CLEAR_CLOUD_SQL_INSTANCES:-true}"
# export CLOUD_SQL_INSTANCE="project:region:instance"

# Mandatory for deployed runs. Prefer an ignored env.deploy.local.yaml passed
# through ENV_VARS_FILE; never commit the real URL.
# export ENV_VARS_FILE="Cloud/BBDD-Job/config/env.deploy.local.yaml"
# export GCS_OUTPUT_URI="gs://pii-results/database-discovery/"

# Optional runtime service account. If unset, Cloud Run uses the project default.
# export SERVICE_ACCOUNT="bbdd-pii-job@${PROJECT_ID}.iam.gserviceaccount.com"
