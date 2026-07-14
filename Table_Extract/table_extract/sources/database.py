from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from table_extract.models import ColumnProfile, ColumnSample, TableProfile
from table_extract.models._validation import require_non_blank
from table_extract.operational import (
    OperationalErrorInfo,
    OperationalException,
    emit_operational_log,
    sanitize_text,
)


DEFAULT_EXCLUDED_SCHEMAS = frozenset(
    {
        "information_schema",
        "mysql",
        "performance_schema",
        "pg_catalog",
        "pg_toast",
        "sys",
        "system",
    }
)

ORACLE_DEFAULT_EXCLUDED_SCHEMAS = frozenset(
    {
        "ANONYMOUS",
        "APPQOSSYS",
        "AUDSYS",
        "CTXSYS",
        "DBSNMP",
        "DIP",
        "DVF",
        "DVSYS",
        "EXFSYS",
        "GSMADMIN_INTERNAL",
        "LBACSYS",
        "MDSYS",
        "OJVMSYS",
        "OLAPSYS",
        "ORACLE_OCM",
        "ORDDATA",
        "ORDPLUGINS",
        "ORDSYS",
        "OUTLN",
        "REMOTE_SCHEDULER_AGENT",
        "SI_INFORMTN_SCHEMA",
        "SYS",
        "SYSBACKUP",
        "SYSDG",
        "SYSKM",
        "SYSRAC",
        "SYSTEM",
        "WMSYS",
        "XDB",
        "XS$NULL",
    }
)


@dataclass(frozen=True)
class DatabaseScanRequest:
    connection_uri: str = field(repr=False)
    source_name: str = "database"
    dialect: str | None = None
    include_schemas: tuple[str, ...] = field(default_factory=tuple)
    exclude_schemas: tuple[str, ...] = field(default_factory=tuple)
    include_tables: tuple[str, ...] = field(default_factory=tuple)
    exclude_tables: tuple[str, ...] = field(default_factory=tuple)
    include_views: bool = True

    def __post_init__(self) -> None:
        require_non_blank(self.connection_uri, "connection_uri")
        require_non_blank(self.source_name, "source_name")
        object.__setattr__(self, "dialect", _normalize_optional_name(self.dialect))
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


class DatabaseOperationalError(OperationalException):
    component = "database"
    category = "database_error"
    retryable = True


class DatabaseConnectionError(DatabaseOperationalError):
    category = "database_connection"
    retryable = True


class DatabasePermissionError(DatabaseOperationalError):
    category = "database_permission"
    retryable = False


class DatabaseIntrospectionError(DatabaseOperationalError):
    category = "database_introspection"
    retryable = False


class DatabaseSourceAdapter:
    source_type = "database"

    def __init__(self, request: DatabaseScanRequest) -> None:
        try:
            from sqlalchemy import create_engine, inspect
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: install SQLAlchemy with "
                "`python -m pip install -r requirements.txt`."
            ) from exc

        self.request = request
        self.source_name = request.source_name
        self._engine = create_engine(request.connection_uri)
        self._inspect = inspect
        self.dialect = request.dialect or self._engine.dialect.name
        self.source_uri = _safe_connection_uri(self._engine.url)
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

        values, truncated = _generic_column_values(
            self._engine,
            table,
            column,
            limit,
            max_value_length,
        )
        return _column_sample(
            table,
            column,
            values=values,
            max_value_length=max_value_length,
            truncated=truncated,
        )

    def close(self) -> None:
        self._engine.dispose()

    def _load_profile(self) -> None:
        if self._tables is not None:
            return

        try:
            inspector = self._inspect(self._engine)
            tables: list[TableProfile] = []
            columns_by_table: dict[tuple[str | None, str], tuple[ColumnProfile, ...]] = {}

            for schema_name in _schema_names(inspector, self.request):
                for table_name in _relation_names(
                    inspector.get_table_names,
                    schema_name,
                    self.request,
                ):
                    table = TableProfile(
                        schema_name=schema_name,
                        table_name=table_name,
                        table_type="table",
                        row_count=self._row_count_for_table(schema_name, table_name),
                    )
                    tables.append(table)
                    columns_by_table[_table_key(table)] = _columns_for_table(
                        inspector,
                        table,
                    )

                if not self.request.include_views:
                    continue

                for view_name in _relation_names(
                    inspector.get_view_names,
                    schema_name,
                    self.request,
                ):
                    table = TableProfile(
                        schema_name=schema_name,
                        table_name=view_name,
                        table_type="view",
                        row_count=None,
                    )
                    tables.append(table)
                    columns_by_table[_table_key(table)] = _columns_for_table(
                        inspector,
                        table,
                    )
        except DatabaseOperationalError:
            raise
        except Exception as exc:
            raise _database_operational_error(
                exc,
                dialect=self.dialect,
                source_uri=self.source_uri,
                operation="profile_metadata",
            ) from exc

        self._tables = tuple(tables)
        self._columns_by_table = columns_by_table
        _emit_profile_empty_if_filtered(self.request, self.dialect, self.source_uri, tables)

    def _row_count_for_table(self, schema_name: str | None, table_name: str) -> int | None:
        return None


