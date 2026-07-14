from pathlib import Path

import pytest

from table_extract.materialization import (
    DriveCredentialsError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveTokenError,
    DriveTransientError,
    FileMaterializer,
    MaterializationConfig,
    MaterializationDeferred,
    MaterializationLease,
    PermanentMaterializationError,
)
from table_extract.messaging import is_non_retryable_exception
from table_extract.runtime import (
    FileScanContext,
    StoredFile,
    process_file_id,
    process_table_payload,
    run_table_listener,
)
from table_extract.runtime.models import GOOGLE_SPREADSHEET_MIME_TYPE


class FakeRepository:
    def __init__(
        self,
        stored_file: StoredFile,
        *,
        lease: MaterializationLease | None = None,
        defer: bool = False,
    ) -> None:
        self.stored_file = stored_file
        self.lease = lease
        self.defer = defer
        self.acquired_count = 0
        self.activated_paths: list[str] = []
        self.failed_leases: list[tuple[str, str]] = []
        self.released_files: list[str] = []

    def get_file(self, file_id: str) -> StoredFile | None:
        if file_id == self.stored_file.file_id:
            return self.stored_file
        return None

    def expire_materialization_leases(self) -> list[str]:
        return []

    def acquire_materialization_lease(
        self,
        stored_file: StoredFile,
        config: MaterializationConfig,
    ) -> MaterializationLease:
        self.acquired_count += 1
        if self.defer:
            raise MaterializationDeferred("small_budget_unavailable")
        if self.lease is None:
            self.lease = MaterializationLease(
                lease_id="lease-001",
                file_id=stored_file.file_id,
                run_id=stored_file.run_id,
                source_uri=stored_file.source_uri,
                local_path=None,
                expected_bytes=stored_file.size_bytes,
                actual_bytes=0,
                is_oversize=False,
                status="active",
            )
        return self.lease

    def update_materialization_progress(
        self,
        lease_id: str,
        actual_bytes: int,
        is_oversize: bool,
        config: MaterializationConfig,
    ) -> None:
        return None

    def activate_materialization_lease(
        self,
        lease_id: str,
        local_path: str,
        actual_bytes: int,
        is_oversize: bool,
    ) -> MaterializationLease:
        self.activated_paths.append(local_path)
        self.lease = MaterializationLease(
            lease_id=lease_id,
            file_id=self.stored_file.file_id,
            run_id=self.stored_file.run_id,
            source_uri=self.stored_file.source_uri,
            local_path=local_path,
            expected_bytes=self.stored_file.size_bytes,
            actual_bytes=actual_bytes,
            is_oversize=is_oversize,
            status="active",
        )
        return self.lease

    def fail_materialization_lease(self, lease_id: str, error: str) -> None:
        self.failed_leases.append((lease_id, error))

    def release_materialization_lease(self, file_id: str) -> list[str]:
        self.released_files.append(file_id)
        if self.lease and self.lease.local_path:
            return [self.lease.local_path]
        return []


class FakeDriveClient:
    def __init__(self, content: bytes = b"id,name\n1,Ana\n") -> None:
        self.content = content
        self.downloaded: list[str] = []
        self.exported: list[str] = []

    def download_binary(self, file_id: str, output_path: Path, progress_callback) -> None:
        self.downloaded.append(file_id)
        output_path.write_bytes(self.content)
        progress_callback(output_path.stat().st_size)

    def export_file(self, file_id: str, export_mime_type: str, output_path: Path, progress_callback) -> None:
        self.exported.append(file_id)
        output_path.write_bytes(self.content)
        progress_callback(output_path.stat().st_size)


class FailingDriveClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.downloaded: list[str] = []
        self.exported: list[str] = []

    def download_binary(self, file_id: str, output_path: Path, progress_callback) -> None:
        self.downloaded.append(file_id)
        output_path.write_bytes(b"partial")
        raise self.exc

    def export_file(self, file_id: str, export_mime_type: str, output_path: Path, progress_callback) -> None:
        self.exported.append(file_id)
        output_path.write_bytes(b"partial")
        raise self.exc


class FakeConsumer:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads
        self.queue_name: str | None = None
        self.max_messages: int | None = None
        self.requeue_messages: bool | None = None

    def consume(self, queue_name, handle_payload, max_messages=None, requeue_messages=False):
        self.queue_name = queue_name
        self.max_messages = max_messages
        self.requeue_messages = requeue_messages
        for payload in self.payloads[: max_messages or None]:
            handle_payload(payload)


def local_stored_file(path: Path) -> StoredFile:
    return StoredFile(
        file_id="file-001",
        run_id="run-001",
        source_type="local",
        source_uri=str(path),
        external_id=None,
        file_name=path.name,
        relative_path=path.name,
        extension=".csv",
        mime_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum_sha256=None,
    )


