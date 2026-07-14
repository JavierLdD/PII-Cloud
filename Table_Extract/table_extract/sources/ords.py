from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from table_extract.models import ColumnProfile, ColumnSample, TableProfile
from table_extract.models._validation import require_non_blank, require_positive_int
from table_extract.operational import OperationalException
from table_extract.sources.database import (
    _OracleRelation,
    _column_sample,
    _normalize_filter_values,
    _normalize_sample_value,
    _optional_int,
    _oracle_data_type,
    _oracle_nullable,
    _oracle_relation_in_scope,
    _qualified_table_name,
    _row_text,
    _row_value,
    _table_key,
    _validate_sample_limit,
)

try:
    import requests
except ImportError:  # pragma: no cover - exercised only in incomplete envs
    requests = None


_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "bearer_token",
    "client_secret",
    "password",
    "secret",
    "token",
}


class OrdsError(OperationalException):
    component = "ords"
    category = "ords_error"
    retryable = False


class OrdsAuthError(OrdsError):
    category = "ords_auth"
    retryable = False


class OrdsTimeoutError(OrdsError):
    category = "ords_timeout"
    retryable = True


class OrdsHttpError(OrdsError):
    category = "ords_http"
    retryable = True


class OrdsResponseError(OrdsError):
    category = "ords_response_invalid"
    retryable = False


@dataclass(frozen=True)
class OrdsScanRequest:
    rest_sql_url: str = field(repr=False)
    source_name: str = "ords"
    auth_mode: str = "none"
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    bearer_token: str | None = field(default=None, repr=False)
    timeout_seconds: float = 30.0
    page_size: int = 500
    max_pages: int = 100
    include_schemas: tuple[str, ...] = field(default_factory=tuple)
    exclude_schemas: tuple[str, ...] = field(default_factory=tuple)
    include_tables: tuple[str, ...] = field(default_factory=tuple)
    exclude_tables: tuple[str, ...] = field(default_factory=tuple)
    include_views: bool = True

    def __post_init__(self) -> None:
        require_non_blank(self.rest_sql_url, "rest_sql_url")
        require_non_blank(self.source_name, "source_name")
        _validate_rest_sql_url(self.rest_sql_url)

        auth_mode = str(self.auth_mode).strip().casefold()
        if auth_mode not in {"none", "basic", "bearer"}:
            raise ValueError("auth_mode must be one of: none, basic, bearer")
        object.__setattr__(self, "auth_mode", auth_mode)

        if auth_mode == "basic":
            require_non_blank(self.username or "", "username")
            require_non_blank(self.password or "", "password")
        if auth_mode == "bearer":
            require_non_blank(self.bearer_token or "", "bearer_token")

        if not isinstance(self.timeout_seconds, int | float) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        require_positive_int(self.page_size, "page_size")
        require_positive_int(self.max_pages, "max_pages")

        object.__setattr__(
            self,
            "include_schemas",
            _normalize_filter_values(self.include_schemas),
        )
        object.__setattr__(
            self,
            "exclude_schemas",
            _normalize_filter_values(self.exclude_schemas),
        )
        object.__setattr__(
            self,
            "include_tables",
            _normalize_filter_values(self.include_tables),
        )
        object.__setattr__(
            self,
            "exclude_tables",
            _normalize_filter_values(self.exclude_tables),
        )


