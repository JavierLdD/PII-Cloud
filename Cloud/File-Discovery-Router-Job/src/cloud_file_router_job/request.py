from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json


REQUEST_ENV_NAME = "DISCOVERY_ROUTER_REQUEST_JSON"
SUPPORTED_SOURCE_TYPES = frozenset({"drive"})


class DiscoveryRouterRequestError(ValueError):
    """Raised when the Cloud File Discovery + Router request is invalid."""


@dataclass(frozen=True)
class DiscoveryRouterRequest:
    user_id: str
    run_id: str
    drive_folder_id: str
    source_type: str = "drive"
    source_name: str | None = None
    force_enqueue: bool = False
    dry_run: bool = False
    max_files: int | None = None

    @property
    def source_scope_key(self) -> str:
        """Stable, server-derived identity for this user's logical source."""
        return self.drive_folder_id

    def __post_init__(self) -> None:
        user_id = _normalize_text(self.user_id)
        if not user_id:
            raise DiscoveryRouterRequestError("user_id is required")
        object.__setattr__(self, "user_id", user_id)

        run_id = _normalize_text(self.run_id)
        if not run_id:
            raise DiscoveryRouterRequestError("run_id is required")
        object.__setattr__(self, "run_id", run_id)

        source_type = (_normalize_text(self.source_type) or "drive").casefold()
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise DiscoveryRouterRequestError(
                f"Unsupported source_type for this job version: {source_type}"
            )
        object.__setattr__(self, "source_type", source_type)

        drive_folder_id = _normalize_text(self.drive_folder_id)
        if not drive_folder_id:
            raise DiscoveryRouterRequestError(
                "drive_folder_id is required for source_type=drive"
            )
        object.__setattr__(self, "drive_folder_id", drive_folder_id)
        object.__setattr__(self, "source_name", _normalize_text(self.source_name))

        if self.max_files is not None:
            max_files = int(self.max_files)
            if max_files <= 0:
                raise DiscoveryRouterRequestError("max_files must be greater than zero")
            object.__setattr__(self, "max_files", max_files)

    @classmethod
    def from_json(cls, raw_json: str) -> "DiscoveryRouterRequest":
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise DiscoveryRouterRequestError(
                f"Invalid {REQUEST_ENV_NAME}: {exc.msg}"
            ) from exc
        if not isinstance(payload, dict):
            raise DiscoveryRouterRequestError(f"{REQUEST_ENV_NAME} must be a JSON object")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> "DiscoveryRouterRequest":
        return cls(
            user_id=_text_value(payload.get("user_id")) or "",
            run_id=_text_value(payload.get("run_id")) or "",
            source_type=_text_value(payload.get("source_type")) or "drive",
            drive_folder_id=_text_value(payload.get("drive_folder_id")) or "",
            source_name=_text_value(payload.get("source_name")),
            force_enqueue=_bool_value(payload.get("force_enqueue"), default=False),
            dry_run=_bool_value(payload.get("dry_run"), default=False),
            max_files=_int_value(payload.get("max_files")),
        )


def load_request_from_env(env: Mapping[str, str]) -> DiscoveryRouterRequest:
    raw_json = env.get(REQUEST_ENV_NAME)
    if raw_json and raw_json.strip():
        return DiscoveryRouterRequest.from_json(raw_json)

    return DiscoveryRouterRequest.from_mapping(
        {
            "user_id": env.get("USER_ID"),
            "run_id": env.get("RUN_ID"),
            "source_type": env.get("SOURCE_TYPE") or "drive",
            "drive_folder_id": env.get("DRIVE_FOLDER_ID"),
            "source_name": env.get("SOURCE_NAME"),
            "force_enqueue": env.get("FORCE_ENQUEUE"),
            "dry_run": env.get("DRY_RUN"),
            "max_files": env.get("MAX_FILES"),
        }
    )


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    raise DiscoveryRouterRequestError(f"Invalid boolean value: {value!r}")


def _int_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DiscoveryRouterRequestError(f"Invalid integer value: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise DiscoveryRouterRequestError(
                f"Invalid integer value: {value!r}"
            ) from exc
    raise DiscoveryRouterRequestError(f"Invalid integer value: {value!r}")


def _text_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _normalize_text(value)
    return _normalize_text(str(value))


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