def build_database_source_adapter(request: DatabaseScanRequest) -> DatabaseSourceAdapter:
    dialect = request.dialect or _dialect_from_connection_uri(request.connection_uri)
    if dialect == "oracle":
        return OracleDatabaseSourceAdapter(request)
    if dialect == "postgresql":
        return PostgreSQLDatabaseSourceAdapter(request)
    return DatabaseSourceAdapter(request)


class OracleDatabaseSourceAdapter(DatabaseSourceAdapter):
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

        values, truncated = _oracle_column_values(
            self._engine,
            table,
            column,
            limit,
            max_value_length,
        )
        return _column_sample(
            table,
            column,
            values=values,
            max_value_length=max_value_length,
            truncated=truncated,
        )

    def _load_profile(self) -> None:
        if self._tables is not None:
            return

        try:
            relations = _oracle_relations(self._engine, self.request)
            selected_keys = {(relation.schema_name, relation.table_name) for relation in relations}
            columns_by_table = _oracle_columns_by_table(self._engine, selected_keys)
            tables = [
                TableProfile(
                    schema_name=relation.schema_name,
                    table_name=relation.table_name,
                    table_type=relation.table_type,
                    row_count=relation.row_count,
                )
                for relation in relations
            ]
        except DatabaseOperationalError:
            raise
        except Exception as exc:
            raise _database_operational_error(
                exc,
                dialect=self.dialect,
                source_uri=self.source_uri,
                operation="oracle_profile_metadata",
            ) from exc

        self._tables = tuple(tables)
        self._columns_by_table = columns_by_table
        _emit_profile_empty_if_filtered(self.request, self.dialect, self.source_uri, tables)


class PostgreSQLDatabaseSourceAdapter(DatabaseSourceAdapter):
    def __init__(self, request: DatabaseScanRequest) -> None:
        super().__init__(request)
        self._row_estimates: dict[tuple[str | None, str], int | None] | None = None

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

        values, truncated = _postgres_column_values(
            self._engine,
            table,
            column,
            limit,
            max_value_length,
        )
        return _column_sample(
            table,
            column,
            values=values,
            max_value_length=max_value_length,
            truncated=truncated,
        )

    def _row_count_for_table(self, schema_name: str | None, table_name: str) -> int | None:
        if self._row_estimates is None:
            self._row_estimates = _postgres_row_estimates(self._engine)
        return self._row_estimates.get((schema_name, table_name))


def _schema_names(inspector, request: DatabaseScanRequest) -> tuple[str | None, ...]:
    try:
        raw_schemas = tuple(inspector.get_schema_names())
    except NotImplementedError:
        raw_schemas = (None,)

    schemas: tuple[str | None, ...] = raw_schemas or (None,)
    if request.include_schemas:
        included = set(request.include_schemas)
        schemas = tuple(schema for schema in schemas if schema in included)

    excluded = set(request.exclude_schemas)
    if not request.include_schemas:
        excluded.update(DEFAULT_EXCLUDED_SCHEMAS)

    return tuple(schema for schema in schemas if schema not in excluded)


def _relation_names(
    get_names,
    schema_name: str | None,
    request: DatabaseScanRequest,
) -> tuple[str, ...]:
    try:
        names = tuple(get_names(schema=schema_name))
    except NotImplementedError:
        return ()

    if request.include_tables:
        included = set(request.include_tables)
        names = tuple(name for name in names if name in included)
    if request.exclude_tables:
        excluded = set(request.exclude_tables)
        names = tuple(name for name in names if name not in excluded)
    return names


def _columns_for_table(inspector, table: TableProfile) -> tuple[ColumnProfile, ...]:
    columns: list[ColumnProfile] = []
    for index, column in enumerate(
        inspector.get_columns(table.table_name, schema=table.schema_name),
        start=1,
    ):
        raw_type = column.get("type")
        columns.append(
            ColumnProfile(
                column_name=str(column["name"]),
                data_type=str(raw_type) if raw_type is not None else None,
                nullable=column.get("nullable"),
                ordinal_position=index,
            )
        )
    return tuple(columns)


