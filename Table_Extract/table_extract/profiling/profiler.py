from __future__ import annotations

from dataclasses import replace

from table_extract.models import DataSourceProfile, ScanConfig, ScanSession
from table_extract.runtime.models import FileScanContext
from table_extract.sources import SourceAdapter
from table_extract.sources.files import build_file_source_adapter


def profile_source(source: SourceAdapter) -> DataSourceProfile:
    tables = []
    for table in source.iter_tables():
        columns = tuple(source.iter_columns(table))
        tables.append(replace(table, columns=columns))

    return DataSourceProfile(
        source_name=source.source_name,
        source_type=source.source_type,
        dialect=source.dialect,
        source_uri=source.source_uri,
        tables=tuple(tables),
    )


def create_scan_session(
    source: SourceAdapter,
    *,
    run_id: str,
    config: ScanConfig | None = None,
) -> ScanSession:
    profile = profile_source(source)
    return ScanSession(
        run_id=run_id,
        source=source,
        profile=profile,
        config=config or ScanConfig(),
    )


def profile_file_context(context: FileScanContext) -> DataSourceProfile:
    source = build_file_source_adapter(context)
    try:
        return profile_source(source)
    finally:
        source.close()
