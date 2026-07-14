from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
import uuid


DEFAULT_OUTPUT_LOCAL_PATH = "/tmp/table_extract_discovery.json"
SUPPORTED_SOURCE_TYPES = frozenset({"database"})
SUPPORTED_OUTPUT_URI_SCHEMES = ("gs://",)
SUPPORTED_DATABASE_TYPES = frozenset({"postgresql", "oracle"})
VALID_DEVICES = frozenset({"auto", "cpu", "cuda", "mps"})


class ScanRequestError(ValueError):
    """Raised when a Cloud BBDD scan request is invalid."""


@dataclass(frozen=True)
class ScanRequest:
    scan_id: str
    connection_uri: str = field(repr=False)
    user_id: str = ""
    run_name: str = ""
    database_type: str = ""
    confirm_full_scan: bool = False
    source_id: str | None = None
    source_name: str = "database"
    source_type: str = "database"
    dialect: str | None = None
    include_schemas: tuple[str, ...] = field(default_factory=tuple)
    include_tables: tuple[str, ...] = field(default_factory=tuple)
    exclude_schemas: tuple[str, ...] = field(default_factory=tuple)
    exclude_tables: tuple[str, ...] = field(default_factory=tuple)
    include_views: bool = True
    allow_full_database_scan: bool = False
    profile_only: bool = False
    disable_zero_shot: bool = False
    zero_shot_model_name: str | None = None
    device: str | None = None
    use_gpu: bool = False
    output_local_path: str = DEFAULT_OUTPUT_LOCAL_PATH
    output_uri: str | None = None

    def __post_init__(self) -> None:
        source_type = (_normalize_optional_text(self.source_type) or "database").casefold()
        object.__setattr__(self, "source_type", source_type)
        if source_type not in SUPPORTED_SOURCE_TYPES:
            raise ScanRequestError(
                f"Unsupported source_type for this job version: {source_type}"
            )

        scan_id = _normalize_optional_text(self.scan_id)
        if not scan_id:
            raise ScanRequestError("scan_id is required")
        object.__setattr__(self, "scan_id", scan_id)

        user_id = _normalize_optional_text(self.user_id)
        if not user_id:
            raise ScanRequestError("user_id is required")
        object.__setattr__(self, "user_id", user_id)

        run_name = _normalize_optional_text(self.run_name)
        if not run_name:
            raise ScanRequestError("run_name is required")
        if len(run_name) > 120:
            raise ScanRequestError("run_name must contain at most 120 characters")
        object.__setattr__(self, "run_name", run_name)

        connection_uri = _normalize_optional_text(self.connection_uri)
        if not connection_uri:
            raise ScanRequestError("connection_uri is required for source_type=database")
        connection_uri = _normalize_connection_uri(connection_uri)
        object.__setattr__(self, "connection_uri", connection_uri)

        database_type = _normalize_database_type(self.database_type or self.dialect)
        if database_type is None:
            raise ScanRequestError(
                "database_type must be one of: "
                f"{', '.join(sorted(SUPPORTED_DATABASE_TYPES))}"
            )
        _validate_connection_database_type(connection_uri, database_type)
        object.__setattr__(self, "database_type", database_type)

        source_id = _normalize_optional_text(self.source_id)
        object.__setattr__(self, "source_id", source_id)

        source_name = _normalize_optional_text(self.source_name) or source_id or "database"
        object.__setattr__(self, "source_name", source_name)

        dialect = _normalize_database_type(self.dialect) or database_type
        if dialect != database_type:
            raise ScanRequestError("dialect must match database_type")
        object.__setattr__(self, "dialect", dialect)
        object.__setattr__(self, "include_schemas", _normalize_text_tuple(self.include_schemas))
        object.__setattr__(self, "include_tables", _normalize_text_tuple(self.include_tables))
        object.__setattr__(self, "exclude_schemas", _normalize_text_tuple(self.exclude_schemas))
        object.__setattr__(self, "exclude_tables", _normalize_text_tuple(self.exclude_tables))

        full_scan = bool(self.confirm_full_scan or self.allow_full_database_scan)
        object.__setattr__(self, "confirm_full_scan", full_scan)
        object.__setattr__(self, "allow_full_database_scan", full_scan)
        if (
            not full_scan
            and not self.include_schemas
            and not self.include_tables
        ):
            raise ScanRequestError(
                "Scan scope is required: set include_schemas, include_tables, "
                "or allow_full_database_scan=true"
            )

        device = _normalize_optional_text(self.device)
        if device is not None:
            device = device.casefold()
        if device is not None and device not in VALID_DEVICES:
            raise ScanRequestError(
                f"device must be one of: {', '.join(sorted(VALID_DEVICES))}"
            )
        object.__setattr__(self, "device", device)

        output_local_path = (
            _normalize_optional_text(self.output_local_path) or DEFAULT_OUTPUT_LOCAL_PATH
        )
        object.__setattr__(self, "output_local_path", output_local_path)

        output_uri = _normalize_optional_text(self.output_uri)
        if output_uri is not None and not output_uri.startswith(SUPPORTED_OUTPUT_URI_SCHEMES):
            raise ScanRequestError("output_uri currently supports only gs:// destinations")
        object.__setattr__(self, "output_uri", output_uri)
        object.__setattr__(
            self,
            "zero_shot_model_name",
            _normalize_optional_text(self.zero_shot_model_name),
        )

    @classmethod
    def from_json(
        cls,
        raw_json: str,
        *,
        generated_scan_id: str | None = None,
    ) -> "ScanRequest":
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ScanRequestError(f"Invalid SCAN_REQUEST_JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ScanRequestError("SCAN_REQUEST_JSON must be a JSON object")
        return cls.from_mapping(payload, generated_scan_id=generated_scan_id)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        generated_scan_id: str | None = None,
    ) -> "ScanRequest":
        scan_id = (
            _text_value(payload.get("scan_id"))
            or _text_value(payload.get("run_id"))
            or generated_scan_id
            or new_scan_id({})
        )
        source_id = _text_value(payload.get("source_id"))
        source_name = (
            _text_value(payload.get("source_name"))
            or _text_value(payload.get("run_name"))
            or source_id
            or "database"
        )
        source_type = _text_value(payload.get("source_type")) or "database"
        allow_full_database_scan = _bool_value(
            payload.get("allow_full_database_scan"),
            default=False,
        )
        confirm_full_scan = _bool_value(
            payload.get("confirm_full_scan"),
            default=allow_full_database_scan,
        )
        return cls(
            scan_id=scan_id,
            user_id=_text_value(payload.get("user_id")) or "",
            run_name=_text_value(payload.get("run_name")) or "",
            database_type=(
                _text_value(payload.get("database_type"))
                or _text_value(payload.get("dialect"))
                or ""
            ),
            confirm_full_scan=confirm_full_scan,
            source_id=source_id,
            source_name=source_name,
            source_type=source_type,
            dialect=_text_value(payload.get("dialect")),
            connection_uri=_text_value(payload.get("connection_uri")) or "",
            include_schemas=_text_tuple_value(payload.get("include_schemas")),
            include_tables=_text_tuple_value(payload.get("include_tables")),
            exclude_schemas=_text_tuple_value(payload.get("exclude_schemas")),
            exclude_tables=_text_tuple_value(payload.get("exclude_tables")),
            include_views=_bool_value(payload.get("include_views"), default=True),
            allow_full_database_scan=allow_full_database_scan,
            profile_only=_bool_value(payload.get("profile_only"), default=False),
            disable_zero_shot=_bool_value(
                payload.get("disable_zero_shot"),
                default=False,
            ),
            zero_shot_model_name=_text_value(payload.get("zero_shot_model_name")),
            device=_text_value(payload.get("device")),
            use_gpu=_bool_value(payload.get("use_gpu"), default=False),
            output_local_path=(
                _text_value(payload.get("output_local_path")) or DEFAULT_OUTPUT_LOCAL_PATH
            ),
            output_uri=_text_value(payload.get("output_uri")),
        )

    @classmethod
    def from_legacy_env(
        cls,
        env: Mapping[str, str],
        *,
        generated_scan_id: str | None = None,
    ) -> "ScanRequest":
        scan_id = _first_env(env, "BBDD_RUN_ID") or generated_scan_id or new_scan_id(env)
        connection_uri = _first_env(
            env,
            "BBDD_DATABASE_URL",
            "TABLE_EXTRACT_DATABASE_URL",
            "DATABASE_URL",
        )
        source_id = _first_env(env, "BBDD_SOURCE_ID")
        source_name = _first_env(env, "BBDD_SOURCE_NAME") or source_id or "database"
        return cls(
            scan_id=scan_id,
            user_id=_first_env(env, "BBDD_USER_ID") or "",
            run_name=_first_env(env, "BBDD_RUN_NAME") or source_name,
            database_type=(
                _first_env(env, "BBDD_DATABASE_TYPE", "BBDD_DATABASE_DIALECT") or ""
            ),
            confirm_full_scan=_truthy_env(env, "BBDD_CONFIRM_FULL_SCAN"),
            source_id=source_id,
            source_name=source_name,
            source_type=_first_env(env, "BBDD_SOURCE_TYPE") or "database",
            dialect=_first_env(env, "BBDD_DATABASE_DIALECT"),
            connection_uri=connection_uri or "",
            include_schemas=_csv_env(env, "BBDD_INCLUDE_SCHEMAS"),
            include_tables=_csv_env(env, "BBDD_INCLUDE_TABLES"),
            exclude_schemas=_csv_env(env, "BBDD_EXCLUDE_SCHEMAS"),
            exclude_tables=_csv_env(env, "BBDD_EXCLUDE_TABLES"),
            include_views=not _truthy_env(env, "BBDD_EXCLUDE_VIEWS"),
            allow_full_database_scan=_truthy_env(env, "BBDD_ALLOW_FULL_DATABASE_SCAN"),
            profile_only=_truthy_env(env, "BBDD_PROFILE_ONLY"),
            disable_zero_shot=_truthy_env(env, "BBDD_DISABLE_ZERO_SHOT"),
            zero_shot_model_name=_first_env(env, "TABLE_EXTRACT_ZERO_SHOT_MODEL"),
            device=_first_env(env, "TABLE_EXTRACT_ZERO_SHOT_DEVICE"),
            use_gpu=_truthy_env(env, "BBDD_USE_GPU"),
            output_local_path=(
                _first_env(env, "BBDD_OUTPUT_LOCAL_PATH") or DEFAULT_OUTPUT_LOCAL_PATH
            ),
            output_uri=_first_env(env, "GCS_OUTPUT_URI"),
        )

    def to_table_extract_argv(self, output_path: str | None = None) -> list[str]:
        argv = [
            "--database-url",
            self.connection_uri,
            "--source-name",
            self.source_name,
            "--output",
            output_path or self.output_local_path,
            "--run-id",
            self.scan_id,
        ]

        _append_optional(argv, "--database-dialect", self.dialect)
        _append_optional(argv, "--zero-shot-model-name", self.zero_shot_model_name)
        if self.use_gpu:
            argv.append("--gpu")
        else:
            _append_optional(argv, "--device", self.device)

        for schema in self.include_schemas:
            argv.extend(["--include-schema", schema])
        for schema in self.exclude_schemas:
            argv.extend(["--exclude-schema", schema])
        for table in self.include_tables:
            argv.extend(["--include-table", table])
        for table in self.exclude_tables:
            argv.extend(["--exclude-table", table])

        if not self.include_views:
            argv.append("--exclude-views")
        if self.profile_only:
            argv.append("--profile-only")
        if self.disable_zero_shot:
            argv.append("--disable-zero-shot")
        return argv


