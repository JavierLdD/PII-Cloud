from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import uuid
from typing import Any

from common.models import (
    DESTINATION_QUEUE_NAMES,
    PAGE_COMPLETED_STATUS,
    PAGE_FAILED_STATUS,
    PAGE_PENDING_OCR_STATUS,
    PDF_ATTEMPT_ACTIVE_STATUS,
    PDF_ATTEMPT_COMPLETED_STATUS,
    PDF_ATTEMPT_QUARANTINED_STATUS,
    QUEUE_DOC,
    QUEUE_ENTITY,
    QUEUE_OCR,
    QUEUE_OCR_URGENT,
    TEXT_EXTRACTION_COMPLETED_STATUS,
    TEXT_EXTRACTION_FAILED_STATUS,
    WAITING_OCR_STATUS,
    DocProcessingResult,
    OcrProcessingResult,
    OcrBatchMetrics,
    OutboxMessage,
    PdfAttemptState,
    PdfProcessingResult,
    PdfPageResult,
    RoutedFileMessage,
    StoredFile,
    TextChunk,
    build_chunks_ready_payload,
    build_chunks_ready_payload_for_file,
    build_ocr_batch_request_payload,
)
from materialization.models import (
    LEASE_ACTIVE_STATUS,
    LEASE_DEFERRED_STATUS,
    LEASE_EXPIRED_STATUS,
    LEASE_FAILED_STATUS,
    LEASE_RELEASED_STATUS,
    BudgetSnapshot,
    MaterializationConfig,
    MaterializationDeferred,
    MaterializationLease,
    decide_materialization_budget,
)


