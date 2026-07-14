from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from table_extract.discovery import discover_pii
from table_extract.models import DataSourceProfile, DiscoveryResult, ScanConfig
from table_extract.profiling import create_scan_session, profile_file_context, profile_source
from table_extract.runtime.models import FileScanContext
from table_extract.sources import (
    DatabaseScanRequest,
    OrdsScanRequest,
    build_database_source_adapter,
    build_file_source_adapter,
    build_ords_source_adapter,
)


@dataclass(frozen=True)
class FileProfileRequest:
    context: FileScanContext


@dataclass(frozen=True)
class DatabaseProfileRequest:
    request: DatabaseScanRequest


@dataclass(frozen=True)
class OrdsProfileRequest:
    request: OrdsScanRequest


ProfileSourceRequest: TypeAlias = (
    FileProfileRequest | DatabaseProfileRequest | OrdsProfileRequest
)


def profile_table_source(request: ProfileSourceRequest) -> DataSourceProfile:
    if isinstance(request, FileProfileRequest):
        return profile_file_context(request.context)

    if isinstance(request, DatabaseProfileRequest):
        adapter = build_database_source_adapter(request.request)
        try:
            return profile_source(adapter)
        finally:
            adapter.close()

    if isinstance(request, OrdsProfileRequest):
        adapter = build_ords_source_adapter(request.request)
        try:
            return profile_source(adapter)
        finally:
            adapter.close()

    raise TypeError(f"Unsupported profile request: {type(request).__name__}")


def discover_table_source(
    request: ProfileSourceRequest,
    *,
    run_id: str | None = None,
    config: ScanConfig | None = None,
) -> DiscoveryResult:
    if isinstance(request, FileProfileRequest):
        adapter = build_file_source_adapter(request.context)
        try:
            session = create_scan_session(
                adapter,
                run_id=run_id or request.context.run_id,
                config=config,
            )
            return DiscoveryResult(
                run_id=session.run_id,
                profile=session.profile,
                findings=tuple(discover_pii(session)),
            )
        finally:
            adapter.close()

    if isinstance(request, DatabaseProfileRequest):
        adapter = build_database_source_adapter(request.request)
        try:
            if not run_id:
                raise ValueError("run_id is required for database discovery")
            session = create_scan_session(adapter, run_id=run_id, config=config)
            return DiscoveryResult(
                run_id=session.run_id,
                profile=session.profile,
                findings=tuple(discover_pii(session)),
            )
        finally:
            adapter.close()

    if isinstance(request, OrdsProfileRequest):
        adapter = build_ords_source_adapter(request.request)
        try:
            if not run_id:
                raise ValueError("run_id is required for ORDS discovery")
            session = create_scan_session(adapter, run_id=run_id, config=config)
            return DiscoveryResult(
                run_id=session.run_id,
                profile=session.profile,
                findings=tuple(discover_pii(session)),
            )
        finally:
            adapter.close()

    raise TypeError(f"Unsupported discovery request: {type(request).__name__}")
