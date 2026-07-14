#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-entity-text-extract-job}"

cmd=(
  gcloud run jobs execute "${JOB_NAME}"
  --region "${REGION}"
  --wait
)

if [[ -n "${PROJECT_ID:-}" ]]; then
  cmd+=(--project "${PROJECT_ID}")
fi

if [[ -n "${UPDATE_ENV_VARS:-}" ]]; then
  cmd+=(--update-env-vars "${UPDATE_ENV_VARS}")
fi

"${cmd[@]}"
