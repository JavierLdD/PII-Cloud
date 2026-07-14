#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}/Entity_Text_Extract:${PROJECT_ROOT}:${PROJECT_ROOT}/Cloud/Text-Extract-Job-Common/src:${PROJECT_ROOT}/Cloud/Entity-Text-Extract-Job/src:${PYTHONPATH:-}"
export PII_ENTITY_OUTPUT_DIR="${PII_ENTITY_OUTPUT_DIR:-/tmp/pii-entity-output}"

python -m cloud_entity_text_extract_job.main
