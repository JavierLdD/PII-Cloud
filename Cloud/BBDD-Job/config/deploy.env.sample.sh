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
export MEMORY="${MEMORY:-4Gi}"

export IMAGE_TAG="${IMAGE_TAG:-amd64-$(date +%Y%m%d-%H%M%S)}"
export IMAGE_URI="${IMAGE_URI:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}}"

# Set PRELOAD_ZERO_SHOT_MODEL=true before building if the image must run
# zero-shot discovery offline in Cloud Run.
export PRELOAD_ZERO_SHOT_MODEL="${PRELOAD_ZERO_SHOT_MODEL:-false}"
export ZERO_SHOT_MODEL_NAME="${ZERO_SHOT_MODEL_NAME:-MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7}"

# Attach the results Cloud SQL instance. This is independent of the target BBDD,
# whose connection string is supplied per execution.
export ATTACH_CLOUD_SQL="${ATTACH_CLOUD_SQL:-false}"
export CLEAR_CLOUD_SQL_INSTANCES="${CLEAR_CLOUD_SQL_INSTANCES:-true}"
# export CLOUD_SQL_INSTANCE="project:region:instance"

# Mandatory for deployed runs: a different secret from the target database
# connection passed inside SCAN_REQUEST_JSON for each execution.
# export BBDD_RESULTS_DATABASE_URL_SECRET="bbdd-results-database-url"
# export BBDD_RESULTS_DATABASE_URL_SECRET_VERSION="latest"
# export GCS_OUTPUT_URI="gs://pii-results/database-discovery/"

# Optional runtime service account. If unset, Cloud Run uses the project default.
# export SERVICE_ACCOUNT="bbdd-pii-job@${PROJECT_ID}.iam.gserviceaccount.com"
