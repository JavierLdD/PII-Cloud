from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.request import (  # noqa: E402
    DiscoveryRouterRequest,
    DiscoveryRouterRequestError,
    load_request_from_env,
)


def valid_payload(**overrides):
    payload = {
        "user_id": "user-001",
        "run_id": "run-001",
        "source_type": "drive",
        "drive_folder_id": "folder-001",
        "source_name": "clientes",
        "force_enqueue": False,
        "dry_run": False,
        "max_files": 10,
    }
    payload.update(overrides)
    return payload


def test_request_json_valid() -> None:
    request = DiscoveryRouterRequest.from_json(json.dumps(valid_payload()))

    assert request.user_id == "user-001"
    assert request.run_id == "run-001"
    assert request.source_type == "drive"
    assert request.drive_folder_id == "folder-001"
    assert request.source_scope_key == "folder-001"
    assert request.max_files == 10


def test_request_rejects_missing_user_id() -> None:
    with pytest.raises(DiscoveryRouterRequestError, match="user_id is required"):
        DiscoveryRouterRequest.from_mapping(valid_payload(user_id=""))


def test_request_rejects_missing_run_id() -> None:
    with pytest.raises(DiscoveryRouterRequestError, match="run_id is required"):
        DiscoveryRouterRequest.from_mapping(valid_payload(run_id=""))


def test_request_rejects_non_drive_source_type() -> None:
    with pytest.raises(DiscoveryRouterRequestError, match="Unsupported source_type"):
        DiscoveryRouterRequest.from_mapping(valid_payload(source_type="s3"))


def test_request_parses_boolean_env_values() -> None:
    request = load_request_from_env(
        {
            "USER_ID": "user-001",
            "RUN_ID": "run-001",
            "SOURCE_TYPE": "drive",
            "DRIVE_FOLDER_ID": "folder-001",
            "FORCE_ENQUEUE": "true",
            "DRY_RUN": "1",
        }
    )

    assert request.force_enqueue is True
    assert request.dry_run is True


def test_request_json_env_takes_precedence() -> None:
    request = load_request_from_env(
        {
            "DISCOVERY_ROUTER_REQUEST_JSON": json.dumps(
                valid_payload(user_id="from-json")
            ),
            "USER_ID": "from-env",
            "RUN_ID": "run-env",
            "DRIVE_FOLDER_ID": "folder-env",
        }
    )

    assert request.user_id == "from-json"
    assert request.run_id == "run-001"
