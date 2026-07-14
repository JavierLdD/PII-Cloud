from __future__ import annotations

from typing import Any

from models import CHUNK_READY_STATUS, EntityExtractionRecord, SourceFile, TextChunk


class PostgresEntityRepository:
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

    def __enter__(self) -> "PostgresEntityRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def get_file(self, file_id: str) -> SourceFile | None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    f.file_id,
                    f.run_id,
                    f.source_type,
                    f.source_uri,
                    f.external_id,
                    f.file_name,
                    f.relative_path,
                    f.extension,
                    f.mime_type,
                    f.size_bytes,
                    f.checksum_sha256,
                    f.content_hash,
                    f.etag,
                    te.status,
                    te.chunk_count
                FROM files f
                LEFT JOIN text_extraction_files te ON te.file_id = f.file_id
                WHERE f.file_id = %s
                """,
                (file_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return SourceFile(
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
            text_extraction_status=str(row[13]) if row[13] is not None else None,
            expected_chunk_count=int(row[14]) if row[14] is not None else None,
        )

    def list_ready_chunks(self, file_id: str) -> list[TextChunk]:
        with self._conn.cursor() as cursor:
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
                WHERE file_id = %s AND status = %s
                ORDER BY chunk_index, chunk_id
                """,
                (file_id, CHUNK_READY_STATUS),
            )
            rows = cursor.fetchall()

        return [self._chunk_from_row(row) for row in rows]

    def save_entity_extraction_record(self, record: EntityExtractionRecord) -> None:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO entity_extraction_files (
                    file_id,
                    run_id,
                    status,
                    started_at,
                    completed_at,
                    processing_seconds,
                    cpu_user_seconds,
                    cpu_system_seconds,
                    cpu_total_seconds,
                    peak_memory_mb,
                    raw_entity_count,
                    accepted_entity_count,
                    raw_json_path,
                    filtered_json_path,
                    error
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (file_id) DO UPDATE
                SET
                    run_id = EXCLUDED.run_id,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    completed_at = EXCLUDED.completed_at,
                    processing_seconds = EXCLUDED.processing_seconds,
                    cpu_user_seconds = EXCLUDED.cpu_user_seconds,
                    cpu_system_seconds = EXCLUDED.cpu_system_seconds,
                    cpu_total_seconds = EXCLUDED.cpu_total_seconds,
                    peak_memory_mb = EXCLUDED.peak_memory_mb,
                    raw_entity_count = EXCLUDED.raw_entity_count,
                    accepted_entity_count = EXCLUDED.accepted_entity_count,
                    raw_json_path = EXCLUDED.raw_json_path,
                    filtered_json_path = EXCLUDED.filtered_json_path,
                    error = EXCLUDED.error,
                    updated_at = now()
                """,
                (
                    record.file_id,
                    record.run_id,
                    record.status,
                    record.started_at,
                    record.completed_at,
                    record.processing_seconds,
                    record.cpu_user_seconds,
                    record.cpu_system_seconds,
                    record.cpu_total_seconds,
                    record.peak_memory_mb,
                    record.raw_entity_count,
                    record.accepted_entity_count,
                    record.raw_json_path,
                    record.filtered_json_path,
                    record.error,
                ),
            )
        self._conn.commit()

    def save_accepted_entities(
        self,
        *,
        file_id: str,
        run_id: str,
        accepted_entities: list[object],
    ) -> None:
        try:
            from psycopg.types.json import Json
        except ImportError as exc:
            raise RuntimeError("Missing psycopg JSON support.") from exc

        with self._conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM entity_extraction_entities WHERE file_id = %s",
                (file_id,),
            )
            for index, entity in enumerate(accepted_entities):
                payload = _entity_to_dict(entity)
                cursor.execute(
                    """
                    INSERT INTO entity_extraction_entities (
                        file_id,
                        run_id,
                        entity_id,
                        entity_index,
                        entity_type,
                        text,
                        normalized_value,
                        value_key,
                        source,
                        raw_entity_type,
                        score,
                        is_base,
                        validation_status,
                        confidence_level,
                        decision_score,
                        decision_method,
                        zero_shot_score,
                        zero_shot_label,
                        primary_location,
                        evidence
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        file_id,
                        run_id,
                        str(payload["entity_id"]),
                        index,
                        str(payload["entity_type"]),
                        str(payload["text"]),
                        _optional_text(payload.get("normalized_value")),
                        str(payload["value_key"]),
                        str(payload["source"]),
                        str(payload["raw_entity_type"]),
                        float(payload["score"]),
                        bool(payload["is_base"]),
                        str(payload["validation_status"]),
                        _optional_text(payload.get("confidence_level")),
                        _optional_float(payload.get("decision_score")),
                        _optional_text(payload.get("decision_method")),
                        _optional_float(payload.get("zero_shot_score")),
                        _optional_text(payload.get("zero_shot_label")),
                        Json(payload.get("primary_location") or {}),
                        Json(payload.get("evidence") or []),
                    ),
                )
        self._conn.commit()

    def release_materialization_lease(self, file_id: str) -> list[str]:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT local_path
                FROM text_materialization_leases
                WHERE file_id = %s
                    AND status = %s
                    AND local_path IS NOT NULL
                """,
                (file_id, "active"),
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
                ("released", file_id, "active"),
            )
        self._conn.commit()
        return paths

    @staticmethod
    def _chunk_from_row(row: tuple[Any, ...]) -> TextChunk:
        return TextChunk(
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


def _entity_to_dict(entity: object) -> dict[str, Any]:
    to_dict = getattr(entity, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict(mask_text=False))
    if isinstance(entity, dict):
        return dict(entity)
    raise TypeError(f"Unsupported accepted entity object: {type(entity)!r}")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)