class OrdsSourceAdapter:
    source_type = "ords"
    dialect = "oracle"

    def __init__(self, request: OrdsScanRequest, *, session=None) -> None:
        if requests is None:
            raise RuntimeError(
                "Missing dependency: install requests with "
                "`python -m pip install -r requirements.txt`."
            )

        self.request = request
        self.source_name = request.source_name
        self.source_uri = _safe_ords_url(request.rest_sql_url)
        self._session = session or requests.Session()
        self._tables: tuple[TableProfile, ...] | None = None
        self._columns_by_table: dict[tuple[str | None, str], tuple[ColumnProfile, ...]] = {}

    def iter_tables(self) -> Iterable[TableProfile]:
        self._load_profile()
        return iter(self._tables or ())

    def iter_columns(self, table: TableProfile) -> Iterable[ColumnProfile]:
        self._load_profile()
        return iter(self._columns_by_table.get(_table_key(table), ()))

    def get_column_sample(
        self,
        table: TableProfile,
        column: ColumnProfile,
        *,
        limit: int = 1000,
        max_value_length: int = 256,
    ) -> ColumnSample:
        _validate_sample_limit(limit)
        if limit == 0:
            return _column_sample(
                table,
                column,
                values=(),
                max_value_length=max_value_length,
                truncated=False,
            )

        values, truncated = self._column_values(
            table,
            column,
            limit=limit,
            max_value_length=max_value_length,
        )
        return _column_sample(
            table,
            column,
            values=values,
            max_value_length=max_value_length,
            truncated=truncated,
        )

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if close is not None:
            close()

    def _load_profile(self) -> None:
        if self._tables is not None:
            return

        relations = self._relations()
        selected_keys = {(relation.schema_name, relation.table_name) for relation in relations}
        columns_by_table = self._columns_by_table_for(selected_keys)
        tables = [
            TableProfile(
                schema_name=relation.schema_name,
                table_name=relation.table_name,
                table_type=relation.table_type,
                row_count=relation.row_count,
            )
            for relation in relations
        ]

        self._tables = tuple(tables)
        self._columns_by_table = columns_by_table

    def _relations(self) -> tuple[_OracleRelation, ...]:
        table_rows = self._execute_query(
            """
            SELECT owner, table_name, num_rows
            FROM all_tables
            ORDER BY owner, table_name
            """
        ).rows

        relations: list[_OracleRelation] = []
        for row in table_rows:
            owner = _row_text(row, "owner")
            table_name = _row_text(row, "table_name")
            if owner is None or table_name is None:
                continue
            if not _oracle_relation_in_scope(owner, table_name, self.request):
                continue
            relations.append(
                _OracleRelation(
                    schema_name=owner,
                    table_name=table_name,
                    table_type="table",
                    row_count=_optional_int(_row_value(row, "num_rows")),
                )
            )

        if not self.request.include_views:
            return tuple(relations)

        view_rows = self._execute_query(
            """
            SELECT owner, view_name
            FROM all_views
            ORDER BY owner, view_name
            """
        ).rows
        for row in view_rows:
            owner = _row_text(row, "owner")
            view_name = _row_text(row, "view_name")
            if owner is None or view_name is None:
                continue
            if not _oracle_relation_in_scope(owner, view_name, self.request):
                continue
            relations.append(
                _OracleRelation(
                    schema_name=owner,
                    table_name=view_name,
                    table_type="view",
                    row_count=None,
                )
            )

        return tuple(relations)

    def _columns_by_table_for(
        self,
        selected_keys: set[tuple[str, str]],
    ) -> dict[tuple[str | None, str], tuple[ColumnProfile, ...]]:
        if not selected_keys:
            return {}

        rows = self._execute_query(
            """
            SELECT
                owner,
                table_name,
                column_name,
                data_type,
                data_length,
                char_length,
                data_precision,
                data_scale,
                nullable,
                column_id
            FROM all_tab_columns
            ORDER BY owner, table_name, column_id
            """
        ).rows

        columns_by_table: dict[tuple[str | None, str], list[ColumnProfile]] = {
            key: [] for key in selected_keys
        }
        for row in rows:
            owner = _row_text(row, "owner")
            table_name = _row_text(row, "table_name")
            column_name = _row_text(row, "column_name")
            if owner is None or table_name is None or column_name is None:
                continue
            key = (owner, table_name)
            if key not in selected_keys:
                continue

            ordinal_position = _optional_int(_row_value(row, "column_id"))
            if ordinal_position is None:
                ordinal_position = len(columns_by_table[key]) + 1
            columns_by_table[key].append(
                ColumnProfile(
                    column_name=column_name,
                    data_type=_oracle_data_type(row),
                    nullable=_oracle_nullable(_row_value(row, "nullable")),
                    ordinal_position=ordinal_position,
                )
            )

        return {
            (schema_name, table_name): tuple(columns)
            for (schema_name, table_name), columns in columns_by_table.items()
        }

    def _column_values(
        self,
        table: TableProfile,
        column: ColumnProfile,
        *,
        limit: int,
        max_value_length: int,
    ) -> tuple[tuple[str, ...], bool]:
        preparer = _OrdsIdentifierPreparer()
        column_name = preparer.quote(column.column_name)
        table_name = _qualified_table_name(preparer, table)
        result = self._execute_query(
            f"SELECT {column_name} FROM {table_name} "
            f"WHERE {column_name} IS NOT NULL "
            f"FETCH FIRST {limit} ROWS ONLY",
            max_rows=limit,
        )
        json_column_name = _single_json_column_name(result.metadata, column.column_name)

        values: list[str] = []
        truncated = False
        for row in result.rows:
            normalized = _normalize_sample_value(
                _single_column_value(row, json_column_name, column.column_name),
                max_value_length,
            )
            if normalized is None:
                continue
            value, value_truncated = normalized
            values.append(value)
            truncated = truncated or value_truncated
            if len(values) >= limit:
                break
        return tuple(values), truncated

    def _execute_query(
        self,
        statement: str,
        *,
        max_rows: int | None = None,
    ) -> "_OrdsQueryResult":
        rows: list[dict] = []
        metadata: tuple[dict, ...] = ()
        offset = 0

        for page_index in range(self.request.max_pages):
            if max_rows is not None and len(rows) >= max_rows:
                break

            page_limit = self.request.page_size
            if max_rows is not None:
                page_limit = min(page_limit, max_rows - len(rows))
            if page_limit <= 0:
                break

            payload = self._post_sql(statement, limit=page_limit, offset=offset)
            result_set = _extract_result_set(payload, self.source_uri)
            page_metadata = _extract_metadata(result_set, self.source_uri)
            if not metadata:
                metadata = page_metadata
            page_rows = _extract_rows(result_set, self.source_uri)
            rows.extend(page_rows)

            has_more = bool(result_set.get("hasMore"))
            if not has_more:
                break
            if max_rows is not None and len(rows) >= max_rows:
                break
            if page_index + 1 >= self.request.max_pages:
                raise OrdsResponseError(
                    f"ORDS pagination exceeded max_pages={self.request.max_pages} "
                    f"for {self.source_uri}"
                )

            count = _optional_int(result_set.get("count"))
            if count is None:
                count = len(page_rows)
            if count <= 0:
                raise OrdsResponseError(
                    f"ORDS pagination did not advance for {self.source_uri}"
                )
            response_offset = _optional_int(result_set.get("offset"))
            offset = (response_offset if response_offset is not None else offset) + count

        return _OrdsQueryResult(metadata=metadata, rows=tuple(rows))

    def _post_sql(self, statement: str, *, limit: int, offset: int) -> dict:
        payload = {
            "statementText": statement,
            "limit": limit,
            "offset": offset,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        auth = None
        if self.request.auth_mode == "basic":
            auth = (self.request.username, self.request.password)
        elif self.request.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.request.bearer_token}"

        try:
            response = self._session.post(
                self.request.rest_sql_url,
                json=payload,
                headers=headers,
                timeout=self.request.timeout_seconds,
                auth=auth,
            )
        except requests.Timeout as exc:
            raise OrdsTimeoutError(
                f"ORDS request timed out after {self.request.timeout_seconds} "
                f"seconds for {self.source_uri}",
                safe_context={"source_uri": self.source_uri},
            ) from exc
        except requests.RequestException as exc:
            raise OrdsHttpError(
                f"ORDS request failed for {self.source_uri}: "
                f"{exc.__class__.__name__}",
                category="ords_http_request",
                retryable=True,
                safe_context={"source_uri": self.source_uri},
            ) from exc

        status_code = getattr(response, "status_code", None)
        if status_code in {401, 403}:
            raise OrdsAuthError(
                f"ORDS authentication failed with status {status_code} "
                f"for {self.source_uri}",
                safe_context={
                    "source_uri": self.source_uri,
                    "status_code": status_code,
                },
            )
        if status_code is None or status_code >= 400:
            retryable = status_code is None or status_code >= 500
            raise OrdsHttpError(
                f"ORDS request failed with status {status_code} "
                f"for {self.source_uri}",
                category="ords_http_transient" if retryable else "ords_http",
                retryable=retryable,
                safe_context={
                    "source_uri": self.source_uri,
                    "status_code": status_code,
                },
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OrdsResponseError(
                f"ORDS response was not valid JSON for {self.source_uri}",
                safe_context={"source_uri": self.source_uri},
            ) from exc

        if not isinstance(body, dict):
            raise OrdsResponseError(
                f"ORDS response JSON must be an object for {self.source_uri}",
                safe_context={"source_uri": self.source_uri},
            )
        return body


def build_ords_source_adapter(request: OrdsScanRequest) -> OrdsSourceAdapter:
    return OrdsSourceAdapter(request)


@dataclass(frozen=True)
class _OrdsQueryResult:
    metadata: tuple[dict, ...]
    rows: tuple[dict, ...]


class _OrdsIdentifierPreparer:
    def quote(self, name: str) -> str:
        return f'"{name.replace(chr(34), chr(34) * 2)}"'

    def quote_schema(self, name: str) -> str:
        return self.quote(name)


def _validate_rest_sql_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("rest_sql_url must be an http(s) URL")


def _safe_ords_url(value: str) -> str:
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"

    query_items = []
    for key, raw_value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() in _SENSITIVE_QUERY_KEYS or "token" in key.casefold():
            query_items.append((key, "***"))
        else:
            query_items.append((key, raw_value))

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(query_items),
            parsed.fragment,
        )
    )


