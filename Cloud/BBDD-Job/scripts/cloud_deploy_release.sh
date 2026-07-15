#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CLOUDDEPLOY_DIR="${PROJECT_ROOT}/Cloud/BBDD-Job/clouddeploy"

: "${PROJECT_ID:?Set PROJECT_ID before creating the Cloud Deploy release.}"

REGION="${REGION:-us-central1}"
DELIVERY_PIPELINE="${DELIVERY_PIPELINE:-bbdd-pii-job}"
TARGET_NAME="${TARGET_NAME:-dev}"
JOB_NAME="${JOB_NAME:-bbdd-pii-job}"
TASKS="${TASKS:-1}"
PARALLELISM="${PARALLELISM:-1}"
MAX_RETRIES="${MAX_RETRIES:-0}"
TASK_TIMEOUT="${TASK_TIMEOUT:-3600s}"
TASK_TIMEOUT_SECONDS="${TASK_TIMEOUT_SECONDS:-${TASK_TIMEOUT%s}}"
CPU="${CPU:-2}"
MEMORY="${MEMORY:-8Gi}"
RELEASE_NAME="${RELEASE_NAME:-bbdd-pii-job-$(date +%Y%m%d%H%M%S)}"
CLOUD_DEPLOY_DRY_RUN="${CLOUD_DEPLOY_DRY_RUN:-0}"
ATTACH_CLOUD_SQL="${ATTACH_CLOUD_SQL:-false}"
BBDD_DISABLE_ZERO_SHOT="${BBDD_DISABLE_ZERO_SHOT:-false}"
TABLE_EXTRACT_ZERO_SHOT_MODEL_URI="${TABLE_EXTRACT_ZERO_SHOT_MODEL_URI:-}"
TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR="${TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR:-/tmp/pii-models/zero-shot}"

: "${BBDD_RESULTS_DATABASE_URL:?Set the plain results Cloud SQL URL.}"
: "${GCS_OUTPUT_URI:?Set the internal gs:// artifact destination.}"
if ! [[ "${BBDD_DISABLE_ZERO_SHOT}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  : "${TABLE_EXTRACT_ZERO_SHOT_MODEL_URI:?Set the gs:// Zero-Shot snapshot URI.}"
fi

if ! [[ "${TASK_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "TASK_TIMEOUT_SECONDS must be a plain integer. Current value: ${TASK_TIMEOUT_SECONDS}" >&2
  exit 2
fi

if [[ -z "${IMAGE_URI:-}" ]]; then
  IMAGE_URI="$("${PROJECT_ROOT}/Cloud/BBDD-Job/scripts/build.sh" | tail -n 1)"
  export IMAGE_URI
fi

if [[ "${IMAGE_URI}" == *":TAG" || "${IMAGE_URI}" == *"PROJECT_ID"* || "${IMAGE_URI}" == *"tu-proyecto"* ]]; then
  cat >&2 <<EOF
IMAGE_URI appears to contain a placeholder:
  ${IMAGE_URI}

Set IMAGE_URI to a real Artifact Registry image tag before creating a release.
Example:
  export IMAGE_URI="us-central1-docker.pkg.dev/ldd-dev/pii/bbdd-pii-job:20260703-001"
EOF
  exit 2
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/bbdd-pii-job-clouddeploy.XXXXXX")"
cp "${CLOUDDEPLOY_DIR}/skaffold.yaml" "${WORK_DIR}/skaffold.yaml"

cat > "${WORK_DIR}/clouddeploy.yaml" <<YAML
apiVersion: deploy.cloud.google.com/v1
kind: DeliveryPipeline
metadata:
  name: ${DELIVERY_PIPELINE}
description: BBDD PII Cloud Run Job delivery pipeline
serialPipeline:
  stages:
    - targetId: ${TARGET_NAME}
---
apiVersion: deploy.cloud.google.com/v1
kind: Target
metadata:
  name: ${TARGET_NAME}
description: Cloud Run Job target for ${PROJECT_ID}/${REGION}
run:
  location: projects/${PROJECT_ID}/locations/${REGION}
YAML

cat > "${WORK_DIR}/job.yaml" <<YAML
apiVersion: run.googleapis.com/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  labels:
    app: pii
    component: bbdd
spec:
  template:
YAML

if [[ "${ATTACH_CLOUD_SQL}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  : "${CLOUD_SQL_INSTANCE:?Set CLOUD_SQL_INSTANCE when ATTACH_CLOUD_SQL=true.}"
  cat >> "${WORK_DIR}/job.yaml" <<YAML
    metadata:
      annotations:
        run.googleapis.com/cloudsql-instances: "${CLOUD_SQL_INSTANCE}"
YAML
elif [[ -n "${CLOUD_SQL_INSTANCE:-}" ]]; then
  echo "Ignoring CLOUD_SQL_INSTANCE because ATTACH_CLOUD_SQL is not true." >&2
fi

cat >> "${WORK_DIR}/job.yaml" <<YAML
    spec:
      taskCount: ${TASKS}
      parallelism: ${PARALLELISM}
      template:
        spec:
YAML

if [[ -n "${SERVICE_ACCOUNT:-}" ]]; then
  cat >> "${WORK_DIR}/job.yaml" <<YAML
          serviceAccountName: "${SERVICE_ACCOUNT}"
YAML
fi

cat >> "${WORK_DIR}/job.yaml" <<YAML
          maxRetries: ${MAX_RETRIES}
          timeoutSeconds: ${TASK_TIMEOUT_SECONDS}
          containers:
            - image: bbdd-pii-job-image
              resources:
                limits:
                  cpu: "${CPU}"
                  memory: "${MEMORY}"
              env:
                - name: TABLE_EXTRACT_ZERO_SHOT_DEVICE
                  value: "cpu"
                - name: BBDD_DISABLE_ZERO_SHOT
                  value: "${BBDD_DISABLE_ZERO_SHOT}"
                - name: TABLE_EXTRACT_ZERO_SHOT_MODEL_URI
                  value: "${TABLE_EXTRACT_ZERO_SHOT_MODEL_URI}"
                - name: TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR
                  value: "${TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR}"
                - name: GCS_OUTPUT_URI
                  value: "${GCS_OUTPUT_URI}"
                - name: BBDD_RESULTS_DATABASE_URL
                  value: "${BBDD_RESULTS_DATABASE_URL}"
YAML

echo "Cloud Deploy source rendered at: ${WORK_DIR}"
echo "Image: ${IMAGE_URI}"
echo "Pipeline: ${DELIVERY_PIPELINE}"
echo "Target: ${TARGET_NAME}"
echo "Release: ${RELEASE_NAME}"

if [[ "${CLOUD_DEPLOY_DRY_RUN}" == "1" ]]; then
  echo "Dry run enabled. Not applying pipeline or creating release."
  exit 0
fi

gcloud deploy apply \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --file "${WORK_DIR}/clouddeploy.yaml"

gcloud deploy releases create "${RELEASE_NAME}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --delivery-pipeline "${DELIVERY_PIPELINE}" \
  --source "${WORK_DIR}" \
  --images "bbdd-pii-job-image=${IMAGE_URI}" \
  --to-target "${TARGET_NAME}"
