from __future__ import annotations

from typing import Callable

from table_extract.materialization import FileMaterializer, MaterializedFile
from table_extract.messaging import QueueConsumer
from table_extract.profiling import profile_file_context
from table_extract.runtime.models import (
    QUEUE_TABLES,
    FileScanContext,
    StoredFile,
    TableRoutedMessage,
)
from table_extract.runtime.repository import TableExtractRepository


FileScanCallback = Callable[[FileScanContext], None]


def process_table_payload(
    payload: dict[str, object],
    repository: TableExtractRepository,
    materializer: FileMaterializer,
) -> FileScanContext:
    message = TableRoutedMessage.from_payload(payload)
    return process_file_id(
        message.file_id,
        repository=repository,
        materializer=materializer,
        message=message,
        routing_decision_id=message.routing_decision_id,
    )


def process_file_id(
    file_id: str,
    repository: TableExtractRepository,
    materializer: FileMaterializer,
    message: TableRoutedMessage | None = None,
    routing_decision_id: str | None = None,
) -> FileScanContext:
    stored_file = repository.get_file(file_id)
    if stored_file is None:
        raise ValueError(f"File not found in database: {file_id}")

    materialized = materializer.materialize(stored_file)
    return build_file_scan_context(
        stored_file=stored_file,
        materialized=materialized,
        message=message,
        routing_decision_id=routing_decision_id,
    )


def build_file_scan_context(
    stored_file: StoredFile,
    materialized: MaterializedFile,
    message: TableRoutedMessage | None = None,
    routing_decision_id: str | None = None,
) -> FileScanContext:
    return FileScanContext(
        run_id=stored_file.run_id,
        message=message,
        stored_file=materialized.stored_file,
        local_path=materialized.local_path,
        source_uri=stored_file.source_uri,
        is_temporary=materialized.is_temporary,
        lease_id=materialized.lease.lease_id if materialized.lease else None,
        routing_decision_id=routing_decision_id,
    )


def run_table_listener(
    repository: TableExtractRepository,
    materializer: FileMaterializer,
    consumer: QueueConsumer,
    source_queue_name: str = QUEUE_TABLES,
    max_messages: int | None = None,
    requeue_messages: bool = False,
    handle_context: FileScanCallback | None = None,
) -> None:
    callback = handle_context or default_file_scan_callback

    def handle_payload(payload: dict[str, object]) -> None:
        context = process_table_payload(
            payload,
            repository=repository,
            materializer=materializer,
        )
        try:
            callback(context)
        finally:
            if context.is_temporary:
                materializer.release_context(context.file_id)

    consumer.consume(
        source_queue_name,
        handle_payload,
        max_messages=max_messages,
        requeue_messages=requeue_messages,
    )


def default_file_scan_callback(context: FileScanContext) -> None:
    profile = profile_file_context(context)
    column_count = sum(len(table.columns) for table in profile.tables)
    print(
        "profiled_file_scan_context "
        f"file_id={context.file_id} "
        f"run_id={context.run_id} "
        f"source_type={profile.source_type} "
        f"tables={len(profile.tables)} "
        f"columns={column_count} "
        f"temporary={context.is_temporary} "
        f"path={context.local_path}"
    )