class PostgresTextExtractionRepository:
    def __init__(self, database_url: str, chunk_ttl_hours: int = 24) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install psycopg with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        self._conn = psycopg.connect(database_url)
        self._chunk_ttl_hours = chunk_ttl_hours

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostgresTextExtractionRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def get_file(self, file_id: str) -> StoredFile | None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    file_id,
                    run_id,
                    source_type,
                    source_uri,
                    external_id,
                    file_name,
                    relative_path,
                    extension,
                    mime_type,
                    size_bytes,
                    checksum_sha256,
                    content_hash,
                    etag
                FROM files
                WHERE file_id = %s
                """,
                (file_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return StoredFile(
            file_id=str(row[0]),
            run_id=str(row[1]),
            source_type=str(row[2]),
            source_uri=str(row[3]),
            external_id=str(row[4]) if row[4] is not None else None,
            file_name=str(row[5]),
            relative_path=str(row[6]),
            extension=str(row[7] or ""),
            mime_type=str(row[8]) if row[8] is not None else None,
            size_bytes=int(row[9]) if row[9] is not None else None,
            checksum_sha256=str(row[10]).strip() if row[10] is not None else None,
            content_hash=str(row[11]).strip() if row[11] is not None else None,
            etag=str(row[12]).strip() if row[12] is not None else None,
        )

    def record_pdf_attempt_start(
        self,
        message: RoutedFileMessage,
        max_attempts: int,
    ) -> PdfAttemptState:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO text_pdf_processing_attempts (
                        file_id,
                        run_id,
                        routing_decision_id,
                        attempts,
                        max_attempts,
                        status,
                        first_attempt_at,
                        last_attempt_at
                    )
                    VALUES (%s, %s, %s, 1, %s, %s, now(), now())
                    ON CONFLICT (file_id) DO UPDATE
                    SET
                        run_id = EXCLUDED.run_id,
                        routing_decision_id = EXCLUDED.routing_decision_id,
                        attempts = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN text_pdf_processing_attempts.attempts
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN 1
                            ELSE text_pdf_processing_attempts.attempts + 1
                        END,
                        max_attempts = EXCLUDED.max_attempts,
                        status = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN text_pdf_processing_attempts.status
                            ELSE %s
                        END,
                        first_attempt_at = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN now()
                            ELSE text_pdf_processing_attempts.first_attempt_at
                        END,
                        last_attempt_at = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN text_pdf_processing_attempts.last_attempt_at
                            ELSE now()
                        END,
                        last_error_at = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN NULL
                            ELSE text_pdf_processing_attempts.last_error_at
                        END,
                        last_error_type = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN NULL
                            ELSE text_pdf_processing_attempts.last_error_type
                        END,
                        last_error_message = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN NULL
                            ELSE text_pdf_processing_attempts.last_error_message
                        END,
                        last_error_traceback = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN NULL
                            ELSE text_pdf_processing_attempts.last_error_traceback
                        END,
                        last_result_status = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN NULL
                            ELSE text_pdf_processing_attempts.last_result_status
                        END,
                        quarantined_at = CASE
                            WHEN text_pdf_processing_attempts.status = %s
                                THEN text_pdf_processing_attempts.quarantined_at
                            ELSE NULL
                        END,
                        updated_at = now()
                    RETURNING
                        file_id,
                        attempts,
                        max_attempts,
                        status,
                        first_attempt_at,
                        last_attempt_at,
                        last_error_at,
                        last_error_type,
                        last_error_message,
                        last_error_traceback,
                        quarantined_at,
                        last_result_status
                    """,
                    (
                        message.file_id,
                        message.run_id,
                        message.routing_decision_id,
                        max_attempts,
                        PDF_ATTEMPT_ACTIVE_STATUS,
                        PDF_ATTEMPT_QUARANTINED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_QUARANTINED_STATUS,
                        PDF_ATTEMPT_ACTIVE_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_QUARANTINED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        PDF_ATTEMPT_QUARANTINED_STATUS,
                    ),
                )
                state = _pdf_attempt_state_from_row(cursor.fetchone())
            self._conn.commit()
            return state
        except Exception:
            self._conn.rollback()
            raise

    def record_pdf_attempt_error(
        self,
        file_id: str,
        error_type: str,
        error_message: str,
        error_traceback: str,
    ) -> PdfAttemptState:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE text_pdf_processing_attempts
                    SET
                        last_error_at = now(),
                        last_error_type = %s,
                        last_error_message = %s,
                        last_error_traceback = %s,
                        updated_at = now()
                    WHERE file_id = %s
                    RETURNING
                        file_id,
                        attempts,
                        max_attempts,
                        status,
                        first_attempt_at,
                        last_attempt_at,
                        last_error_at,
                        last_error_type,
                        last_error_message,
                        last_error_traceback,
                        quarantined_at,
                        last_result_status
                    """,
                    (error_type, error_message, error_traceback, file_id),
                )
                state = _pdf_attempt_state_from_row(cursor.fetchone())
            self._conn.commit()
            return state
        except Exception:
            self._conn.rollback()
            raise

    def record_pdf_attempt_completed(
        self,
        file_id: str,
        result_status: str,
    ) -> None:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE text_pdf_processing_attempts
                    SET
                        status = %s,
                        last_result_status = %s,
                        updated_at = now()
                    WHERE file_id = %s AND status <> %s
                    """,
                    (
                        PDF_ATTEMPT_COMPLETED_STATUS,
                        result_status,
                        file_id,
                        PDF_ATTEMPT_QUARANTINED_STATUS,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def record_pdf_attempt_quarantined(self, file_id: str) -> PdfAttemptState:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE text_pdf_processing_attempts
                    SET
                        status = %s,
                        quarantined_at = COALESCE(quarantined_at, now()),
                        updated_at = now()
                    WHERE file_id = %s
                    RETURNING
                        file_id,
                        attempts,
                        max_attempts,
                        status,
                        first_attempt_at,
                        last_attempt_at,
                        last_error_at,
                        last_error_type,
                        last_error_message,
                        last_error_traceback,
                        quarantined_at,
                        last_result_status
                    """,
                    (PDF_ATTEMPT_QUARANTINED_STATUS, file_id),
                )
                state = _pdf_attempt_state_from_row(cursor.fetchone())
            self._conn.commit()
            return state
        except Exception:
            self._conn.rollback()
            raise

    def expire_materialization_leases(self) -> list[str]:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("LOCK TABLE text_materialization_leases IN EXCLUSIVE MODE")
                paths = self._expire_materialization_leases(cursor)
            self._conn.commit()
            return paths
        except Exception:
            self._conn.rollback()
            raise

    def acquire_materialization_lease(
        self,
        stored_file: StoredFile,
        config: MaterializationConfig,
    ) -> MaterializationLease:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("LOCK TABLE text_materialization_leases IN EXCLUSIVE MODE")
                self._expire_materialization_leases(cursor)
                existing = self._active_materialization_lease(
                    cursor,
                    stored_file.file_id,
                )
                if existing is not None:
                    self._conn.commit()
                    return existing

                snapshot = self._materialization_budget_snapshot(cursor)
                decision = decide_materialization_budget(
                    snapshot=snapshot,
                    expected_bytes=stored_file.size_bytes,
                    small_limit_bytes=config.small_limit_bytes,
                    global_limit_bytes=config.global_limit_bytes,
                )
                if not decision.allowed:
                    self._insert_materialization_lease(
                        cursor=cursor,
                        stored_file=stored_file,
                        config=config,
                        lease_id=str(uuid.uuid4()),
                        status=LEASE_DEFERRED_STATUS,
                        is_oversize=decision.is_oversize,
                        reason=decision.reason,
                    )
                    self._conn.commit()
                    raise MaterializationDeferred(decision.reason or "budget_unavailable")

                lease_id = str(uuid.uuid4())
                self._insert_materialization_lease(
                    cursor=cursor,
                    stored_file=stored_file,
                    config=config,
                    lease_id=lease_id,
                    status=LEASE_ACTIVE_STATUS,
                    is_oversize=decision.is_oversize,
                    reason=None,
                )
                lease = self._materialization_lease_by_id(cursor, lease_id)

            self._conn.commit()
        except MaterializationDeferred:
            raise
        except Exception:
            self._conn.rollback()
            raise

        if lease is None:
            raise RuntimeError(f"Could not create materialization lease: {lease_id}")
        return lease

    def update_materialization_progress(
        self,
        lease_id: str,
        actual_bytes: int,
        is_oversize: bool,
        config: MaterializationConfig,
    ) -> None:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("LOCK TABLE text_materialization_leases IN EXCLUSIVE MODE")
                self._expire_materialization_leases(cursor)
                snapshot = self._materialization_budget_snapshot(
                    cursor,
                    exclude_lease_id=lease_id,
                )
                reason = None
                if snapshot.active_total_bytes + actual_bytes > config.global_limit_bytes:
                    reason = "global_budget_unavailable"
                elif (
                    not is_oversize
                    and snapshot.active_small_bytes + actual_bytes
                    > config.small_limit_bytes
                ):
                    reason = "small_budget_unavailable"

                if reason:
                    cursor.execute(
                        """
                        UPDATE text_materialization_leases
                        SET
                            status = %s,
                            reason = %s,
                            actual_bytes = %s,
                            is_oversize = %s,
                            updated_at = now(),
                            released_at = now()
                        WHERE lease_id = %s AND status = %s
                        """,
                        (
                            LEASE_DEFERRED_STATUS,
                            reason,
                            actual_bytes,
                            is_oversize,
                            lease_id,
                            LEASE_ACTIVE_STATUS,
                        ),
                    )
                    self._conn.commit()
                    raise MaterializationDeferred(reason)

                cursor.execute(
                    """
                    UPDATE text_materialization_leases
                    SET
                        actual_bytes = %s,
                        is_oversize = %s,
                        updated_at = now(),
                        expires_at = now() + (%s * interval '1 second')
                    WHERE lease_id = %s AND status = %s
                    """,
                    (
                        actual_bytes,
                        is_oversize,
                        config.lease_ttl_seconds,
                        lease_id,
                        LEASE_ACTIVE_STATUS,
                    ),
                )
            self._conn.commit()
        except MaterializationDeferred:
            raise
        except Exception:
            self._conn.rollback()
            raise

    def activate_materialization_lease(
        self,
        lease_id: str,
        local_path: str,
        actual_bytes: int,
        is_oversize: bool,
    ) -> MaterializationLease:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE text_materialization_leases
                    SET
                        local_path = %s,
                        actual_bytes = %s,
                        is_oversize = %s,
                        updated_at = now()
                    WHERE lease_id = %s AND status = %s
                    """,
                    (
                        local_path,
                        actual_bytes,
                        is_oversize,
                        lease_id,
                        LEASE_ACTIVE_STATUS,
                    ),
                )
                lease = self._materialization_lease_by_id(cursor, lease_id)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        if lease is None:
            raise RuntimeError(f"Materialization lease not found: {lease_id}")
        return lease

    def fail_materialization_lease(self, lease_id: str, error: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE text_materialization_leases
                SET
                    status = %s,
                    error = %s,
                    updated_at = now(),
                    released_at = now()
                WHERE lease_id = %s AND status = %s
                """,
                (
                    LEASE_FAILED_STATUS,
                    error[:2000],
                    lease_id,
                    LEASE_ACTIVE_STATUS,
                ),
            )
        self._conn.commit()

    def release_materialization_lease(self, file_id: str) -> list[str]:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT local_path
                FROM text_materialization_leases
                WHERE file_id = %s AND status = %s AND local_path IS NOT NULL
                """,
                (file_id, LEASE_ACTIVE_STATUS),
            )
            paths = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                """
                UPDATE text_materialization_leases
                SET
                    status = %s,
                    updated_at = now(),
                    released_at = now()
                WHERE file_id = %s AND status = %s
                """,
                (LEASE_RELEASED_STATUS, file_id, LEASE_ACTIVE_STATUS),
            )
        self._conn.commit()
        return paths

    def save_pdf_result(
        self,
        result: PdfProcessingResult,
        publish_downstream: bool,
    ) -> PdfProcessingResult:
        try:
            from psycopg.types.json import Json

            with self._conn.cursor() as cursor:
                previous_ocr_outbox = self._existing_ocr_outbox_ids(cursor, result)
                previous_entity_outbox_id = self._existing_entity_outbox_id(cursor, result)

                cursor.execute(
                    "DELETE FROM text_chunks_staging WHERE file_id = %s",
                    (result.message.file_id,),
                )
                cursor.execute(
                    "DELETE FROM text_extraction_pages WHERE file_id = %s",
                    (result.message.file_id,),
                )

                batch_ocr_outbox_id = None
                batch_ocr_requested_at = None
                ocr_pages = [page for page in result.pages if page.needs_ocr]
                if ocr_pages and publish_downstream:
                    first_previous_ocr = next(
                        (
                            previous_ocr_outbox.get(page.page_number)
                            for page in ocr_pages
                            if previous_ocr_outbox.get(page.page_number) is not None
                        ),
                        None,
                    )
                    if first_previous_ocr is not None:
                        batch_ocr_outbox_id = first_previous_ocr["outbox_id"]
                        batch_ocr_requested_at = first_previous_ocr["ocr_requested_at"]
                    if batch_ocr_outbox_id is None:
                        batch_ocr_outbox_id = str(uuid.uuid4())
                    if batch_ocr_requested_at is None:
                        batch_ocr_requested_at = datetime.now(UTC)
                    self._insert_outbox_if_needed(
                        cursor=cursor,
                        outbox_id=batch_ocr_outbox_id,
                        run_id=result.message.run_id,
                        file_id=result.message.file_id,
                        queue_name=QUEUE_OCR_URGENT,
                        payload=build_ocr_batch_request_payload(
                            result.message,
                            result.stored_file,
                            ocr_pages,
                            batch_ocr_requested_at,
                        ),
                        json_wrapper=Json,
                    )

                page_results = []
                for page in result.pages:
                    if page.needs_ocr and publish_downstream:
                        page_results.append(
                            page.with_ocr_outbox_id(
                                batch_ocr_outbox_id,
                                batch_ocr_requested_at,
                            )
                        )
                    else:
                        page_results.append(page)

                entity_outbox_id = None
                if result.is_ready_for_entity and publish_downstream:
                    entity_outbox_id = previous_entity_outbox_id
                    if entity_outbox_id is None:
                        entity_outbox_id = str(uuid.uuid4())
                    self._insert_outbox_if_needed(
                        cursor=cursor,
                        outbox_id=entity_outbox_id,
                        run_id=result.message.run_id,
                        file_id=result.message.file_id,
                        queue_name=QUEUE_ENTITY,
                        payload=build_chunks_ready_payload(result),
                        json_wrapper=Json,
                    )

                saved_result = result.with_outbox_ids(
                    pages=page_results,
                    entity_outbox_id=entity_outbox_id,
                )
                self._upsert_extraction_file(cursor, saved_result)
                self._insert_pages(cursor, saved_result)
                self._insert_chunks(cursor, saved_result, Json)
                cursor.execute(
                    """
                    UPDATE files
                    SET status = %s, updated_at = now()
                    WHERE file_id = %s
                    """,
                    (saved_result.status, saved_result.message.file_id),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return saved_result

    def save_ocr_result(
        self,
        result: OcrProcessingResult,
        publish_downstream: bool,
    ) -> OcrProcessingResult:
        try:
            from psycopg.types.json import Json

            with self._conn.cursor() as cursor:
                file_state = self._get_extraction_file_state(
                    cursor,
                    result.message.file_id,
                )
                if result.message.is_pdf_page and file_state is None:
                    raise ValueError(
                        "OCR PDF page has no parent text_extraction_files row: "
                        f"{result.message.file_id}"
                    )

                existing_page_ocr = self._existing_page_ocr_state(
                    cursor,
                    result.message.file_id,
                    result.page.page_number,
                )
                page = result.page
                if page.ocr_requested_at is None and existing_page_ocr[
                    "ocr_requested_at"
                ] is not None:
                    page = page.with_ocr_metrics(
                        requested_at=existing_page_ocr["ocr_requested_at"],
                        started_at=result.started_at,
                        completed_at=result.completed_at,
                        processing_seconds=result.processing_seconds,
                        cpu_user_seconds=page.cpu_user_seconds,
                        cpu_system_seconds=page.cpu_system_seconds,
                        cpu_total_seconds=page.cpu_total_seconds,
                        peak_memory_mb=page.peak_memory_mb,
                    )
                page = page.with_ocr_outbox_id(
                    existing_page_ocr["ocr_outbox_id"],
                    page.ocr_requested_at,
                )
                existing_chunks = self._list_text_chunks(cursor, result.message.file_id)
                kept_chunks = [
                    chunk
                    for chunk in existing_chunks
                    if not (
                        chunk.page_start == page.page_number
                        and chunk.page_end == page.page_number
                    )
                ]
                combined_chunks = [*kept_chunks]
                if page.status == PAGE_COMPLETED_STATUS:
                    combined_chunks.extend(result.chunks)
                reindexed_chunks = self._reindex_chunks(combined_chunks)

                cursor.execute(
                    "DELETE FROM text_chunks_staging WHERE file_id = %s",
                    (result.message.file_id,),
                )
                self._insert_text_chunks(cursor, reindexed_chunks, Json)
                self._upsert_page(cursor, page)

                pages = self._list_pages(cursor, result.message.file_id)
                total_pages = max(
                    int(file_state["total_pages"]) if file_state else 0,
                    len(pages),
                    result.page.page_number,
                )
                completed_pages = sum(
                    1 for item in pages if item.status == PAGE_COMPLETED_STATUS
                )
                pending_ocr_pages = sum(
                    1 for item in pages if item.status == PAGE_PENDING_OCR_STATUS
                )
                failed_pages = sum(1 for item in pages if item.status == PAGE_FAILED_STATUS)
                file_status = self._file_status_from_page_counts(
                    pending_ocr_pages=pending_ocr_pages,
                    failed_pages=failed_pages,
                )
                started_at = (
                    file_state["started_at"] if file_state else result.started_at
                )
                completed_at = (
                    result.completed_at
                    if file_status
                    in {TEXT_EXTRACTION_COMPLETED_STATUS, TEXT_EXTRACTION_FAILED_STATUS}
                    else None
                )
                processing_seconds = self._elapsed_from_started_at(
                    started_at,
                    completed_at,
                )
                previous_entity_outbox_id = (
                    file_state["entity_outbox_id"]
                    if (
                        file_state
                        and file_state["run_id"] == result.message.run_id
                        and file_state["entity_outbox_run_id"] == result.message.run_id
                    )
                    else None
                )
                entity_outbox_id = (
                    previous_entity_outbox_id
                    if (
                        previous_entity_outbox_id
                        and file_status == TEXT_EXTRACTION_COMPLETED_STATUS
                    )
                    else None
                )

                if file_status == TEXT_EXTRACTION_COMPLETED_STATUS and publish_downstream:
                    if entity_outbox_id is None:
                        entity_outbox_id = str(uuid.uuid4())
                    self._insert_outbox_if_needed(
                        cursor=cursor,
                        outbox_id=entity_outbox_id,
                        run_id=result.message.run_id,
                        file_id=result.message.file_id,
                        queue_name=QUEUE_ENTITY,
                        payload=build_chunks_ready_payload_for_file(
                            run_id=result.message.run_id,
                            file_id=result.message.file_id,
                            routing_decision_id=result.message.routing_decision_id,
                            stored_file=result.stored_file,
                            source_queue_name=result.message.destination_queue_name,
                            chunk_count=len(reindexed_chunks),
                            page_count=total_pages,
                        ),
                        json_wrapper=Json,
                    )
                elif not publish_downstream:
                    entity_outbox_id = previous_entity_outbox_id

                self._upsert_extraction_file_values(
                    cursor=cursor,
                    file_id=result.message.file_id,
                    run_id=result.message.run_id,
                    routing_decision_id=result.message.routing_decision_id,
                    status=file_status,
                    total_pages=total_pages,
                    completed_pages=completed_pages,
                    pending_ocr_pages=pending_ocr_pages,
                    failed_pages=failed_pages,
                    chunk_count=len(reindexed_chunks),
                    entity_outbox_id=entity_outbox_id,
                    error=(
                        result.error
                        if file_status == TEXT_EXTRACTION_FAILED_STATUS
                        else None
                    ),
                    started_at=started_at,
                    completed_at=completed_at,
                    processing_seconds=processing_seconds,
                    embedded_text_seconds=_sum_page_seconds(
                        pages,
                        "embedded_processing_seconds",
                    ),
                    ocr_queue_wait_seconds=_sum_page_seconds(
                        pages,
                        "ocr_queue_wait_seconds",
                    ),
                    ocr_processing_seconds=_sum_page_seconds(
                        pages,
                        "ocr_processing_seconds",
                    ),
                    ocr_processing_wall_seconds=_ocr_wall_seconds(pages),
                    cpu_user_seconds=_sum_page_seconds(
                        pages,
                        "cpu_user_seconds",
                    ),
                    cpu_system_seconds=_sum_page_seconds(
                        pages,
                        "cpu_system_seconds",
                    ),
                    cpu_total_seconds=_sum_page_seconds(
                        pages,
                        "cpu_total_seconds",
                    ),
                    peak_memory_mb=_max_page_value(
                        pages,
                        "peak_memory_mb",
                    ),
                )
                cursor.execute(
                    """
                    UPDATE files
                    SET status = %s, updated_at = now()
                    WHERE file_id = %s
                    """,
                    (file_status, result.message.file_id),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return result.with_persisted_state(
            file_status=file_status,
            total_pages=total_pages,
            completed_pages=completed_pages,
            pending_ocr_pages=pending_ocr_pages,
            failed_pages=failed_pages,
            file_chunk_count=len(reindexed_chunks),
            entity_outbox_id=entity_outbox_id,
        )

    def save_ocr_batch_metrics(self, metrics: OcrBatchMetrics) -> None:
        try:
            from psycopg.types.json import Json

            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO text_ocr_batches (
                        batch_id,
                        file_id,
                        run_id,
                        page_numbers,
                        requested_device,
                        effective_device,
                        cuda_available,
                        gpu_name,
                        cuda_visible_devices,
                        mineru_device,
                        started_at,
                        completed_at,
                        wall_seconds,
                        mineru_command_count,
                        fallback_level,
                        error
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (batch_id) DO UPDATE
                    SET
                        page_numbers = EXCLUDED.page_numbers,
                        requested_device = EXCLUDED.requested_device,
                        effective_device = EXCLUDED.effective_device,
                        cuda_available = EXCLUDED.cuda_available,
                        gpu_name = EXCLUDED.gpu_name,
                        cuda_visible_devices = EXCLUDED.cuda_visible_devices,
                        mineru_device = EXCLUDED.mineru_device,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        wall_seconds = EXCLUDED.wall_seconds,
                        mineru_command_count = EXCLUDED.mineru_command_count,
                        fallback_level = EXCLUDED.fallback_level,
                        error = EXCLUDED.error,
                        updated_at = now()
                    """,
                    (
                        metrics.batch_id,
                        metrics.file_id,
                        metrics.run_id,
                        Json(list(metrics.page_numbers)),
                        metrics.requested_device,
                        metrics.effective_device,
                        metrics.cuda_available,
                        metrics.gpu_name,
                        metrics.cuda_visible_devices,
                        metrics.mineru_device,
                        metrics.started_at,
                        metrics.completed_at,
                        metrics.wall_seconds,
                        metrics.mineru_command_count,
                        metrics.fallback_level,
                        metrics.error,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def save_doc_result(
        self,
        result: DocProcessingResult,
        publish_downstream: bool,
    ) -> DocProcessingResult:
        try:
            from psycopg.types.json import Json

            with self._conn.cursor() as cursor:
                previous_entity_outbox_id = self._existing_entity_outbox_id_for_file(
                    cursor,
                    result.message.file_id,
                    result.message.run_id,
                )

                cursor.execute(
                    "DELETE FROM text_chunks_staging WHERE file_id = %s",
                    (result.message.file_id,),
                )
                cursor.execute(
                    "DELETE FROM text_extraction_pages WHERE file_id = %s",
                    (result.message.file_id,),
                )

                entity_outbox_id = None
                if result.is_ready_for_entity and publish_downstream:
                    entity_outbox_id = previous_entity_outbox_id
                    if entity_outbox_id is None:
                        entity_outbox_id = str(uuid.uuid4())
                    self._insert_outbox_if_needed(
                        cursor=cursor,
                        outbox_id=entity_outbox_id,
                        run_id=result.message.run_id,
                        file_id=result.message.file_id,
                        queue_name=QUEUE_ENTITY,
                        payload=build_chunks_ready_payload_for_file(
                            run_id=result.message.run_id,
                            file_id=result.message.file_id,
                            routing_decision_id=result.message.routing_decision_id,
                            stored_file=result.stored_file,
                            source_queue_name=QUEUE_DOC,
                            chunk_count=result.chunk_count,
                            page_count=result.total_pages,
                        ),
                        json_wrapper=Json,
                    )

                saved_result = result.with_outbox_ids(
                    pages=result.pages,
                    entity_outbox_id=entity_outbox_id,
                )
                self._upsert_extraction_file(cursor, saved_result)
                self._insert_pages(cursor, saved_result)
                self._insert_chunks(cursor, saved_result, Json)
                cursor.execute(
                    """
                    UPDATE files
                    SET status = %s, updated_at = now()
                    WHERE file_id = %s
                    """,
                    (saved_result.status, saved_result.message.file_id),
                )

            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return saved_result

    def list_pending_outbox(self, queue_name: str) -> list[OutboxMessage]:
        if queue_name not in DESTINATION_QUEUE_NAMES:
            return []

        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT outbox_id, queue_name, payload
                FROM queue_outbox
                WHERE queue_name = %s AND status = %s
                ORDER BY created_at, outbox_id
                """,
                (queue_name, "pending"),
            )
            rows = cursor.fetchall()

        return [
            OutboxMessage(
                outbox_id=str(row[0]),
                queue_name=str(row[1]),
                payload=dict(row[2]),
            )
            for row in rows
        ]

    def mark_outbox_published(self, outbox_id: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE queue_outbox
                SET
                    status = %s,
                    published_at = now(),
                    updated_at = now(),
                    last_error = NULL
                WHERE outbox_id = %s
                """,
                ("published", outbox_id),
            )
        self._conn.commit()

    def record_outbox_error(self, outbox_id: str, error: str) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE queue_outbox
                SET
                    attempts = attempts + 1,
                    last_error = %s,
                    updated_at = now()
                WHERE outbox_id = %s
                """,
                (error[:2000], outbox_id),
            )
        self._conn.commit()

    def _expire_materialization_leases(self, cursor: Any) -> list[str]:
        cursor.execute(
            """
            UPDATE text_materialization_leases
            SET
                status = %s,
                reason = COALESCE(reason, %s),
                updated_at = now(),
                    released_at = now()
            WHERE status = %s AND expires_at <= now()
            RETURNING local_path
            """,
            (
                LEASE_EXPIRED_STATUS,
                "lease_expired",
                LEASE_ACTIVE_STATUS,
            ),
        )
        return [str(row[0]) for row in cursor.fetchall() if row[0] is not None]

    def _active_materialization_lease(
        self,
        cursor: Any,
        file_id: str,
    ) -> MaterializationLease | None:
        cursor.execute(
            """
            SELECT
                lease_id,
                file_id,
                run_id,
                source_uri,
                local_path,
                expected_bytes,
                actual_bytes,
                is_oversize,
                status
            FROM text_materialization_leases
            WHERE file_id = %s AND status = %s AND expires_at > now()
            ORDER BY acquired_at DESC
            LIMIT 1
            """,
            (file_id, LEASE_ACTIVE_STATUS),
        )
        return _materialization_lease_from_row(cursor.fetchone())

    def _materialization_lease_by_id(
        self,
        cursor: Any,
        lease_id: str,
    ) -> MaterializationLease | None:
        cursor.execute(
            """
            SELECT
                lease_id,
                file_id,
                run_id,
                source_uri,
                local_path,
                expected_bytes,
                actual_bytes,
                is_oversize,
                status
            FROM text_materialization_leases
            WHERE lease_id = %s
            """,
            (lease_id,),
        )
        return _materialization_lease_from_row(cursor.fetchone())

    def _materialization_budget_snapshot(
        self,
        cursor: Any,
        exclude_lease_id: str | None = None,
    ) -> BudgetSnapshot:
        params: list[object] = [LEASE_ACTIVE_STATUS]
        exclude_clause = ""
        if exclude_lease_id is not None:
            exclude_clause = "AND lease_id <> %s"
            params.append(exclude_lease_id)
        cursor.execute(
            f"""
            SELECT
                COALESCE(
                    SUM(
                        CASE
                            WHEN NOT is_oversize
                            THEN GREATEST(actual_bytes, COALESCE(expected_bytes, 0))
                            ELSE 0
                        END
                    ),
                    0
                ) AS active_small_bytes,
                COALESCE(
                    SUM(GREATEST(actual_bytes, COALESCE(expected_bytes, 0))),
                    0
                ) AS active_total_bytes
            FROM text_materialization_leases
            WHERE status = %s AND expires_at > now()
            {exclude_clause}
            """,
            tuple(params),
        )
        row = cursor.fetchone()
        if row is None:
            return BudgetSnapshot(active_small_bytes=0, active_total_bytes=0)
        return BudgetSnapshot(
            active_small_bytes=int(row[0] or 0),
            active_total_bytes=int(row[1] or 0),
        )

    def _insert_materialization_lease(
        self,
        cursor: Any,
        stored_file: StoredFile,
        config: MaterializationConfig,
        lease_id: str,
        status: str,
        is_oversize: bool,
        reason: str | None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO text_materialization_leases (
                lease_id,
                file_id,
                run_id,
                worker_id,
                source_uri,
                expected_bytes,
                actual_bytes,
                is_oversize,
                status,
                reason,
                expires_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                now() + (%s * interval '1 second')
            )
            """,
            (
                lease_id,
                stored_file.file_id,
                stored_file.run_id,
                config.worker_id,
                stored_file.source_uri,
                stored_file.size_bytes,
                0,
                is_oversize,
                status,
                reason,
                config.lease_ttl_seconds,
            ),
        )

    def _existing_ocr_outbox_ids(
        self,
        cursor: Any,
        result: PdfProcessingResult,
    ) -> dict[int, dict[str, Any]]:
        cursor.execute(
            """
            SELECT page_number, ocr_outbox_id, ocr_requested_at
            FROM text_extraction_pages
            WHERE file_id = %s AND ocr_outbox_id IS NOT NULL
            """,
            (result.message.file_id,),
        )
        return {
            int(row[0]): {
                "outbox_id": str(row[1]),
                "ocr_requested_at": row[2],
            }
            for row in cursor.fetchall()
        }

    def _existing_entity_outbox_id(
        self,
        cursor: Any,
        result: PdfProcessingResult,
    ) -> str | None:
        return self._existing_entity_outbox_id_for_file(
            cursor,
            result.message.file_id,
            result.message.run_id,
        )

    def _existing_entity_outbox_id_for_file(
        self,
        cursor: Any,
        file_id: str,
        run_id: str,
    ) -> str | None:
        cursor.execute(
            """
            SELECT
                text_extraction_files.run_id,
                text_extraction_files.entity_outbox_id,
                queue_outbox.run_id
            FROM text_extraction_files
            LEFT JOIN queue_outbox
                ON queue_outbox.outbox_id = text_extraction_files.entity_outbox_id
            WHERE text_extraction_files.file_id = %s
            """,
            (file_id,),
        )
        row = cursor.fetchone()
        if row is None or row[1] is None:
            return None
        if str(row[0]) != run_id or str(row[2]) != run_id:
            return None
        return str(row[1])

    def _existing_page_ocr_state(
        self,
        cursor: Any,
        file_id: str,
        page_number: int,
    ) -> dict[str, Any]:
        cursor.execute(
            """
            SELECT ocr_outbox_id, ocr_requested_at
            FROM text_extraction_pages
            WHERE file_id = %s AND page_number = %s
            """,
            (file_id, page_number),
        )
        row = cursor.fetchone()
        if row is None:
            return {"ocr_outbox_id": None, "ocr_requested_at": None}
        return {
            "ocr_outbox_id": str(row[0]) if row[0] is not None else None,
            "ocr_requested_at": row[1],
        }

    def _get_extraction_file_state(
        self,
        cursor: Any,
        file_id: str,
    ) -> dict[str, Any] | None:
        cursor.execute(
            """
            SELECT
                text_extraction_files.run_id,
                text_extraction_files.status,
                text_extraction_files.total_pages,
                text_extraction_files.entity_outbox_id,
                text_extraction_files.started_at,
                queue_outbox.run_id
            FROM text_extraction_files
            LEFT JOIN queue_outbox
                ON queue_outbox.outbox_id = text_extraction_files.entity_outbox_id
            WHERE text_extraction_files.file_id = %s
            """,
            (file_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "status": str(row[1]),
            "total_pages": int(row[2]),
            "entity_outbox_id": str(row[3]) if row[3] is not None else None,
            "started_at": row[4],
            "entity_outbox_run_id": str(row[5]) if row[5] is not None else None,
        }

    def _insert_outbox_if_needed(
        self,
        cursor: Any,
        outbox_id: str,
        run_id: str,
        file_id: str,
        queue_name: str,
        payload: dict[str, Any],
        json_wrapper: Any,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO queue_outbox (
                outbox_id,
                run_id,
                file_id,
                queue_name,
                payload,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (outbox_id) DO NOTHING
            """,
            (
                outbox_id,
                run_id,
                file_id,
                queue_name,
                json_wrapper(payload),
                "pending",
            ),
        )

    def _upsert_extraction_file(
        self,
        cursor: Any,
        result: PdfProcessingResult | DocProcessingResult,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO text_extraction_files (
                file_id,
                run_id,
                routing_decision_id,
                status,
                total_pages,
                completed_pages,
                pending_ocr_pages,
                failed_pages,
                chunk_count,
                entity_outbox_id,
                error,
                started_at,
                completed_at,
                processing_seconds,
                embedded_text_seconds,
                ocr_queue_wait_seconds,
                ocr_processing_seconds,
                ocr_processing_wall_seconds,
                cpu_user_seconds,
                cpu_system_seconds,
                cpu_total_seconds,
                peak_memory_mb
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (file_id) DO UPDATE
            SET
                status = EXCLUDED.status,
                total_pages = EXCLUDED.total_pages,
                completed_pages = EXCLUDED.completed_pages,
                pending_ocr_pages = EXCLUDED.pending_ocr_pages,
                failed_pages = EXCLUDED.failed_pages,
                chunk_count = EXCLUDED.chunk_count,
                entity_outbox_id = EXCLUDED.entity_outbox_id,
                error = EXCLUDED.error,
                started_at = EXCLUDED.started_at,
                updated_at = now(),
                completed_at = EXCLUDED.completed_at,
                processing_seconds = EXCLUDED.processing_seconds,
                embedded_text_seconds = EXCLUDED.embedded_text_seconds,
                ocr_queue_wait_seconds = EXCLUDED.ocr_queue_wait_seconds,
                ocr_processing_seconds = EXCLUDED.ocr_processing_seconds,
                ocr_processing_wall_seconds = EXCLUDED.ocr_processing_wall_seconds,
                cpu_user_seconds = EXCLUDED.cpu_user_seconds,
                cpu_system_seconds = EXCLUDED.cpu_system_seconds,
                cpu_total_seconds = EXCLUDED.cpu_total_seconds,
                peak_memory_mb = EXCLUDED.peak_memory_mb
            """,
            (
                result.message.file_id,
                result.message.run_id,
                result.message.routing_decision_id,
                result.status,
                result.total_pages,
                result.completed_pages,
                result.pending_ocr_pages,
                result.failed_pages,
                result.chunk_count,
                result.entity_outbox_id,
                result.error,
                result.started_at,
                result.completed_at,
                result.processing_seconds,
                result.embedded_text_seconds,
                result.ocr_queue_wait_seconds,
                result.ocr_processing_seconds,
                result.ocr_processing_wall_seconds,
                result.cpu_user_seconds,
                result.cpu_system_seconds,
                result.cpu_total_seconds,
                result.peak_memory_mb,
            ),
        )

    def _upsert_extraction_file_values(
        self,
        cursor: Any,
        file_id: str,
        run_id: str,
        routing_decision_id: str,
        status: str,
        total_pages: int,
        completed_pages: int,
        pending_ocr_pages: int,
        failed_pages: int,
        chunk_count: int,
        entity_outbox_id: str | None,
        error: str | None,
        started_at: datetime,
        completed_at: datetime | None,
        processing_seconds: float | None,
        embedded_text_seconds: float,
        ocr_queue_wait_seconds: float,
        ocr_processing_seconds: float,
        ocr_processing_wall_seconds: float,
        cpu_user_seconds: float,
        cpu_system_seconds: float,
        cpu_total_seconds: float,
        peak_memory_mb: float,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO text_extraction_files (
                file_id,
                run_id,
                routing_decision_id,
                status,
                total_pages,
                completed_pages,
                pending_ocr_pages,
                failed_pages,
                chunk_count,
                entity_outbox_id,
                error,
                started_at,
                completed_at,
                processing_seconds,
                embedded_text_seconds,
                ocr_queue_wait_seconds,
                ocr_processing_seconds,
                ocr_processing_wall_seconds,
                cpu_user_seconds,
                cpu_system_seconds,
                cpu_total_seconds,
                peak_memory_mb
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (file_id) DO UPDATE
            SET
                status = EXCLUDED.status,
                total_pages = EXCLUDED.total_pages,
                completed_pages = EXCLUDED.completed_pages,
                pending_ocr_pages = EXCLUDED.pending_ocr_pages,
                failed_pages = EXCLUDED.failed_pages,
                chunk_count = EXCLUDED.chunk_count,
                entity_outbox_id = EXCLUDED.entity_outbox_id,
                error = EXCLUDED.error,
                updated_at = now(),
                completed_at = EXCLUDED.completed_at,
                processing_seconds = EXCLUDED.processing_seconds,
                embedded_text_seconds = EXCLUDED.embedded_text_seconds,
                ocr_queue_wait_seconds = EXCLUDED.ocr_queue_wait_seconds,
                ocr_processing_seconds = EXCLUDED.ocr_processing_seconds,
                ocr_processing_wall_seconds = EXCLUDED.ocr_processing_wall_seconds,
                cpu_user_seconds = EXCLUDED.cpu_user_seconds,
                cpu_system_seconds = EXCLUDED.cpu_system_seconds,
                cpu_total_seconds = EXCLUDED.cpu_total_seconds,
                peak_memory_mb = EXCLUDED.peak_memory_mb
            """,
            (
                file_id,
                run_id,
                routing_decision_id,
                status,
                total_pages,
                completed_pages,
                pending_ocr_pages,
                failed_pages,
                chunk_count,
                entity_outbox_id,
                error,
                started_at,
                completed_at,
                processing_seconds,
                embedded_text_seconds,
                ocr_queue_wait_seconds,
                ocr_processing_seconds,
                ocr_processing_wall_seconds,
                cpu_user_seconds,
                cpu_system_seconds,
                cpu_total_seconds,
                peak_memory_mb,
            ),
        )

    def _insert_pages(
        self,
        cursor: Any,
        result: PdfProcessingResult | DocProcessingResult,
    ) -> None:
        for page in result.pages:
            self._upsert_page(cursor, page)

    def _upsert_page(self, cursor: Any, page: PdfPageResult) -> None:
        cursor.execute(
            """
            INSERT INTO text_extraction_pages (
                file_id,
                run_id,
                page_number,
                page_index,
                method,
                status,
                reason,
                char_count,
                word_count,
                total_image_ratio,
                largest_image_ratio,
                chunk_count,
                ocr_outbox_id,
                error,
                embedded_started_at,
                embedded_completed_at,
                embedded_processing_seconds,
                ocr_requested_at,
                ocr_started_at,
                ocr_completed_at,
                ocr_queue_wait_seconds,
                ocr_processing_seconds,
                cpu_user_seconds,
                cpu_system_seconds,
                cpu_total_seconds,
                peak_memory_mb
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (file_id, page_number) DO UPDATE
            SET
                method = EXCLUDED.method,
                status = EXCLUDED.status,
                reason = EXCLUDED.reason,
                char_count = EXCLUDED.char_count,
                word_count = EXCLUDED.word_count,
                total_image_ratio = EXCLUDED.total_image_ratio,
                largest_image_ratio = EXCLUDED.largest_image_ratio,
                chunk_count = EXCLUDED.chunk_count,
                ocr_outbox_id = COALESCE(
                    text_extraction_pages.ocr_outbox_id,
                    EXCLUDED.ocr_outbox_id
                ),
                error = EXCLUDED.error,
                embedded_started_at = EXCLUDED.embedded_started_at,
                embedded_completed_at = EXCLUDED.embedded_completed_at,
                embedded_processing_seconds = EXCLUDED.embedded_processing_seconds,
                ocr_requested_at = COALESCE(
                    text_extraction_pages.ocr_requested_at,
                    EXCLUDED.ocr_requested_at
                ),
                ocr_started_at = EXCLUDED.ocr_started_at,
                ocr_completed_at = EXCLUDED.ocr_completed_at,
                ocr_queue_wait_seconds = EXCLUDED.ocr_queue_wait_seconds,
                ocr_processing_seconds = EXCLUDED.ocr_processing_seconds,
                cpu_user_seconds = EXCLUDED.cpu_user_seconds,
                cpu_system_seconds = EXCLUDED.cpu_system_seconds,
                cpu_total_seconds = EXCLUDED.cpu_total_seconds,
                peak_memory_mb = EXCLUDED.peak_memory_mb,
                updated_at = now()
            """,
            (
                page.file_id,
                page.run_id,
                page.page_number,
                page.page_index,
                page.method,
                page.status,
                page.reason,
                page.char_count,
                page.word_count,
                page.total_image_ratio,
                page.largest_image_ratio,
                page.chunk_count,
                page.ocr_outbox_id,
                page.error,
                page.embedded_started_at,
                page.embedded_completed_at,
                page.embedded_processing_seconds,
                page.ocr_requested_at,
                page.ocr_started_at,
                page.ocr_completed_at,
                page.ocr_queue_wait_seconds,
                page.ocr_processing_seconds,
                page.cpu_user_seconds,
                page.cpu_system_seconds,
                page.cpu_total_seconds,
                page.peak_memory_mb,
            ),
        )

    def _insert_chunks(
        self,
        cursor: Any,
        result: PdfProcessingResult | DocProcessingResult,
        json_wrapper: Any,
    ) -> None:
        self._insert_text_chunks(cursor, result.chunks, json_wrapper)

    def _insert_text_chunks(
        self,
        cursor: Any,
        chunks: list[TextChunk],
        json_wrapper: Any,
    ) -> None:
        for chunk in chunks:
            cursor.execute(
                """
                INSERT INTO text_chunks_staging (
                    chunk_id,
                    run_id,
                    file_id,
                    chunk_index,
                    page_start,
                    page_end,
                    text,
                    text_hash_sha256,
                    source_map,
                    method,
                    status,
                    expires_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    now() + (%s * interval '1 hour')
                )
                ON CONFLICT (chunk_id) DO UPDATE
                SET
                    chunk_index = EXCLUDED.chunk_index,
                    page_start = EXCLUDED.page_start,
                    page_end = EXCLUDED.page_end,
                    text = EXCLUDED.text,
                    text_hash_sha256 = EXCLUDED.text_hash_sha256,
                    source_map = EXCLUDED.source_map,
                    method = EXCLUDED.method,
                    status = EXCLUDED.status,
                    expires_at = EXCLUDED.expires_at,
                    updated_at = now()
                """,
                (
                    chunk.chunk_id,
                    chunk.run_id,
                    chunk.file_id,
                    chunk.chunk_index,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.text,
                    chunk.text_hash_sha256,
                    json_wrapper(chunk.source_map),
                    chunk.method,
                    chunk.status,
                    self._chunk_ttl_hours,
                ),
            )

    def _list_pages(self, cursor: Any, file_id: str) -> list[PdfPageResult]:
        cursor.execute(
            """
            SELECT
                file_id,
                run_id,
                page_number,
                page_index,
                method,
                status,
                reason,
                char_count,
                word_count,
                total_image_ratio,
                largest_image_ratio,
                chunk_count,
                ocr_outbox_id,
                error,
                embedded_started_at,
                embedded_completed_at,
                embedded_processing_seconds,
                ocr_requested_at,
                ocr_started_at,
                ocr_completed_at,
                ocr_queue_wait_seconds,
                ocr_processing_seconds,
                cpu_user_seconds,
                cpu_system_seconds,
                cpu_total_seconds,
                peak_memory_mb
            FROM text_extraction_pages
            WHERE file_id = %s
            ORDER BY page_number
            """,
            (file_id,),
        )
        return [
            PdfPageResult(
                file_id=str(row[0]),
                run_id=str(row[1]),
                page_number=int(row[2]),
                page_index=int(row[3]),
                method=str(row[4]),
                status=str(row[5]),
                reason=str(row[6]),
                char_count=int(row[7]),
                word_count=int(row[8]),
                total_image_ratio=float(row[9]),
                largest_image_ratio=float(row[10]),
                chunk_count=int(row[11]),
                ocr_outbox_id=str(row[12]) if row[12] is not None else None,
                error=str(row[13]) if row[13] is not None else None,
                embedded_started_at=row[14],
                embedded_completed_at=row[15],
                embedded_processing_seconds=(
                    float(row[16]) if row[16] is not None else None
                ),
                ocr_requested_at=row[17],
                ocr_started_at=row[18],
                ocr_completed_at=row[19],
                ocr_queue_wait_seconds=(
                    float(row[20]) if row[20] is not None else None
                ),
                ocr_processing_seconds=(
                    float(row[21]) if row[21] is not None else None
                ),
                cpu_user_seconds=(
                    float(row[22]) if row[22] is not None else None
                ),
                cpu_system_seconds=(
                    float(row[23]) if row[23] is not None else None
                ),
                cpu_total_seconds=(
                    float(row[24]) if row[24] is not None else None
                ),
                peak_memory_mb=(
                    float(row[25]) if row[25] is not None else None
                ),
            )
            for row in cursor.fetchall()
        ]

    def _list_text_chunks(self, cursor: Any, file_id: str) -> list[TextChunk]:
        cursor.execute(
            """
            SELECT
                chunk_id,
                run_id,
                file_id,
                chunk_index,
                page_start,
                page_end,
                text,
                text_hash_sha256,
                source_map,
                method,
                status
            FROM text_chunks_staging
            WHERE file_id = %s
            ORDER BY page_start, chunk_index, chunk_id
            """,
            (file_id,),
        )
        return [
            TextChunk(
                chunk_id=str(row[0]),
                run_id=str(row[1]),
                file_id=str(row[2]),
                chunk_index=int(row[3]),
                page_start=int(row[4]),
                page_end=int(row[5]),
                text=str(row[6]),
                text_hash_sha256=str(row[7]),
                source_map=dict(row[8]),
                method=str(row[9]),
                status=str(row[10]),
            )
            for row in cursor.fetchall()
        ]

    def _reindex_chunks(self, chunks: list[TextChunk]) -> list[TextChunk]:
        ordered_chunks = sorted(
            chunks,
            key=lambda chunk: (chunk.page_start, chunk.page_end, chunk.chunk_index),
        )
        return [
            replace(
                chunk,
                chunk_id=f"{chunk.file_id}:c{index:06d}",
                chunk_index=index,
            )
            for index, chunk in enumerate(ordered_chunks, start=1)
        ]

    def _file_status_from_page_counts(
        self,
        pending_ocr_pages: int,
        failed_pages: int,
    ) -> str:
        if failed_pages:
            return TEXT_EXTRACTION_FAILED_STATUS
        if pending_ocr_pages:
            return WAITING_OCR_STATUS
        return TEXT_EXTRACTION_COMPLETED_STATUS

    def _elapsed_from_started_at(
        self,
        started_at: datetime,
        completed_at: datetime | None,
    ) -> float | None:
        if completed_at is None:
            return None
        return round((completed_at - started_at).total_seconds(), 6)


def _materialization_lease_from_row(row: Any | None) -> MaterializationLease | None:
    if row is None:
        return None
    return MaterializationLease(
        lease_id=str(row[0]),
        file_id=str(row[1]),
        run_id=str(row[2]),
        source_uri=str(row[3]),
        local_path=str(row[4]) if row[4] is not None else None,
        expected_bytes=int(row[5]) if row[5] is not None else None,
        actual_bytes=int(row[6] or 0),
        is_oversize=bool(row[7]),
        status=str(row[8]),
    )


def _pdf_attempt_state_from_row(row: Any | None) -> PdfAttemptState:
    if row is None:
        raise RuntimeError("PDF attempt row was not found")
    return PdfAttemptState(
        file_id=str(row[0]),
        attempts=int(row[1]),
        max_attempts=int(row[2]),
        status=str(row[3]),
        first_attempt_at=row[4],
        last_attempt_at=row[5],
        last_error_at=row[6],
        last_error_type=str(row[7]) if row[7] is not None else None,
        last_error_message=str(row[8]) if row[8] is not None else None,
        last_error_traceback=str(row[9]) if row[9] is not None else None,
        quarantined_at=row[10],
        last_result_status=str(row[11]) if row[11] is not None else None,
    )


def _sum_page_seconds(pages: list[PdfPageResult], attribute: str) -> float:
    total = 0.0
    for page in pages:
        value = getattr(page, attribute)
        if value is not None:
            total += float(value)
    return round(total, 6)


def _max_page_value(pages: list[PdfPageResult], attribute: str) -> float:
    values = [
        float(value)
        for page in pages
        if (value := getattr(page, attribute)) is not None
    ]
    if not values:
        return 0.0
    return round(max(values), 6)


def _ocr_wall_seconds(pages: list[PdfPageResult]) -> float:
    started = [
        page.ocr_started_at
        for page in pages
        if page.ocr_started_at is not None and page.ocr_completed_at is not None
    ]
    completed = [
        page.ocr_completed_at
        for page in pages
        if page.ocr_started_at is not None and page.ocr_completed_at is not None
    ]
    if not started or not completed:
        return 0.0
    return round(max(0.0, (max(completed) - min(started)).total_seconds()), 6)
