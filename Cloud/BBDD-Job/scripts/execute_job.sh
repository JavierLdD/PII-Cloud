#!/usr/bin/env bash
set -euo pipefail

REGION="${REGION:-us-central1}"
JOB_NAME="${JOB_NAME:-bbdd-pii-job}"
WAIT="${WAIT:-true}"

if [[ -n "${SCAN_REQUEST_JSON_COMPACT:-}" ]]; then
  scan_request_json="${SCAN_REQUEST_JSON_COMPACT}"
elif [[ -n "${SCAN_REQUEST_JSON:-}" ]]; then
  scan_request_json="$(printf '%s' "${SCAN_REQUEST_JSON}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), separators=(",",":")))' )"
else
  cat >&2 <<EOF
Set SCAN_REQUEST_JSON or SCAN_REQUEST_JSON_COMPACT before executing the job.
The database connection must be selected per execution, not at deploy time.
EOF
  exit 2
fi

cmd=(
  gcloud run jobs execute "${JOB_NAME}"
  --region "${REGION}"
  --update-env-vars "^~^SCAN_REQUEST_JSON=${scan_request_json}"
)

if [[ -n "${PROJECT_ID:-}" ]]; then
  cmd+=(--project "${PROJECT_ID}")
fi

if [[ "${WAIT}" == "true" ]]; then
  cmd+=(--wait)
fi

"${cmd[@]}"
