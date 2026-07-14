from __future__ import annotations

from pathlib import Path
import re
import time

from common.models import SOURCE_DRIVE, StoredFile
from materialization.drive import GoogleDriveContentClient
from materialization.models import (
    DriveContentClient,
    MaterializationConfig,
    MaterializationDeferred,
    MaterializationLease,
    MaterializationRepository,
    MaterializedFile,
    PermanentMaterializationError,
)


GOOGLE_NATIVE_EXPORT_MIME_TYPE = "text/plain"


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
        if (
            stored_file.size_bytes is not None
            and stored_file.size_bytes > self._config.max_file_bytes
        ):
            raise PermanentMaterializationError(
                "file_size_exceeds_limit "
                f"size_bytes={stored_file.size_bytes} "
                f"max_file_bytes={self._config.max_file_bytes}"
            )

        try:
            lease = self._repository.acquire_materialization_lease(
                stored_file,
                self._config,
            )
        except MaterializationDeferred as exc:
            print(
                "materialization_deferred "
                f"file_id={stored_file.file_id} "
                f"reason={exc}"
            )
            self._sleep(self._config.requeue_delay_seconds)
            raise

        if lease.has_local_file:
            print(
                "materialization_reused "
                f"file_id={stored_file.file_id} "
                f"bytes={lease.actual_bytes} "
                f"oversize={lease.is_oversize} "
                f"path={lease.local_path}"
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

    def release_if_final(self, stored_file: StoredFile, status: str | None) -> None:
        if status not in {"text_extraction_completed", "text_extraction_failed"}:
            return
        released_paths = self._repository.release_materialization_lease(
            stored_file.file_id
        )
        for raw_path in released_paths:
            if not self._delete_materialized_path(raw_path, stored_file.file_id):
                continue
            print(
                "materialization_released "
                f"file_id={stored_file.file_id} "
                f"path={raw_path}"
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
            print(
                "WARN materialization_cleanup_failed "
                f"file_id={file_id} "
                f"path={path} "
                f"error={str(exc).replace(chr(10), ' ')}"
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
            if stored_file.is_google_native:
                self._get_drive_client().export_file(
                    _drive_file_id(stored_file),
                    GOOGLE_NATIVE_EXPORT_MIME_TYPE,
                    output_path,
                    self._progress_callback(lease),
                )
            else:
                self._get_drive_client().download_binary(
                    _drive_file_id(stored_file),
                    output_path,
                    self._progress_callback(lease),
                )
        except MaterializationDeferred:
            _delete_partial(output_path)
            raise
        except PermanentMaterializationError as exc:
            _delete_partial(output_path)
            self._repository.fail_materialization_lease(lease.lease_id, str(exc))
            raise
        except Exception as exc:
            _delete_partial(output_path)
            self._repository.fail_materialization_lease(lease.lease_id, str(exc))
            raise PermanentMaterializationError(str(exc)) from exc

        actual_bytes = output_path.stat().st_size
        if actual_bytes > self._config.max_file_bytes:
            error = (
                "file_size_exceeds_limit "
                f"size_bytes={actual_bytes} "
                f"max_file_bytes={self._config.max_file_bytes}"
            )
            _delete_partial(output_path)
            self._repository.fail_materialization_lease(lease.lease_id, error)
            raise PermanentMaterializationError(error)
        is_oversize = actual_bytes > self._config.small_limit_bytes
        lease = self._repository.activate_materialization_lease(
            lease.lease_id,
            str(output_path),
            actual_bytes,
            is_oversize,
        )
        print(
            "materialized "
            f"file_id={stored_file.file_id} "
            f"bytes={actual_bytes} "
            f"oversize={is_oversize} "
            f"path={output_path}"
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
                if actual_bytes > self._config.max_file_bytes:
                    raise PermanentMaterializationError(
                        "file_size_exceeds_limit "
                        f"size_bytes={actual_bytes} "
                        f"max_file_bytes={self._config.max_file_bytes}"
                    )
            except MaterializationDeferred as exc:
                print(
                    "materialization_deferred "
                    f"file_id={lease.file_id} "
                    f"reason={exc}"
                )
                self._sleep(self._config.requeue_delay_seconds)
                raise

        return callback

    def _get_drive_client(self) -> DriveContentClient:
        if self._drive_client is not None:
            return self._drive_client
        has_client_secret = bool(self._config.google_client_secrets_file)
        has_token = bool(self._config.google_token_file)
        if has_client_secret != has_token:
            raise PermanentMaterializationError(
                "Set both GOOGLE_CLIENT_SECRETS_FILE and GOOGLE_TOKEN_FILE for "
                "OAuth Drive materialization, or set neither to use ADC."
            )
        if has_client_secret and has_token:
            self._drive_client = GoogleDriveContentClient.from_oauth(
                self._config.google_client_secrets_file,
                self._config.google_token_file,
            )
        else:
            self._drive_client = GoogleDriveContentClient.from_adc()
        return self._drive_client

    def _output_path(self, stored_file: StoredFile) -> Path:
        suffix = ".txt" if stored_file.is_google_native else stored_file.extension
        if not suffix:
            suffix = Path(stored_file.file_name).suffix
        if not suffix:
            suffix = ".bin"
        name = _safe_name(Path(stored_file.file_name).stem or stored_file.file_id)
        return self._config.scratch_dir / stored_file.file_id / f"{name}{suffix}"


def build_file_materializer(repository: MaterializationRepository) -> FileMaterializer:
    return FileMaterializer(repository, MaterializationConfig.from_env())


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
