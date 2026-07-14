from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import uuid


PENDING_STATUS = "pending"
PUBLISHED_STATUS = "published"


@dataclass(frozen=True)
class PendingOutboxRecord:
    outbox_id: str
    queue_name: str
    payload: dict[str, Any]
    status: str


class PubSubOutboxRepository:
    def __init__(self, database_url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Missing psycopg dependency.") from exc

        self._conn = psycopg.connect(database_url)
        self._columns = self._load_queue_outbox_columns()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PubSubOutboxRepository":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def insert_pending(
        self,
        *,
        run_id: str,
        file_id: str,
        queue_name: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        attributes: Mapping[str, str],
    ) -> PendingOutboxRecord:
        from psycopg.types.json import Json

        outbox_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
        try:
            with self._conn.cursor() as cursor:
                if self._has_cloud_columns:
                    cursor.execute(
                        """
                        INSERT INTO queue_outbox (
                            outbox_id,
                            run_id,
                            file_id,
                            queue_name,
                            payload,
                            status,
                            idempotency_key,
                            pubsub_attributes
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (idempotency_key)
                        DO UPDATE SET
                            queue_name = EXCLUDED.queue_name,
                            payload = EXCLUDED.payload,
                            pubsub_attributes = EXCLUDED.pubsub_attributes,
                            status = CASE
                                WHEN queue_outbox.status = 'published'
                                THEN queue_outbox.status
                                ELSE 'pending'
                            END,
                            updated_at = now()
                        RETURNING outbox_id, queue_name, payload, status
                        """,
                        (
                            outbox_id,
                            run_id,
                            file_id,
                            queue_name,
                            Json(dict(payload)),
                            PENDING_STATUS,
                            idempotency_key,
                            Json(dict(attributes)),
                        ),
                    )
                else:
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
                        ON CONFLICT (outbox_id)
                        DO UPDATE SET
                            queue_name = EXCLUDED.queue_name,
                            payload = EXCLUDED.payload,
                            status = CASE
                                WHEN queue_outbox.status = 'published'
                                THEN queue_outbox.status
                                ELSE 'pending'
                            END,
                            updated_at = now()
                        RETURNING outbox_id, queue_name, payload, status
                        """,
                        (
                            outbox_id,
                            run_id,
                            file_id,
                            queue_name,
                            Json(dict(payload)),
                            PENDING_STATUS,
                        ),
                    )
                row = cursor.fetchone()
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

        return PendingOutboxRecord(
            outbox_id=str(row[0]),
            queue_name=str(row[1]),
            payload=dict(row[2]),
            status=str(row[3]),
        )

    def mark_published(
        self,
        outbox_id: str,
        *,
        pubsub_message_id: str | None = None,
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        from psycopg.types.json import Json

        with self._conn.cursor() as cursor:
            if self._has_cloud_columns:
                cursor.execute(
                    """
                    UPDATE queue_outbox
                    SET
                        status = %s,
                        pubsub_message_id = %s,
                        pubsub_attributes = %s,
                        published_at = now(),
                        updated_at = now(),
                        last_error = NULL
                    WHERE outbox_id = %s
                    """,
                    (
                        PUBLISHED_STATUS,
                        pubsub_message_id,
                        Json(dict(attributes or {})),
                        outbox_id,
                    ),
                )
            else:
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
                    (PUBLISHED_STATUS, outbox_id),
                )
        self._conn.commit()

    def record_error(self, outbox_id: str, error: str) -> None:
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

    def _load_queue_outbox_columns(self) -> set[str]:
        with self._conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'queue_outbox'
                """
            )
            return {str(row[0]) for row in cursor.fetchall()}

    @property
    def _has_cloud_columns(self) -> bool:
        return {"idempotency_key", "pubsub_message_id", "pubsub_attributes"}.issubset(
            self._columns
        )
