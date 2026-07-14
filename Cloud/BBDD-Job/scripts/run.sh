#!/usr/bin/env bash
# Source this file before build/deploy/execute commands.
#
# Set BBDD_DATABASE_URL in the shell or in an ignored local configuration.
# Never commit the real connection URI.

: "${BBDD_DATABASE_URL:?Set BBDD_DATABASE_URL before sourcing this file}"

export SCAN_REQUEST_JSON="$(
  BBDD_DATABASE_URL="${BBDD_DATABASE_URL}" python3 - <<'PY'
import json
import os

payload = {
    "scan_id": "cloud-run-profile-010",
    "source_id": "pii-test",
    "source_name": "pii-test",
    "source_type": "database",
    "dialect": "postgresql",
    "connection_uri": os.environ["BBDD_DATABASE_URL"],
    "include_schemas": ["bi_clientes"],
    "include_tables": [],
    "exclude_schemas": [],
    "exclude_tables": [],
    "include_views": True,
    "allow_full_database_scan": False,
    "profile_only": False,
    "disable_zero_shot": False,
    "zero_shot_model_name": None,
    "device": "cpu",
    "use_gpu": False,
    "output_local_path": "/tmp/table_extract_discovery.json",
    "output_uri": "gs://pii_test_bucket/bbdd/pii-test/Prueba/Hoy/3Julio/Test/A/",
}
print(json.dumps(payload, separators=(",", ":")))
PY
)"

export SCAN_REQUEST_JSON_COMPACT="${SCAN_REQUEST_JSON}"
