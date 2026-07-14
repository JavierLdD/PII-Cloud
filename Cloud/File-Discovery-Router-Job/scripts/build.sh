#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

: "${IMAGE_URI:?Set IMAGE_URI with the Artifact Registry image to build.}"

DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"

docker buildx build \
  --platform "${DOCKER_PLATFORM}" \
  --load \
  -f "${PROJECT_ROOT}/Cloud/File-Discovery-Router-Job/Dockerfile" \
  -t "${IMAGE_URI}" \
  "${PROJECT_ROOT}"