@dataclass(frozen=True)
class _OracleRelation:
    schema_name: str
    table_name: str
    table_type: str
    row_count: int | None


def _oracle_relations(engine, request: DatabaseScanRequest) -> tuple[_OracleRelation, ...]:
    table_rows = _execute_mappings(
        engine,
        """
        SELECT owner, table_name, num_rows
        FROM all_tables
        ORDER BY owner, table_name
        """,
        operation="oracle_all_tables",
    )
    relations: list[_OracleRelation] = []
    for row in table_rows:
        owner = _row_text(row, "owner")
        table_name = _row_text(row, "table_name")
        if owner is None or table_name is None:
            continue
        if not _oracle_relation_in_scope(owner, table_name, request):
            continue
        relations.append(
            _OracleRelation(
                schema_name=owner,
                table_name=table_name,
                table_type="table",
                row_count=_optional_int(_row_value(row, "num_rows")),
            )
        )

    if not request.include_views:
        return tuple(relations)

    view_rows = _execute_mappings(
        engine,
        """
        SELECT owner, view_name
        FROM all_views
        ORDER BY owner, view_name
        """,
        operation="oracle_all_views",
    )
    for row in view_rows:
        owner = _row_text(row, "owner")
        view_name = _row_text(row, "view_name")
        if owner is None or view_name is None:
            continue
        if not _oracle_relation_in_scope(owner, view_name, request):
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


