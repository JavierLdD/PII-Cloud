#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

: "${PROJECT_ID:?Set PROJECT_ID before building the image.}"

REGION="${REGION:-us-central1}"
AR_REPOSITORY="${AR_REPOSITORY:-pii}"
IMAGE_NAME="${IMAGE_NAME:-bbdd-pii-job}"
IMAGE_TAG="${IMAGE_TAG:-$(git -C "${PROJECT_ROOT}" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
IMAGE_URI="${IMAGE_URI:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}}"

cd "${PROJECT_ROOT}"

gcloud builds submit \
  --project "${PROJECT_ID}" \
  --config Cloud/BBDD-Job/cloudbuild.yaml \
  --substitutions "_IMAGE_URI=${IMAGE_URI}" \
  .

printf '%s\n' "${IMAGE_URI}"
