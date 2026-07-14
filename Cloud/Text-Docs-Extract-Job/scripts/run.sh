#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}/Text_Extract:${PROJECT_ROOT}/Cloud/Text-Extract-Job-Common/src:${PROJECT_ROOT}/Cloud/Text-Docs-Extract-Job/src:${PYTHONPATH:-}"
export TEXT_MATERIALIZE_SCRATCH_DIR="${TEXT_MATERIALIZE_SCRATCH_DIR:-/tmp/pii-text}"

python -m cloud_text_docs_extract_job.main
