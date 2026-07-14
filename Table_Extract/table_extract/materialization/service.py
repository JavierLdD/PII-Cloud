from __future__ import annotations

from pathlib import Path
import re
import time

from table_extract.materialization.drive import GoogleDriveContentClient
from table_extract.materialization.models import (
    DriveCredentialsError,
    DriveContentClient,
    MaterializationConfig,
    MaterializationDeferred,
    MaterializationLease,
    MaterializationRepository,
    MaterializedFile,
    PermanentMaterializationError,
)
from table_extract.operational import (
    OperationalErrorInfo,
    classify_operational_exception,
    emit_operational_log,
)
from table_extract.runtime.models import SOURCE_DRIVE, StoredFile


GOOGLE_SHEETS_EXPORT_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class FileMaterializer:
    def __init__(
        self,
        repository: MaterializationRepository,
        config: MaterializationConfig | None = None,
        drive_client: DriveContentClient | None = None,
        sleep_fn=time.sleep,
    ) -> None:
        self._repository = repository
        self._config = config or MaterializationConfig.from_env()
        self._drive_client = drive_client
        self._sleep = sleep_fn

    def materialize(self, stored_file: StoredFile) -> MaterializedFile:
        self._cleanup_paths(self._repository.expire_materialization_leases())
        if stored_file.is_materialized_local:
            return MaterializedFile(stored_file=stored_file)
        if stored_file.source_type != SOURCE_DRIVE:
            raise PermanentMaterializationError(
                f"Unsupported remote source_type: {stored_file.source_type}"
            )

        try:
            lease = self._repository.acquire_materialization_lease(
                stored_file,
                self._config,
            )
        except MaterializationDeferred as exc:
            _emit_materialization_event(
                "table_materialization_deferred",
                exc,
                file_id=stored_file.file_id,
            )
            self._sleep(self._config.requeue_delay_seconds)
            raise

        if lease.has_local_file:
            _emit_materialization_event(
                "table_materialization_reused",
                OperationalErrorInfo(
                    component="materialization",
                    category="materialization_reused",
                    retryable=False,
                    message="Reused existing table materialization.",
                ),
                file_id=stored_file.file_id,
                bytes=lease.actual_bytes,
                oversize=lease.is_oversize,
                path=lease.local_path,
            )
            return MaterializedFile(
                stored_file=stored_file.with_materialized_path(str(lease.local_path)),
                lease=lease,
            )

        if lease.local_path and not lease.has_local_file:
            self._repository.fail_materialization_lease(
                lease.lease_id,
                "materialized_path_missing",
            )
            return self.materialize(stored_file)

        return self._download_drive_file(stored_file, lease)

    def release_context(self, file_id: str) -> None:
        released_paths = self._repository.release_materialization_lease(file_id)
        for raw_path in released_paths:
            if self._delete_materialized_path(raw_path, file_id):
                _emit_materialization_event(
                    "table_materialization_released",
                    OperationalErrorInfo(
                        component="materialization",
                        category="materialization_released",
                        retryable=False,
                        message="Released table materialization.",
                    ),
                    file_id=file_id,
                    path=raw_path,
                )

    def _cleanup_paths(self, raw_paths: list[str]) -> None:
        for raw_path in raw_paths:
            self._delete_materialized_path(raw_path, file_id="expired")

    def _delete_materialized_path(self, raw_path: str, file_id: str) -> bool:
        path = Path(raw_path)
        try:
            if path.exists():
                path.unlink()
                _remove_empty_parents(path.parent, self._config.scratch_dir)
        except OSError as exc:
            _emit_materialization_event(
                "table_materialization_cleanup_failed",
                exc,
                file_id=file_id,
                path=str(path),
            )
            return False
        return True

    def _download_drive_file(
        self,
        stored_file: StoredFile,
        lease: MaterializationLease,
    ) -> MaterializedFile:
        output_path = self._output_path(stored_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if stored_file.is_google_native_spreadsheet:
                self._get_drive_client().export_file(
                    _drive_file_id(stored_file),
                    GOOGLE_SHEETS_EXPORT_MIME_TYPE,
                    output_path,
                    self._progress_callback(lease),
                )
            else:
                self._get_drive_client().download_binary(
                    _drive_file_id(stored_file),
                    output_path,
                    self._progress_callback(lease),
                )
        except MaterializationDeferred as exc:
            _delete_partial(output_path)
            self._repository.fail_materialization_lease(
                lease.lease_id,
                _lease_error_message(exc),
            )
            _emit_materialization_event(
                "table_materialization_failed",
                exc,
                file_id=stored_file.file_id,
                lease_id=lease.lease_id,
            )
            raise
        except PermanentMaterializationError as exc:
            _delete_partial(output_path)
            self._repository.fail_materialization_lease(
                lease.lease_id,
                _lease_error_message(exc),
            )
            _emit_materialization_event(
                "table_materialization_failed",
                exc,
                file_id=stored_file.file_id,
                lease_id=lease.lease_id,
            )
            raise
        except Exception as exc:
            _delete_partial(output_path)
            wrapped = PermanentMaterializationError(
                "Table file materialization failed.",
                category="materialization_unexpected",
                safe_context={
                    "file_id": stored_file.file_id,
                    "source_type": stored_file.source_type,
                },
            )
            self._repository.fail_materialization_lease(
                lease.lease_id,
                _lease_error_message(wrapped),
            )
            _emit_materialization_event(
                "table_materialization_failed",
                wrapped,
                file_id=stored_file.file_id,
                lease_id=lease.lease_id,
            )
            raise wrapped from exc

        actual_bytes = output_path.stat().st_size
        is_oversize = actual_bytes > self._config.small_limit_bytes
        lease = self._repository.activate_materialization_lease(
            lease.lease_id,
            str(output_path),
            actual_bytes,
            is_oversize,
        )
        _emit_materialization_event(
            "table_materialized",
            OperationalErrorInfo(
                component="materialization",
                category="materialized",
                retryable=False,
                message="Materialized table file.",
            ),
            file_id=stored_file.file_id,
            bytes=actual_bytes,
            oversize=is_oversize,
            path=str(output_path),
        )
        return MaterializedFile(
            stored_file=stored_file.with_materialized_path(str(output_path)),
            lease=lease,
        )

    def _progress_callback(self, lease: MaterializationLease):
        def callback(actual_bytes: int) -> None:
            is_oversize = actual_bytes > self._config.small_limit_bytes
            try:
                self._repository.update_materialization_progress(
                    lease.lease_id,
                    actual_bytes,
                    is_oversize,
                    self._config,
                )
            except MaterializationDeferred as exc:
                _emit_materialization_event(
                    "table_materialization_deferred",
                    exc,
                    file_id=lease.file_id,
                    lease_id=lease.lease_id,
                )
                self._sleep(self._config.requeue_delay_seconds)
                raise

        return callback

    def _get_drive_client(self) -> DriveContentClient:
        if self._drive_client is not None:
            return self._drive_client
        if not self._config.google_client_secrets_file:
            raise DriveCredentialsError(
                "Missing GOOGLE_CLIENT_SECRETS_FILE for Drive materialization."
            )
        if not self._config.google_token_file:
            raise DriveCredentialsError(
                "Missing GOOGLE_TOKEN_FILE for Drive materialization."
            )
        self._drive_client = GoogleDriveContentClient.from_oauth(
            self._config.google_client_secrets_file,
            self._config.google_token_file,
        )
        return self._drive_client

    def _output_path(self, stored_file: StoredFile) -> Path:
        suffix = ".xlsx" if stored_file.is_google_native_spreadsheet else stored_file.extension
        if not suffix:
            suffix = Path(stored_file.file_name).suffix
        if not suffix:
            suffix = ".bin"
        name = _safe_name(Path(stored_file.file_name).stem or stored_file.file_id)
        return self._config.scratch_dir / stored_file.file_id / f"{name}{suffix}"


def _drive_file_id(stored_file: StoredFile) -> str:
    if stored_file.external_id:
        return stored_file.external_id
    prefix = "drive://file/"
    if stored_file.source_uri.startswith(prefix):
        return stored_file.source_uri[len(prefix) :]
    raise PermanentMaterializationError(
        f"Cannot determine Drive file id from source_uri={stored_file.source_uri}"
    )


def _safe_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return sanitized or "file"


def _delete_partial(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop = stop_at.resolve()
    current = path
    while current.exists() and current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _lease_error_message(exc: Exception) -> str:
    info = classify_operational_exception(exc, default_component="materialization")
    return f"{info.category}: {info.message}"[:2000]


def _emit_materialization_event(
    event: str,
    info: OperationalErrorInfo | Exception,
    **context,
) -> None:
    emit_operational_log(
        event,
        info,
        safe_context=context,
    )
