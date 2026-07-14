#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-bbdd-pii-job}"
TASKS="${TASKS:-1}"
PARALLELISM="${PARALLELISM:-1}"
MAX_RETRIES="${MAX_RETRIES:-0}"
TASK_TIMEOUT="${TASK_TIMEOUT:-3600s}"
CPU="${CPU:-2}"
MEMORY="${MEMORY:-4Gi}"
BBDD_RESULTS_DATABASE_URL_SECRET_VERSION="${BBDD_RESULTS_DATABASE_URL_SECRET_VERSION:-latest}"
ENV_VARS_FILE="${ENV_VARS_FILE:-${PROJECT_ROOT}/Cloud/BBDD-Job/config/env.sample.yaml}"
ATTACH_CLOUD_SQL="${ATTACH_CLOUD_SQL:-false}"
CLEAR_CLOUD_SQL_INSTANCES="${CLEAR_CLOUD_SQL_INSTANCES:-true}"

: "${IMAGE_URI:?Set IMAGE_URI with the Artifact Registry image to deploy.}"
: "${BBDD_RESULTS_DATABASE_URL_SECRET:?Set the results Cloud SQL Secret Manager name.}"

cmd=(
  gcloud run jobs deploy "${JOB_NAME}"
  --image "${IMAGE_URI}"
  --region "${REGION}"
  --tasks "${TASKS}"
  --parallelism "${PARALLELISM}"
  --max-retries "${MAX_RETRIES}"
  --task-timeout "${TASK_TIMEOUT}"
  --cpu "${CPU}"
  --memory "${MEMORY}"
  --labels "app=pii,component=bbdd"
)

if [[ -n "${PROJECT_ID:-}" ]]; then
  cmd+=(--project "${PROJECT_ID}")
fi

if [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
  cmd+=(--service-account "${SERVICE_ACCOUNT}")
fi

if [[ -f "${ENV_VARS_FILE}" ]]; then
  cmd+=(--env-vars-file "${ENV_VARS_FILE}")
fi

cmd+=(
  --set-secrets
  "BBDD_RESULTS_DATABASE_URL=${BBDD_RESULTS_DATABASE_URL_SECRET}:${BBDD_RESULTS_DATABASE_URL_SECRET_VERSION}"
)

if [[ "${ATTACH_CLOUD_SQL}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  : "${CLOUD_SQL_INSTANCE:?Set CLOUD_SQL_INSTANCE when ATTACH_CLOUD_SQL=true.}"
  cmd+=(--set-cloudsql-instances "${CLOUD_SQL_INSTANCE}")
elif [[ -n "${CLOUD_SQL_INSTANCE:-}" ]]; then
  echo "Ignoring CLOUD_SQL_INSTANCE because ATTACH_CLOUD_SQL is not true." >&2
fi

if [[ -n "${VPC_CONNECTOR:-}" ]]; then
  cmd+=(--vpc-connector "${VPC_CONNECTOR}")
fi

if [[ -n "${VPC_EGRESS:-}" ]]; then
  cmd+=(--vpc-egress "${VPC_EGRESS}")
fi

"${cmd[@]}"

if [[ ! "${ATTACH_CLOUD_SQL}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]] \
  && [[ "${CLEAR_CLOUD_SQL_INSTANCES}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  clear_cmd=(
    gcloud run jobs update "${JOB_NAME}"
    --region "${REGION}"
    --clear-cloudsql-instances
  )
  if [[ -n "${PROJECT_ID:-}" ]]; then
    clear_cmd+=(--project "${PROJECT_ID}")
  fi
  "${clear_cmd[@]}"
fi
