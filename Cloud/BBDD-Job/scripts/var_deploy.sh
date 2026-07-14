#!/usr/bin/env bash
# Backwards-compatible helper. Prefer:
#   source Cloud/BBDD-Job/config/deploy.env.sample.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PROJECT_ID="${PROJECT_ID:-ldd-dev}"
export REGION="${REGION:-us-central1}"
export AR_REPOSITORY="${AR_REPOSITORY:-pii}"
export IMAGE_NAME="${IMAGE_NAME:-bbdd-pii-job}"
export JOB_NAME="${JOB_NAME:-bbdd-pii-job}"

source "${SCRIPT_DIR}/../config/deploy.env.sample.sh"

export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}/${IMAGE_NAME}:${IMAGE_TAG}"

gcloud config set project "${PROJECT_ID}"