def _oracle_columns_by_table(
    engine,
    selected_keys: set[tuple[str, str]],
) -> dict[tuple[str | None, str], tuple[ColumnProfile, ...]]:
    if not selected_keys:
        return {}

    rows = _execute_mappings(
        engine,
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
        """,
        operation="oracle_all_tab_columns",
    )
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


def _oracle_relation_in_scope(
    owner: str,
    relation_name: str,
    request: DatabaseScanRequest,
) -> bool:
    owner_key = owner.casefold()
    table_key = relation_name.casefold()

    if request.include_schemas and owner_key not in _casefold_set(request.include_schemas):
        return False

    excluded_schemas = set(_casefold_set(request.exclude_schemas))
    if not request.include_schemas:
        excluded_schemas.update(schema.casefold() for schema in ORACLE_DEFAULT_EXCLUDED_SCHEMAS)
    if owner_key in excluded_schemas:
        return False

    if request.include_tables and table_key not in _casefold_set(request.include_tables):
        return False
    if table_key in _casefold_set(request.exclude_tables):
        return False

    return True


def _oracle_data_type(row) -> str | None:
    data_type = _row_text(row, "data_type")
    if data_type is None:
        return None

    upper_type = data_type.upper()
    if upper_type in {"CHAR", "NCHAR", "NVARCHAR2", "VARCHAR2"}:
        length = _optional_int(_row_value(row, "char_length"))
        if length is None:
            length = _optional_int(_row_value(row, "data_length"))
        return f"{data_type}({length})" if length is not None else data_type

    if upper_type in {"RAW"}:
        length = _optional_int(_row_value(row, "data_length"))
        return f"{data_type}({length})" if length is not None else data_type

    if upper_type == "NUMBER":
        precision = _optional_int(_row_value(row, "data_precision"))
        scale = _optional_int(_row_value(row, "data_scale"))
        if precision is not None and scale is not None:
            return f"{data_type}({precision},{scale})"
        if precision is not None:
            return f"{data_type}({precision})"
        return data_type

    if upper_type == "FLOAT":
        precision = _optional_int(_row_value(row, "data_precision"))
        return f"{data_type}({precision})" if precision is not None else data_type

    return data_type


def _oracle_nullable(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    if text == "y":
        return True
    if text == "n":
        return False
    return None


def _oracle_column_values(
    engine,
    table: TableProfile,
    column: ColumnProfile,
    limit: int,
    max_value_length: int,
) -> tuple[tuple[str, ...], bool]:
    from sqlalchemy import text

    preparer = engine.dialect.identifier_preparer
    column_name = preparer.quote(column.column_name)
    table_name = _qualified_table_name(preparer, table)
    statement = text(
        f"SELECT {column_name} FROM {table_name} "
        f"WHERE {column_name} IS NOT NULL "
        f"FETCH FIRST {limit} ROWS ONLY"
    )

    values: list[str] = []
    truncated = False
    try:
        with engine.connect() as connection:
            for row in connection.execute(statement):
                normalized = _normalize_sample_value(row[0], max_value_length)
                if normalized is None:
                    continue
                value, value_truncated = normalized
                values.append(value)
                truncated = truncated or value_truncated
                if len(values) >= limit:
                    break
    except DatabaseOperationalError:
        raise
    except Exception as exc:
        raise _database_operational_error(
            exc,
            dialect=getattr(engine.dialect, "name", None),
            source_uri=_safe_connection_uri(engine.url),
            operation="oracle_column_sample",
        ) from exc
    return tuple(values), truncated


def _execute_mappings(engine, statement: str, *, operation: str) -> tuple:
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            return tuple(connection.execute(text(statement)).mappings())
    except DatabaseOperationalError:
        raise
    except Exception as exc:
        raise _database_operational_error(
            exc,
            dialect=getattr(engine.dialect, "name", None),
            source_uri=_safe_connection_uri(engine.url),
            operation=operation,
        ) from exc


def _postgres_row_estimates(engine) -> dict[tuple[str | None, str], int | None]:
    try:
        from sqlalchemy import text
        from sqlalchemy.exc import SQLAlchemyError
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install SQLAlchemy with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    query = text(
        """
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            CASE
                WHEN c.reltuples < 0 THEN NULL
                ELSE GREATEST(c.reltuples::bigint, 0)
            END AS row_estimate
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
        """
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(query).mappings()
            return {
                (row["schema_name"], row["table_name"]): row["row_estimate"]
                for row in rows
            }
    except SQLAlchemyError:
        return {}


def _generic_column_values(
    engine,
    table: TableProfile,
    column: ColumnProfile,
    limit: int,
    max_value_length: int,
) -> tuple[tuple[str, ...], bool]:
    from sqlalchemy import text

    preparer = engine.dialect.identifier_preparer
    column_name = preparer.quote(column.column_name)
    table_name = _qualified_table_name(preparer, table)
    statement = text(
        f"SELECT {column_name} FROM {table_name} "
        f"WHERE {column_name} IS NOT NULL "
        f"AND NULLIF(TRIM(CAST({column_name} AS TEXT)), '') IS NOT NULL "
        "LIMIT :limit"
    )

    values: list[str] = []
    truncated = False
    try:
        with engine.connect() as connection:
            for row in connection.execute(statement, {"limit": limit}):
                normalized = _normalize_sample_value(row[0], max_value_length)
                if normalized is None:
                    continue
                value, value_truncated = normalized
                values.append(value)
                truncated = truncated or value_truncated
                if len(values) >= limit:
                    break
    except DatabaseOperationalError:
        raise
    except Exception as exc:
        raise _database_operational_error(
            exc,
            dialect=getattr(engine.dialect, "name", None),
            source_uri=_safe_connection_uri(engine.url),
            operation="database_column_sample",
        ) from exc
    return tuple(values), truncated


def _postgres_column_values(
    engine,
    table: TableProfile,
    column: ColumnProfile,
    limit: int,
    max_value_length: int,
) -> tuple[tuple[str, ...], bool]:
    from sqlalchemy import text

    preparer = engine.dialect.identifier_preparer
    column_name = preparer.quote(column.column_name)
    table_name = _qualified_table_name(preparer, table)
    statement = text(
        f"SELECT {column_name} FROM {table_name} "
        f"WHERE {column_name} IS NOT NULL "
        "LIMIT :limit"
    )

    values: list[str] = []
    truncated = False
    try:
        with engine.connect() as connection:
            for row in connection.execute(statement, {"limit": limit}):
                normalized = _normalize_sample_value(row[0], max_value_length)
                if normalized is None:
                    continue
                value, value_truncated = normalized
                values.append(value)
                truncated = truncated or value_truncated
                if len(values) >= limit:
                    break
    except DatabaseOperationalError:
        raise
    except Exception as exc:
        raise _database_operational_error(
            exc,
            dialect=getattr(engine.dialect, "name", None),
            source_uri=_safe_connection_uri(engine.url),
            operation="postgres_column_sample",
        ) from exc
    return tuple(values), truncated


def _qualified_table_name(preparer, table: TableProfile) -> str:
    table_name = preparer.quote(table.table_name)
    if table.schema_name:
        return f"{preparer.quote_schema(table.schema_name)}.{table_name}"
    return table_name


def _column_sample(
    table: TableProfile,
    column: ColumnProfile,
    *,
    values: tuple[str, ...],
    max_value_length: int,
    truncated: bool,
) -> ColumnSample:
    return ColumnSample(
        table_name=table.table_name,
        schema_name=table.schema_name,
        column_name=column.column_name,
        values=values,
        sampled_count=len(values),
        non_null_count=len(values),
        max_value_length=max_value_length,
        truncated=truncated,
    )


def _table_key(table: TableProfile) -> tuple[str | None, str]:
    return (table.schema_name, table.table_name)


def _safe_connection_uri(url) -> str:
    return url.render_as_string(hide_password=True)


def _dialect_from_connection_uri(connection_uri: str) -> str | None:
    try:
        from sqlalchemy.engine import make_url
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install SQLAlchemy with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    drivername = make_url(connection_uri).get_backend_name()
    return _normalize_optional_name(drivername)


def _normalize_sample_value(
    value: object,
    max_value_length: int,
) -> tuple[str, bool] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_value_length:
        return text[:max_value_length], True
    return text, False


def _validate_sample_limit(limit: int) -> None:
    if not isinstance(limit, int):
        raise TypeError("limit must be an integer")
    if limit < 0:
        raise ValueError("limit must be non-negative")


def _normalize_optional_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if not normalized:
        return None
    return normalized


def _normalize_filter_values(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return tuple(normalized)


def _casefold_set(values: Iterable[str]) -> set[str]:
    return {value.casefold() for value in values}


def _row_text(row, key: str) -> str | None:
    value = _row_value(row, key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_value(row, key: str):
    for candidate in (key, key.upper(), key.lower()):
        try:
            return row[candidate]
        except (KeyError, TypeError):
            pass
        getter = getattr(row, "get", None)
        if getter is not None:
            value = getter(candidate)
            if value is not None:
                return value
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _database_operational_error(
    exc: Exception,
    *,
    dialect: str | None,
    source_uri: str | None,
    operation: str,
) -> DatabaseOperationalError:
    context = {
        "dialect": dialect,
        "source_uri": source_uri,
        "operation": operation,
        "error_type": exc.__class__.__name__,
    }
    if _is_permission_error(exc):
        return DatabasePermissionError(
            f"Database permissions are insufficient while running {operation}.",
            safe_context=context,
        )
    if _is_connection_error(exc):
        return DatabaseConnectionError(
            f"Database connection failed while running {operation}.",
            safe_context=context,
        )
    return DatabaseIntrospectionError(
        f"Database introspection failed while running {operation}: "
        f"{sanitize_text(exc.__class__.__name__)}.",
        safe_context=context,
    )


def _is_permission_error(exc: Exception) -> bool:
    text = _exception_text(exc)
    return any(
        marker in text
        for marker in (
            "insufficient privilege",
            "insufficient privileges",
            "insufficientprivilege",
            "not authorized",
            "ora-01031",
            "ora-00942",
            "permission denied",
            "privilege",
            "sqlstate: 42501",
        )
    )


def _is_connection_error(exc: Exception) -> bool:
    text = _exception_text(exc)
    class_name = exc.__class__.__name__.casefold()
    return (
        "operationalerror" in class_name
        or "interfaceerror" in class_name
        or any(
            marker in text
            for marker in (
                "connection refused",
                "connection reset",
                "could not connect",
                "database is locked",
                "ora-12154",
                "ora-12514",
                "ora-12541",
                "server closed the connection",
                "timeout",
                "timed out",
            )
        )
    )


def _exception_text(exc: Exception) -> str:
    parts = [exc.__class__.__name__, str(exc)]
    original = getattr(exc, "orig", None)
    if original is not None:
        parts.extend([original.__class__.__name__, str(original)])
    return " ".join(parts).casefold()


def _has_explicit_filters(request: DatabaseScanRequest) -> bool:
    return bool(
        request.include_schemas
        or request.exclude_schemas
        or request.include_tables
        or request.exclude_tables
    )


def _emit_profile_empty_if_filtered(
    request: DatabaseScanRequest,
    dialect: str | None,
    source_uri: str | None,
    tables: list[TableProfile],
) -> None:
    if tables or not _has_explicit_filters(request):
        return
    emit_operational_log(
        "profile_empty",
        OperationalErrorInfo(
            component="database",
            category="profile_empty",
            retryable=False,
            message="Database profile produced no relations after applying filters.",
        ),
        safe_context={
            "source_name": request.source_name,
            "dialect": dialect,
            "source_uri": source_uri,
            "include_schemas": request.include_schemas,
            "exclude_schemas": request.exclude_schemas,
            "include_tables": request.include_tables,
            "exclude_tables": request.exclude_tables,
        },
    )