def drive_stored_file() -> StoredFile:
    return StoredFile(
        file_id="file-001",
        run_id="run-001",
        source_type="drive",
        source_uri="drive://file/drive-file-001",
        external_id="drive-file-001",
        file_name="clientes.csv",
        relative_path="clientes.csv",
        extension=".csv",
        mime_type="text/csv",
        size_bytes=12,
        checksum_sha256=None,
    )


def google_sheet_stored_file() -> StoredFile:
    stored_file = drive_stored_file()
    return StoredFile(
        file_id=stored_file.file_id,
        run_id=stored_file.run_id,
        source_type=stored_file.source_type,
        source_uri=stored_file.source_uri,
        external_id=stored_file.external_id,
        file_name="clientes",
        relative_path="clientes",
        extension="",
        mime_type=GOOGLE_SPREADSHEET_MIME_TYPE,
        size_bytes=stored_file.size_bytes,
        checksum_sha256=stored_file.checksum_sha256,
    )


def table_payload(**overrides):
    payload = {
        "schema_version": "2.0",
        "event_type": "file.routed",
        "run_id": "run-001",
        "file_id": "file-001",
        "routing_decision_id": "route-001",
        "source_type": "local",
        "source_uri": "/tmp/clientes.csv",
        "external_id": None,
        "file_name": "clientes.csv",
        "relative_path": "clientes.csv",
        "extension": ".csv",
        "mime_type": "text/csv",
        "checksum_sha256": None,
        "content_hash": None,
        "etag": None,
        "size_bytes": 12,
        "source_queue_name": "Queue-Archivos",
        "destination_queue_name": "Queue-Tables",
        "route_type": "table",
        "reason": "tabular_extension",
    }
    payload.update(overrides)
    return payload


def materializer_for(repo: FakeRepository, tmp_path: Path, drive_client=None) -> FileMaterializer:
    return FileMaterializer(
        repo,
        MaterializationConfig(
            scratch_dir=tmp_path / "scratch",
            requeue_delay_seconds=0,
            worker_id="test-worker",
        ),
        drive_client=drive_client,
        sleep_fn=lambda seconds: None,
    )


def test_local_file_context_does_not_create_lease(tmp_path: Path) -> None:
    path = tmp_path / "clientes.csv"
    path.write_text("email\na@example.com\n", encoding="utf-8")
    repo = FakeRepository(local_stored_file(path))
    materializer = materializer_for(repo, tmp_path)

    context = process_file_id("file-001", repo, materializer)

    assert context.local_path == str(path)
    assert context.is_temporary is False
    assert context.lease_id is None
    assert repo.acquired_count == 0


def test_remote_file_materializes_with_temporary_lease(tmp_path: Path) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(repo, tmp_path, FakeDriveClient())

    context = process_file_id("file-001", repo, materializer)

    assert context.is_temporary is True
    assert context.lease_id == "lease-001"
    assert Path(context.local_path).is_file()
    assert repo.acquired_count == 1
    assert repo.activated_paths == [context.local_path]

    materializer.release_context(context.file_id)

    assert repo.released_files == ["file-001"]
    assert not Path(context.local_path).exists()


def test_materialization_deferred_is_retryable(tmp_path: Path) -> None:
    repo = FakeRepository(drive_stored_file(), defer=True)
    materializer = materializer_for(repo, tmp_path, FakeDriveClient())

    with pytest.raises(MaterializationDeferred):
        process_file_id("file-001", repo, materializer)

    assert not is_non_retryable_exception(MaterializationDeferred("retry later"))


def test_permanent_materialization_error_is_non_retryable(tmp_path: Path) -> None:
    stored_file = drive_stored_file()
    stored_file = StoredFile(
        file_id=stored_file.file_id,
        run_id=stored_file.run_id,
        source_type="s3",
        source_uri="s3://bucket/clientes.csv",
        external_id=None,
        file_name=stored_file.file_name,
        relative_path=stored_file.relative_path,
        extension=stored_file.extension,
        mime_type=stored_file.mime_type,
        size_bytes=stored_file.size_bytes,
        checksum_sha256=stored_file.checksum_sha256,
    )
    repo = FakeRepository(stored_file)
    materializer = materializer_for(repo, tmp_path)

    with pytest.raises(PermanentMaterializationError) as exc:
        process_file_id("file-001", repo, materializer)

    assert is_non_retryable_exception(exc.value)


def test_missing_drive_credentials_are_non_retryable(tmp_path: Path) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = FileMaterializer(
        repo,
        MaterializationConfig(
            scratch_dir=tmp_path / "scratch",
            google_client_secrets_file=None,
            google_token_file=None,
            requeue_delay_seconds=0,
            worker_id="test-worker",
        ),
        sleep_fn=lambda seconds: None,
    )

    with pytest.raises(DriveCredentialsError) as exc:
        process_file_id("file-001", repo, materializer)

    assert is_non_retryable_exception(exc.value)
    assert repo.failed_leases
    assert "GOOGLE_CLIENT_SECRETS_FILE" in repo.failed_leases[0][1]


