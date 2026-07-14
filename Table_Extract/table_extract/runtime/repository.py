from __future__ import annotations

from typing import Any, Protocol
import uuid

from table_extract.materialization.models import (
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
from table_extract.runtime.models import StoredFile, TableExtractionRecord


class TableExtractRepository(Protocol):
    def get_file(self, file_id: str) -> StoredFile | None:
        ...

    def save_table_extraction_record(self, record: TableExtractionRecord) -> None:
        ...


class PostgresTableExtractRepository:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install psycopg with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        self._conn = psycopg.connect(database_url)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostgresTableExtractRepository":
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

    def save_table_extraction_record(self, record: TableExtractionRecord) -> None:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO table_extraction_files (
                        file_id,
                        run_id,
                        routing_decision_id,
                        status,
                        started_at,
                        completed_at,
                        processing_seconds,
                        cpu_user_seconds,
                        cpu_system_seconds,
                        cpu_total_seconds,
                        peak_memory_mb,
                        table_count,
                        column_count,
                        finding_count,
                        profile_json_path,
                        discovery_json_path,
                        error
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_id) DO UPDATE
                    SET
                        run_id = EXCLUDED.run_id,
                        routing_decision_id = EXCLUDED.routing_decision_id,
                        status = EXCLUDED.status,
                        started_at = EXCLUDED.started_at,
                        completed_at = EXCLUDED.completed_at,
                        processing_seconds = EXCLUDED.processing_seconds,
                        cpu_user_seconds = EXCLUDED.cpu_user_seconds,
                        cpu_system_seconds = EXCLUDED.cpu_system_seconds,
                        cpu_total_seconds = EXCLUDED.cpu_total_seconds,
                        peak_memory_mb = EXCLUDED.peak_memory_mb,
                        table_count = EXCLUDED.table_count,
                        column_count = EXCLUDED.column_count,
                        finding_count = EXCLUDED.finding_count,
                        profile_json_path = EXCLUDED.profile_json_path,
                        discovery_json_path = EXCLUDED.discovery_json_path,
                        error = EXCLUDED.error,
                        updated_at = now()
                    """,
                    (
                        record.file_id,
                        record.run_id,
                        record.routing_decision_id,
                        record.status,
                        record.started_at,
                        record.completed_at,
                        record.processing_seconds,
                        record.cpu_user_seconds,
                        record.cpu_system_seconds,
                        record.cpu_total_seconds,
                        record.peak_memory_mb,
                        record.table_count,
                        record.column_count,
                        record.finding_count,
                        record.profile_json_path,
                        record.discovery_json_path,
                        record.error,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def expire_materialization_leases(self) -> list[str]:
        try:
            with self._conn.cursor() as cursor:
                cursor.execute("LOCK TABLE table_materialization_leases IN EXCLUSIVE MODE")
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
                cursor.execute("LOCK TABLE table_materialization_leases IN EXCLUSIVE MODE")
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
                cursor.execute("LOCK TABLE table_materialization_leases IN EXCLUSIVE MODE")
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
                        UPDATE table_materialization_leases
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
                    UPDATE table_materialization_leases
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
                    UPDATE table_materialization_leases
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
                UPDATE table_materialization_leases
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
                FROM table_materialization_leases
                WHERE file_id = %s AND status = %s AND local_path IS NOT NULL
                """,
                (file_id, LEASE_ACTIVE_STATUS),
            )
            paths = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                """
                UPDATE table_materialization_leases
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

    def _expire_materialization_leases(self, cursor: Any) -> list[str]:
        cursor.execute(
            """
            UPDATE table_materialization_leases
            SET
                status = %s,
                updated_at = now(),
                released_at = now(),
                reason = COALESCE(reason, 'lease_expired')
            WHERE status IN (%s, %s)
              AND expires_at < now()
            RETURNING local_path
            """,
            (
                LEASE_EXPIRED_STATUS,
                LEASE_ACTIVE_STATUS,
                LEASE_DEFERRED_STATUS,
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
            FROM table_materialization_leases
            WHERE file_id = %s AND status = %s
            """,
            (file_id, LEASE_ACTIVE_STATUS),
        )
        row = cursor.fetchone()
        return _lease_from_row(row) if row else None

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
            FROM table_materialization_leases
            WHERE lease_id = %s
            """,
            (lease_id,),
        )
        row = cursor.fetchone()
        return _lease_from_row(row) if row else None

    def _materialization_budget_snapshot(
        self,
        cursor: Any,
        exclude_lease_id: str | None = None,
    ) -> BudgetSnapshot:
        if exclude_lease_id:
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN NOT is_oversize THEN actual_bytes ELSE 0 END), 0),
                    COALESCE(SUM(actual_bytes), 0)
                FROM table_materialization_leases
                WHERE status = %s AND lease_id <> %s
                """,
                (LEASE_ACTIVE_STATUS, exclude_lease_id),
            )
        else:
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN NOT is_oversize THEN actual_bytes ELSE 0 END), 0),
                    COALESCE(SUM(actual_bytes), 0)
                FROM table_materialization_leases
                WHERE status = %s
                """,
                (LEASE_ACTIVE_STATUS,),
            )
        row = cursor.fetchone()
        return BudgetSnapshot(active_small_bytes=int(row[0]), active_total_bytes=int(row[1]))

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
            INSERT INTO table_materialization_leases (
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
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s, now() + (%s * interval '1 second'))
            """,
            (
                lease_id,
                stored_file.file_id,
                stored_file.run_id,
                config.worker_id,
                stored_file.source_uri,
                stored_file.size_bytes,
                is_oversize,
                status,
                reason,
                config.lease_ttl_seconds,
            ),
        )


def _lease_from_row(row: Any) -> MaterializationLease:
    return MaterializationLease(
        lease_id=str(row[0]),
        file_id=str(row[1]),
        run_id=str(row[2]),
        source_uri=str(row[3]),
        local_path=str(row[4]) if row[4] is not None else None,
        expected_bytes=int(row[5]) if row[5] is not None else None,
        actual_bytes=int(row[6]),
        is_oversize=bool(row[7]),
        status=str(row[8]),
    )
