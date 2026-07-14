from __future__ import annotations

from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.models import DiscoveredFile  # noqa: E402
from cloud_file_router_job.repository import (  # noqa: E402
    SNAPSHOT_MODIFIED,
    SNAPSHOT_NEW,
    SNAPSHOT_REPROCESSED,
    SNAPSHOT_REUSED,
    build_revision_key,
    determine_snapshot_state,
    result_is_reusable,
)


def discovered_file(**overrides) -> DiscoveredFile:
    values = {
        "source_type": "drive",
        "source_uri": "drive://file/file-1",
        "external_id": "file-1",
        "file_name": "sample.pdf",
        "relative_path": "sample.pdf",
        "extension": ".pdf",
        "mime_type": "application/pdf",
        "size_bytes": 42,
        "checksum_sha256": None,
        "content_hash": "md5-value",
        "etag": "version-7",
    }
    values.update(overrides)
    return DiscoveredFile(**values)


def test_revision_key_prefers_checksum_then_content_hash_then_etag() -> None:
    assert (
        build_revision_key(discovered_file(checksum_sha256="sha-value"))
        == "sha256:sha-value"
    )
    assert build_revision_key(discovered_file()) == "content:md5-value"
    assert (
        build_revision_key(discovered_file(content_hash=None))
        == "etag:version-7"
    )
    assert (
        build_revision_key(discovered_file(content_hash=None, etag=None))
        is None
    )


def test_new_file_is_new_snapshot() -> None:
    assert (
        determine_snapshot_state(
            prior_exists=False,
            current_revision_key="etag:2",
            prior_revision_key=None,
            pipeline_compatible=False,
            prior_result_reusable=False,
            force_enqueue=False,
        )
        == SNAPSHOT_NEW
    )


def test_changed_or_unreliable_revision_is_modified() -> None:
    common = {
        "prior_exists": True,
        "pipeline_compatible": True,
        "prior_result_reusable": True,
        "force_enqueue": False,
    }
    assert (
        determine_snapshot_state(
            current_revision_key="etag:2",
            prior_revision_key="etag:1",
            **common,
        )
        == SNAPSHOT_MODIFIED
    )
    assert (
        determine_snapshot_state(
            current_revision_key=None,
            prior_revision_key=None,
            **common,
        )
        == SNAPSHOT_MODIFIED
    )


def test_reuse_requires_same_revision_compatible_pipeline_and_success() -> None:
    common = {
        "prior_exists": True,
        "current_revision_key": "etag:2",
        "prior_revision_key": "etag:2",
        "force_enqueue": False,
    }
    assert (
        determine_snapshot_state(
            pipeline_compatible=True,
            prior_result_reusable=True,
            **common,
        )
        == SNAPSHOT_REUSED
    )
    assert (
        determine_snapshot_state(
            pipeline_compatible=False,
            prior_result_reusable=True,
            **common,
        )
        == SNAPSHOT_REPROCESSED
    )
    assert (
        determine_snapshot_state(
            pipeline_compatible=True,
            prior_result_reusable=False,
            **common,
        )
        == SNAPSHOT_REPROCESSED
    )
    assert (
        determine_snapshot_state(
            pipeline_compatible=True,
            prior_result_reusable=True,
            force_enqueue=True,
            **{key: value for key, value in common.items() if key != "force_enqueue"},
        )
        == SNAPSHOT_REPROCESSED
    )


def test_reusable_result_requires_a_completed_route_specific_result() -> None:
    assert result_is_reusable(
        route_type="pdf",
        route_status="routed",
        text_status="text_extraction_completed",
        table_status=None,
        entity_status="entity_extraction_completed",
    )
    assert not result_is_reusable(
        route_type="pdf",
        route_status="routed",
        text_status="text_extraction_completed",
        table_status=None,
        entity_status=None,
    )
    assert result_is_reusable(
        route_type="table",
        route_status="routed",
        text_status=None,
        table_status="table_discovery_completed",
        entity_status=None,
    )
    assert result_is_reusable(
        route_type="unsupported",
        route_status="unsupported",
        text_status=None,
        table_status=None,
        entity_status=None,
    )


def test_cloud_schemas_remove_global_file_identity_and_add_snapshot_fields() -> None:
    job_root = Path(__file__).resolve().parents[1]
    central_schema = (job_root.parent / "Database" / "schema.sql").read_text(
        encoding="utf-8"
    )
    job_schema = (job_root / "schema.sql").read_text(encoding="utf-8")

    assert "UNIQUE (source_type, source_uri)" not in central_schema
    for schema in (central_schema, job_schema):
        assert "idx_files_run_source_uri" in schema
        assert "source_scope_key" in schema
        assert "SET source_scope_key = source_root" in schema
        assert "pipeline_revision" in schema
        assert "snapshot_state" in schema
        assert "reused_from_file_id" in schema
        assert "file_snapshot_tombstones" in schema