def test_drive_permission_error_is_non_retryable_and_deletes_partial(
    tmp_path: Path,
) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(
        repo,
        tmp_path,
        FailingDriveClient(DrivePermissionError("appNotAuthorizedToFile secret-token")),
    )

    with pytest.raises(DrivePermissionError) as exc:
        process_file_id("file-001", repo, materializer)

    assert is_non_retryable_exception(exc.value)
    assert repo.failed_leases
    assert "drive_permission_denied" in repo.failed_leases[0][1]
    assert "secret-token" not in repo.failed_leases[0][1]
    assert not any((tmp_path / "scratch").rglob("*.csv"))


@pytest.mark.parametrize(
    "drive_error",
    [
        DriveTokenError("invalid_grant secret-token"),
        DriveNotFoundError("notFound secret-token"),
    ],
)
def test_drive_token_and_not_found_errors_are_non_retryable(
    tmp_path: Path,
    drive_error: Exception,
) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(
        repo,
        tmp_path,
        FailingDriveClient(drive_error),
    )

    with pytest.raises(PermanentMaterializationError) as exc:
        process_file_id("file-001", repo, materializer)

    assert is_non_retryable_exception(exc.value)
    assert repo.failed_leases
    assert "secret-token" not in repo.failed_leases[0][1]


def test_drive_transient_error_is_retryable_and_deletes_partial(tmp_path: Path) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(
        repo,
        tmp_path,
        FailingDriveClient(DriveTransientError("Google Drive status 500")),
    )

    with pytest.raises(MaterializationDeferred) as exc:
        process_file_id("file-001", repo, materializer)

    assert not is_non_retryable_exception(exc.value)
    assert repo.failed_leases
    assert "drive_transient" in repo.failed_leases[0][1]
    assert not any((tmp_path / "scratch").rglob("*.csv"))


def test_google_sheet_export_uses_same_operational_drive_errors(tmp_path: Path) -> None:
    repo = FakeRepository(google_sheet_stored_file())
    failing_client = FailingDriveClient(DrivePermissionError("not authorized"))
    materializer = materializer_for(repo, tmp_path, failing_client)

    with pytest.raises(DrivePermissionError):
        process_file_id("file-001", repo, materializer)

    assert failing_client.exported == ["drive-file-001"]
    assert repo.failed_leases
    assert "drive_permission_denied" in repo.failed_leases[0][1]


def test_listener_calls_callback_with_file_scan_context(tmp_path: Path) -> None:
    path = tmp_path / "clientes.csv"
    path.write_text("email\na@example.com\n", encoding="utf-8")
    repo = FakeRepository(local_stored_file(path))
    materializer = materializer_for(repo, tmp_path)
    consumer = FakeConsumer([table_payload(source_uri=str(path))])
    contexts: list[FileScanContext] = []

    run_table_listener(
        repository=repo,
        materializer=materializer,
        consumer=consumer,
        max_messages=1,
        requeue_messages=True,
        handle_context=contexts.append,
    )

    assert consumer.queue_name == "Queue-Tables"
    assert consumer.max_messages == 1
    assert consumer.requeue_messages is True
    assert contexts[0].file_id == "file-001"
    assert contexts[0].local_path == str(path)


def test_listener_releases_temporary_context_after_callback(tmp_path: Path) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(repo, tmp_path, FakeDriveClient())
    consumer = FakeConsumer([table_payload(source_type="drive", source_uri="drive://file/drive-file-001")])
    seen_paths: list[str] = []

    run_table_listener(
        repository=repo,
        materializer=materializer,
        consumer=consumer,
        max_messages=1,
        handle_context=lambda context: seen_paths.append(context.local_path),
    )

    assert seen_paths
    assert repo.released_files == ["file-001"]
    assert not Path(seen_paths[0]).exists()


def test_listener_default_callback_profiles_and_releases_temporary_context(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = FakeRepository(drive_stored_file())
    materializer = materializer_for(repo, tmp_path, FakeDriveClient())
    consumer = FakeConsumer(
        [table_payload(source_type="drive", source_uri="drive://file/drive-file-001")]
    )

    run_table_listener(
        repository=repo,
        materializer=materializer,
        consumer=consumer,
        max_messages=1,
    )

    output = capsys.readouterr().out
    assert "profiled_file_scan_context" in output
    assert "source_type=csv" in output
    assert "tables=1" in output
    assert repo.released_files == ["file-001"]


def test_process_table_payload_rejects_invalid_payload_without_context(tmp_path: Path) -> None:
    path = tmp_path / "clientes.csv"
    path.write_text("email\na@example.com\n", encoding="utf-8")
    repo = FakeRepository(local_stored_file(path))
    materializer = materializer_for(repo, tmp_path)

    with pytest.raises(ValueError, match="Unsupported route_type"):
        process_table_payload(
            table_payload(route_type="pdf"),
            repository=repo,
            materializer=materializer,
        )