def _extract_result_set(payload: dict, source_uri: str) -> dict:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise OrdsResponseError(
            f"ORDS response did not include items for {source_uri}"
        )
    first_item = items[0]
    if not isinstance(first_item, dict):
        raise OrdsResponseError(
            f"ORDS response item must be an object for {source_uri}"
        )
    result_set = first_item.get("resultSet")
    if not isinstance(result_set, dict):
        raise OrdsResponseError(
            f"ORDS response did not include resultSet for {source_uri}"
        )
    return result_set


def _extract_metadata(result_set: dict, source_uri: str) -> tuple[dict, ...]:
    metadata = result_set.get("metadata")
    if metadata is None:
        return ()
    if not isinstance(metadata, list) or not all(isinstance(item, dict) for item in metadata):
        raise OrdsResponseError(
            f"ORDS resultSet metadata must be a list for {source_uri}"
        )
    return tuple(metadata)


def _extract_rows(result_set: dict, source_uri: str) -> tuple[dict, ...]:
    rows = result_set.get("items")
    if rows is None:
        return ()
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise OrdsResponseError(
            f"ORDS resultSet items must be a list for {source_uri}"
        )
    return tuple(rows)


def _single_json_column_name(metadata: tuple[dict, ...], column_name: str) -> str | None:
    if not metadata:
        return None
    for item in metadata:
        metadata_column_name = _row_text(item, "columnName")
        if metadata_column_name is not None and metadata_column_name.casefold() == column_name.casefold():
            return _row_text(item, "jsonColumnName") or metadata_column_name
    return _row_text(metadata[0], "jsonColumnName") or _row_text(metadata[0], "columnName")


def _single_column_value(row: dict, json_column_name: str | None, column_name: str):
    if json_column_name is not None:
        value = _row_value(row, json_column_name)
        if value is not None:
            return value
    value = _row_value(row, column_name)
    if value is not None:
        return value
    if len(row) == 1:
        return next(iter(row.values()))
    return None
