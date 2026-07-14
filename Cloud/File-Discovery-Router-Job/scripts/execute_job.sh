#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-file-discovery-router-job}"

: "${DISCOVERY_ROUTER_REQUEST_JSON:?Set DISCOVERY_ROUTER_REQUEST_JSON for this execution.}"

delimiter="__ROUTER_ENV_DELIM__"
while [[ "${DISCOVERY_ROUTER_REQUEST_JSON}" == *"${delimiter}"* ]]; do
  delimiter="_${delimiter}_"
done
env_vars="^${delimiter}^DISCOVERY_ROUTER_REQUEST_JSON=${DISCOVERY_ROUTER_REQUEST_JSON}"

cmd=(
  gcloud run jobs execute "${JOB_NAME}"
  --region "${REGION}"
  --wait
  --update-env-vars "${env_vars}"
)

if [[ -n "${PROJECT_ID:-}" ]]; then
  cmd+=(--project "${PROJECT_ID}")
fi

"${cmd[@]}"