def load_scan_request_from_env(env: Mapping[str, str]) -> ScanRequest:
    generated_scan_id = new_scan_id(env)
    raw_json = env.get("SCAN_REQUEST_JSON")
    if raw_json and raw_json.strip():
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ScanRequestError(f"Invalid SCAN_REQUEST_JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ScanRequestError("SCAN_REQUEST_JSON must be a JSON object")
        payload.setdefault(
            "output_local_path",
            _first_env(env, "BBDD_OUTPUT_LOCAL_PATH") or DEFAULT_OUTPUT_LOCAL_PATH,
        )
        payload.setdefault("output_uri", _first_env(env, "GCS_OUTPUT_URI"))
        payload.setdefault(
            "disable_zero_shot",
            _truthy_env(env, "BBDD_DISABLE_ZERO_SHOT"),
        )
        payload.setdefault(
            "zero_shot_model_name",
            _first_env(env, "TABLE_EXTRACT_ZERO_SHOT_MODEL"),
        )
        payload.setdefault(
            "device",
            _first_env(env, "TABLE_EXTRACT_ZERO_SHOT_DEVICE"),
        )
        payload.setdefault("use_gpu", _truthy_env(env, "BBDD_USE_GPU"))
        return ScanRequest.from_mapping(payload, generated_scan_id=generated_scan_id)
    return ScanRequest.from_legacy_env(env, generated_scan_id=generated_scan_id)


def new_scan_id(env: Mapping[str, str]) -> str:
    execution = _first_env(env, "CLOUD_RUN_EXECUTION")
    if execution:
        task_index = _first_env(env, "CLOUD_RUN_TASK_INDEX") or "0"
        return _slug(f"{execution}-task-{task_index}")
    return f"scan-{uuid.uuid4()}"


def _append_optional(argv: list[str], flag: str, value: str | None) -> None:
    if value:
        argv.extend([flag, value])


def _first_env(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _csv_env(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    return _text_tuple_value(env.get(name))


def _truthy_env(env: Mapping[str, str], name: str) -> bool:
    return _bool_value(env.get(name), default=False)


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "":
            return default
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    raise ScanRequestError(f"Invalid boolean value: {value!r}")


def _text_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _normalize_optional_text(value)
    return _normalize_optional_text(str(value))


def _text_tuple_value(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _normalize_text_tuple(value.split(","))
    if isinstance(value, (list, tuple)):
        return _normalize_text_tuple(value)
    raise ScanRequestError(f"Expected a string or list of strings, got: {value!r}")


def _normalize_text_tuple(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    normalized: list[str] = []
    for value in values or ():
        text = _text_value(value)
        if text:
            normalized.append(text)
    return tuple(normalized)


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _normalize_connection_uri(connection_uri: str) -> str:
    if connection_uri.startswith("postgresql://"):
        return f"postgresql+psycopg://{connection_uri[len('postgresql://'):]}"
    if connection_uri.startswith("postgres://"):
        return f"postgresql+psycopg://{connection_uri[len('postgres://'):]}"
    if connection_uri.startswith("oracle://"):
        return f"oracle+oracledb://{connection_uri[len('oracle://'):]}"
    return connection_uri


def _normalize_database_type(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    normalized = normalized.casefold()
    aliases = {
        "postgres": "postgresql",
        "postgresql+psycopg": "postgresql",
        "oracle+oracledb": "oracle",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_DATABASE_TYPES:
        raise ScanRequestError(
            "database_type must be one of: "
            f"{', '.join(sorted(SUPPORTED_DATABASE_TYPES))}"
        )
    return normalized


def _validate_connection_database_type(
    connection_uri: str,
    database_type: str,
) -> None:
    scheme = connection_uri.partition("://")[0].casefold()
    actual_type = _normalize_database_type(scheme)
    if actual_type != database_type:
        raise ScanRequestError("connection_uri scheme must match database_type")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return slug.strip("-") or "scan"
