from __future__ import annotations

from dataclasses import replace
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.models import SOURCE_DRIVE, StoredFile  # noqa: E402
from materialization.models import (  # noqa: E402
    DEFAULT_SCRATCH_DIR,
    LEASE_ACTIVE_STATUS,
    LEASE_DEFERRED_STATUS,
    LEASE_FAILED_STATUS,
    LEASE_RELEASED_STATUS,
    BudgetSnapshot,
    MaterializationConfig,
    MaterializationDeferred,
    MaterializationLease,
    PermanentMaterializationError,
    decide_materialization_budget,
)
from materialization.service import FileMaterializer  # noqa: E402


class FakeDriveClient:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.downloads = []
        self.exports = []

    def download_binary(self, file_id: str, output_path: Path, progress_callback) -> None:
        self.downloads.append(file_id)
        self._write(output_path, progress_callback)

    def export_file(
        self,
        file_id: str,
        export_mime_type: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        self.exports.append((file_id, export_mime_type))
        self._write(output_path, progress_callback)

    def _write(self, output_path: Path, progress_callback) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as handle:
            for offset in range(0, len(self.payload), 64):
                handle.write(self.payload[offset : offset + 64])
                handle.flush()
                progress_callback(output_path.stat().st_size)


class FakeLeaseRepository:
    def __init__(self, active_small: int = 0, active_total: int = 0) -> None:
        self.leases: dict[str, MaterializationLease] = {}
        self.active_small = active_small
        self.active_total = active_total
        self.failed: list[tuple[str, str]] = []
        self.expired_paths: list[str] = []

    def expire_materialization_leases(self) -> list[str]:
        paths = list(self.expired_paths)
        self.expired_paths.clear()
        return paths

    def acquire_materialization_lease(
        self,
        stored_file: StoredFile,
        config: MaterializationConfig,
    ) -> MaterializationLease:
        for lease in self.leases.values():
            if lease.file_id == stored_file.file_id and lease.status == LEASE_ACTIVE_STATUS:
                return lease
        decision = decide_materialization_budget(
            BudgetSnapshot(self.active_small, self.active_total),
            stored_file.size_bytes,
            config.small_limit_bytes,
            config.global_limit_bytes,
        )
        lease_id = f"lease-{len(self.leases) + 1}"
        if not decision.allowed:
            self.leases[lease_id] = MaterializationLease(
                lease_id=lease_id,
                file_id=stored_file.file_id,
                run_id=stored_file.run_id,
                source_uri=stored_file.source_uri,
                local_path=None,
                expected_bytes=stored_file.size_bytes,
                actual_bytes=0,
                is_oversize=decision.is_oversize,
                status=LEASE_DEFERRED_STATUS,
            )
            raise MaterializationDeferred(decision.reason or "budget_unavailable")
        lease = MaterializationLease(
            lease_id=lease_id,
            file_id=stored_file.file_id,
            run_id=stored_file.run_id,
            source_uri=stored_file.source_uri,
            local_path=None,
            expected_bytes=stored_file.size_bytes,
            actual_bytes=0,
            is_oversize=decision.is_oversize,
            status=LEASE_ACTIVE_STATUS,
        )
        self.leases[lease_id] = lease
        return lease

    def update_materialization_progress(
        self,
        lease_id: str,
        actual_bytes: int,
        is_oversize: bool,
        config: MaterializationConfig,
    ) -> None:
        if self.active_total + actual_bytes > config.global_limit_bytes:
            self.leases[lease_id] = replace(
                self.leases[lease_id],
                status=LEASE_DEFERRED_STATUS,
                actual_bytes=actual_bytes,
                is_oversize=is_oversize,
            )
            raise MaterializationDeferred("global_budget_unavailable")
        self.leases[lease_id] = replace(
            self.leases[lease_id],
            actual_bytes=actual_bytes,
            is_oversize=is_oversize,
        )

    def activate_materialization_lease(
        self,
        lease_id: str,
        local_path: str,
        actual_bytes: int,
        is_oversize: bool,
    ) -> MaterializationLease:
        lease = replace(
            self.leases[lease_id],
            local_path=local_path,
            actual_bytes=actual_bytes,
            is_oversize=is_oversize,
            status=LEASE_ACTIVE_STATUS,
        )
        self.leases[lease_id] = lease
        return lease

    def fail_materialization_lease(self, lease_id: str, error: str) -> None:
        self.failed.append((lease_id, error))
        self.leases[lease_id] = replace(
            self.leases[lease_id],
            status=LEASE_FAILED_STATUS,
        )

    def release_materialization_lease(self, file_id: str) -> list[str]:
        paths = []
        for lease_id, lease in list(self.leases.items()):
            if lease.file_id == file_id and lease.status == LEASE_ACTIVE_STATUS:
                if lease.local_path:
                    paths.append(lease.local_path)
                self.leases[lease_id] = replace(lease, status=LEASE_RELEASED_STATUS)
        return paths


def drive_file(
    *,
    file_id: str = "file-1",
    mime_type: str = "application/pdf",
    file_name: str = "sample.pdf",
    size_bytes: int | None = 10,
) -> StoredFile:
    return StoredFile(
        file_id=file_id,
        run_id="run-1",
        source_type=SOURCE_DRIVE,
        source_uri=f"drive://file/{file_id}",
        external_id=file_id,
        file_name=file_name,
        relative_path=file_name,
        extension=Path(file_name).suffix,
        mime_type=mime_type,
        size_bytes=size_bytes,
        checksum_sha256=None,
        content_hash=None,
        etag="etag-1",
    )


def config(tmp_path: Path, **overrides: object) -> MaterializationConfig:
    values = {
        "scratch_dir": tmp_path,
        "small_limit_bytes": 100,
        "global_limit_bytes": 500,
        "lease_ttl_seconds": 60,
        "requeue_delay_seconds": 0,
        "worker_id": "test-worker",
    }
    values.update(overrides)
    return MaterializationConfig(**values)


def test_budget_allows_oversize_without_consuming_small_limit():
    decision = decide_materialization_budget(
        BudgetSnapshot(active_small_bytes=90, active_total_bytes=90),
        expected_bytes=200,
        small_limit_bytes=100,
        global_limit_bytes=500,
    )

    assert decision.allowed is True
    assert decision.is_oversize is True


def test_materialization_config_default_and_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("TEXT_MATERIALIZE_SCRATCH_DIR", raising=False)

    assert MaterializationConfig.from_env().scratch_dir == DEFAULT_SCRATCH_DIR

    monkeypatch.setenv("TEXT_MATERIALIZE_SCRATCH_DIR", str(tmp_path))

    assert MaterializationConfig.from_env().scratch_dir == tmp_path


def test_budget_rejects_small_when_small_limit_would_be_exceeded():
    decision = decide_materialization_budget(
        BudgetSnapshot(active_small_bytes=90, active_total_bytes=90),
        expected_bytes=20,
        small_limit_bytes=100,
        global_limit_bytes=500,
    )

    assert decision.allowed is False
    assert decision.reason == "small_budget_unavailable"


def test_budget_rejects_when_global_limit_would_be_exceeded():
    decision = decide_materialization_budget(
        BudgetSnapshot(active_small_bytes=0, active_total_bytes=400),
        expected_bytes=200,
        small_limit_bytes=100,
        global_limit_bytes=500,
    )

    assert decision.allowed is False
    assert decision.reason == "global_budget_unavailable"


def test_drive_binary_materialization_writes_and_releases_temp_file(tmp_path: Path):
    repository = FakeLeaseRepository()
    materializer = FileMaterializer(
        repository,
        config(tmp_path),
        drive_client=FakeDriveClient(b"PDF bytes"),
        sleep_fn=lambda seconds: None,
    )

    materialized = materializer.materialize(drive_file(size_bytes=9))

    assert materialized.stored_file.source_uri == "drive://file/file-1"
    assert materialized.stored_file.original_path == "drive://file/file-1"
    local_path = Path(str(materialized.stored_file.materialized_path))
    assert local_path.read_bytes() == b"PDF bytes"

    materializer.release_if_final(
        materialized.stored_file,
        "text_extraction_completed",
    )

    assert not local_path.exists()


def test_google_native_doc_is_exported_as_txt(tmp_path: Path):
    drive = FakeDriveClient(b"texto exportado")
    materializer = FileMaterializer(
        FakeLeaseRepository(),
        config(tmp_path),
        drive_client=drive,
        sleep_fn=lambda seconds: None,
    )
    stored_file = drive_file(
        mime_type="application/vnd.google-apps.document",
        file_name="Doc",
        size_bytes=None,
    )

    materialized = materializer.materialize(stored_file)

    assert drive.exports == [("file-1", "text/plain")]
    assert Path(str(materialized.stored_file.materialized_path)).suffix == ".txt"
    assert Path(str(materialized.stored_file.materialized_path)).read_text() == (
        "texto exportado"
    )


def test_unknown_size_becomes_oversize_during_stream(tmp_path: Path):
    repository = FakeLeaseRepository()
    materializer = FileMaterializer(
        repository,
        config(tmp_path),
        drive_client=FakeDriveClient(b"x" * 150),
        sleep_fn=lambda seconds: None,
    )

    materialized = materializer.materialize(drive_file(size_bytes=None))

    lease = repository.leases[str(materialized.lease.lease_id)]
    assert lease.actual_bytes == 150
    assert lease.is_oversize is True


def test_streaming_aborts_and_deletes_partial_when_global_limit_is_exceeded(
    tmp_path: Path,
):
    repository = FakeLeaseRepository()
    materializer = FileMaterializer(
        repository,
        config(tmp_path, global_limit_bytes=100),
        drive_client=FakeDriveClient(b"x" * 150),
        sleep_fn=lambda seconds: None,
    )

    try:
        materializer.materialize(drive_file(size_bytes=None))
    except MaterializationDeferred as exc:
        assert "global_budget_unavailable" in str(exc)
    else:
        raise AssertionError("expected MaterializationDeferred")

    assert not list(tmp_path.rglob("sample.pdf"))


def test_materialization_rejects_expected_size_above_hard_limit(tmp_path: Path):
    repository = FakeLeaseRepository()
    materializer = FileMaterializer(
        repository,
        config(tmp_path, max_file_bytes=100),
        drive_client=FakeDriveClient(b""),
        sleep_fn=lambda seconds: None,
    )

    try:
        materializer.materialize(drive_file(size_bytes=101))
    except PermanentMaterializationError as exc:
        assert "file_size_exceeds_limit" in str(exc)
        assert "max_file_bytes=100" in str(exc)
    else:
        raise AssertionError("expected PermanentMaterializationError")

    assert repository.leases == {}


def test_streaming_aborts_and_deletes_partial_when_hard_limit_is_exceeded(
    tmp_path: Path,
):
    repository = FakeLeaseRepository()
    materializer = FileMaterializer(
        repository,
        config(tmp_path, max_file_bytes=100, global_limit_bytes=500),
        drive_client=FakeDriveClient(b"x" * 150),
        sleep_fn=lambda seconds: None,
    )

    try:
        materializer.materialize(drive_file(size_bytes=None))
    except PermanentMaterializationError as exc:
        assert "file_size_exceeds_limit" in str(exc)
        assert "max_file_bytes=100" in str(exc)
    else:
        raise AssertionError("expected PermanentMaterializationError")

    assert repository.failed[0][1].startswith("file_size_exceeds_limit")
    assert not list(tmp_path.rglob("sample.pdf"))
