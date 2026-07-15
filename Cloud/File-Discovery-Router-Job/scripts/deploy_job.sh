#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-file-discovery-router-job}"
TASKS="${TASKS:-1}"
PARALLELISM="${PARALLELISM:-1}"
MAX_RETRIES="${MAX_RETRIES:-0}"
TASK_TIMEOUT="${TASK_TIMEOUT:-3600s}"
CPU="${CPU:-1}"
MEMORY="${MEMORY:-1Gi}"
ENV_VARS_FILE="${ENV_VARS_FILE:-${PROJECT_ROOT}/Cloud/File-Discovery-Router-Job/config/env.sample.yaml}"
ATTACH_CLOUD_SQL="${ATTACH_CLOUD_SQL:-false}"
CLEAR_CLOUD_SQL_INSTANCES="${CLEAR_CLOUD_SQL_INSTANCES:-true}"

: "${IMAGE_URI:?Set IMAGE_URI with the Artifact Registry image to deploy.}"
: "${ENV_VARS_FILE:?Set ENV_VARS_FILE with the plain runtime variables YAML.}"

if [[ ! -f "${ENV_VARS_FILE}" ]]; then
  echo "ENV_VARS_FILE does not exist: ${ENV_VARS_FILE}" >&2
  exit 2
fi

if [[ -n "${DATABASE_URL:-}" && -f "${ENV_VARS_FILE}" ]]; then
  cat >&2 <<EOF
DATABASE_URL cannot be passed from the shell while ENV_VARS_FILE is used,
because gcloud does not allow --env-vars-file and --set-env-vars together.

For plain-env deploys, put DATABASE_URL inside:
  ${ENV_VARS_FILE}

Then run:
  unset DATABASE_URL
EOF
  exit 2
fi

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
  --labels "app=pii,component=file-discovery-router"
)

if [[ -n "${PROJECT_ID:-}" ]]; then
  cmd+=(--project "${PROJECT_ID}")
fi

if [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
  cmd+=(--service-account "${SERVICE_ACCOUNT}")
fi

cmd+=(--env-vars-file "${ENV_VARS_FILE}")

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
