from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import socket
from typing import Protocol

from common.models import StoredFile


DEFAULT_SCRATCH_DIR = Path("/tmp/pii-text-materialization")
DEFAULT_SMALL_LIMIT_BYTES = 100 * 1024 * 1024
DEFAULT_GLOBAL_LIMIT_BYTES = 500 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_LEASE_TTL_SECONDS = 2 * 60 * 60
DEFAULT_REQUEUE_DELAY_SECONDS = 5

LEASE_ACTIVE_STATUS = "active"
LEASE_DEFERRED_STATUS = "deferred"
LEASE_EXPIRED_STATUS = "expired"
LEASE_FAILED_STATUS = "failed"
LEASE_RELEASED_STATUS = "released"


class MaterializationDeferred(RuntimeError):
    """Raised when a remote file should be retried later due to budget pressure."""


class PermanentMaterializationError(RuntimeError):
    """Raised when a remote file cannot be materialized without changing inputs."""


@dataclass(frozen=True)
class MaterializationConfig:
    scratch_dir: Path = DEFAULT_SCRATCH_DIR
    small_limit_bytes: int = DEFAULT_SMALL_LIMIT_BYTES
    global_limit_bytes: int = DEFAULT_GLOBAL_LIMIT_BYTES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    requeue_delay_seconds: int = DEFAULT_REQUEUE_DELAY_SECONDS
    worker_id: str = ""
    google_client_secrets_file: str | None = None
    google_token_file: str | None = None

    @classmethod
    def from_env(cls) -> "MaterializationConfig":
        return cls(
            scratch_dir=Path(
                os.environ.get(
                    "TEXT_MATERIALIZE_SCRATCH_DIR",
                    str(DEFAULT_SCRATCH_DIR),
                )
            ).expanduser(),
            small_limit_bytes=_int_env(
                "TEXT_MATERIALIZE_SMALL_LIMIT_BYTES",
                DEFAULT_SMALL_LIMIT_BYTES,
            ),
            global_limit_bytes=_int_env(
                "TEXT_MATERIALIZE_GLOBAL_LIMIT_BYTES",
                DEFAULT_GLOBAL_LIMIT_BYTES,
            ),
            max_file_bytes=_int_env(
                "TEXT_EXTRACT_MAX_FILE_BYTES",
                DEFAULT_MAX_FILE_BYTES,
            ),
            lease_ttl_seconds=_int_env(
                "TEXT_MATERIALIZE_LEASE_TTL_SECONDS",
                DEFAULT_LEASE_TTL_SECONDS,
            ),
            requeue_delay_seconds=_int_env(
                "TEXT_MATERIALIZE_REQUEUE_DELAY_SECONDS",
                DEFAULT_REQUEUE_DELAY_SECONDS,
            ),
            worker_id=os.environ.get("TEXT_MATERIALIZE_WORKER_ID", _default_worker_id()),
            google_client_secrets_file=os.environ.get("GOOGLE_CLIENT_SECRETS_FILE"),
            google_token_file=os.environ.get("GOOGLE_TOKEN_FILE"),
        )


@dataclass(frozen=True)
class MaterializationLease:
    lease_id: str
    file_id: str
    run_id: str
    source_uri: str
    local_path: str | None
    expected_bytes: int | None
    actual_bytes: int
    is_oversize: bool
    status: str

    @property
    def has_local_file(self) -> bool:
        return bool(self.local_path and Path(self.local_path).is_file())


@dataclass(frozen=True)
class MaterializedFile:
    stored_file: StoredFile
    lease: MaterializationLease | None = None

    @property
    def is_temporary(self) -> bool:
        return self.lease is not None


@dataclass(frozen=True)
class BudgetSnapshot:
    active_small_bytes: int
    active_total_bytes: int


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    is_oversize: bool
    reason: str | None = None


class MaterializationRepository(Protocol):
    def expire_materialization_leases(self) -> list[str]:
        ...

    def acquire_materialization_lease(
        self,
        stored_file: StoredFile,
        config: MaterializationConfig,
    ) -> MaterializationLease:
        ...

    def update_materialization_progress(
        self,
        lease_id: str,
        actual_bytes: int,
        is_oversize: bool,
        config: MaterializationConfig,
    ) -> None:
        ...

    def activate_materialization_lease(
        self,
        lease_id: str,
        local_path: str,
        actual_bytes: int,
        is_oversize: bool,
    ) -> MaterializationLease:
        ...

    def fail_materialization_lease(self, lease_id: str, error: str) -> None:
        ...

    def release_materialization_lease(self, file_id: str) -> list[str]:
        ...


class DriveContentClient(Protocol):
    def download_binary(
        self,
        file_id: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        ...

    def export_file(
        self,
        file_id: str,
        export_mime_type: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        ...


def decide_materialization_budget(
    snapshot: BudgetSnapshot,
    expected_bytes: int | None,
    small_limit_bytes: int,
    global_limit_bytes: int,
) -> BudgetDecision:
    projected_bytes = expected_bytes if expected_bytes is not None else 0
    is_oversize = bool(
        expected_bytes is not None and expected_bytes > small_limit_bytes
    )

    if projected_bytes > global_limit_bytes:
        return BudgetDecision(
            allowed=False,
            is_oversize=is_oversize,
            reason="global_limit_exceeded_by_file",
        )
    if snapshot.active_total_bytes + projected_bytes > global_limit_bytes:
        return BudgetDecision(
            allowed=False,
            is_oversize=is_oversize,
            reason="global_budget_unavailable",
        )
    if not is_oversize and expected_bytes is not None:
        if snapshot.active_small_bytes + projected_bytes > small_limit_bytes:
            return BudgetDecision(
                allowed=False,
                is_oversize=False,
                reason="small_budget_unavailable",
            )
    if expected_bytes is None and snapshot.active_small_bytes >= small_limit_bytes:
        return BudgetDecision(
            allowed=False,
            is_oversize=False,
            reason="small_budget_unavailable",
        )

    return BudgetDecision(allowed=True, is_oversize=is_oversize)


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    return int(value)


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"
