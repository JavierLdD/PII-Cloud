#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

export PYTHONPATH="${PROJECT_ROOT}/Cloud/File-Discovery-Router-Job/src${PYTHONPATH:+:${PYTHONPATH}}"

python -m cloud_file_router_job.main
