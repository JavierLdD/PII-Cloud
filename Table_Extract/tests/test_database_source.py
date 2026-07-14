from pathlib import Path
import json
import os

import pytest
import sqlalchemy

from table_extract.models import ColumnProfile, TableProfile
from table_extract.profiling import profile_source
from table_extract.sources import (
    DatabaseConnectionError,
    DatabasePermissionError,
    DatabaseScanRequest,
    DatabaseSourceAdapter,
    OracleDatabaseSourceAdapter,
    PostgreSQLDatabaseSourceAdapter,
    SourceAdapter,
    build_database_source_adapter,
)


def sqlite_uri(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'pii_tables.db'}"


def create_sqlite_database(connection_uri: str) -> None:
    engine = sqlalchemy.create_engine(connection_uri)
    metadata = sqlalchemy.MetaData()
    sqlalchemy.Table(
        "customers",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column("email", sqlalchemy.String(255), nullable=False),
        sqlalchemy.Column("score", sqlalchemy.Integer, nullable=True),
    )
    sqlalchemy.Table(
        "audit_logs",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            sqlalchemy.text(
                """
                INSERT INTO customers (id, email, score)
                VALUES
                    (1, '  a@example.com  ', 10),
                    (2, '', 20),
                    (3, '   ', NULL),
                    (4, 'long@example.com', 30)
                """
            )
        )
        connection.execute(
            sqlalchemy.text(
                "CREATE VIEW customer_emails AS "
                "SELECT id, email FROM customers"
            )
        )
    engine.dispose()


class FakeSafeURL:
    def render_as_string(self, hide_password: bool = True) -> str:
        if hide_password:
            return "oracle+oracledb://user:***@localhost/FREEPDB1"
        return "oracle+oracledb://user:secret@localhost/FREEPDB1"


class FakeIdentifierPreparer:
    def quote(self, name: str) -> str:
        return f'"{name}"'

    def quote_schema(self, name: str) -> str:
        return f'"{name}"'


class FakeOracleDialect:
    name = "oracle"
    identifier_preparer = FakeIdentifierPreparer()


class FakeOracleResult:
    def __init__(self, rows) -> None:
        self._rows = tuple(rows)

    def __iter__(self):
        return iter(self._rows)

    def mappings(self):
        return iter(self._rows)


class FakeOracleConnection:
    def __init__(self, engine) -> None:
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, statement, params=None):
        if self.engine.execute_error is not None:
            raise self.engine.execute_error
        sql = str(statement)
        self.engine.executed.append((sql, params))
        lowered = sql.lower()
        if "from all_tables" in lowered:
            return FakeOracleResult(self.engine.table_rows)
        if "from all_views" in lowered:
            return FakeOracleResult(self.engine.view_rows)
        if "from all_tab_columns" in lowered:
            return FakeOracleResult(self.engine.column_rows)
        return FakeOracleResult(self.engine.sample_rows)


class FakeOracleEngine:
    def __init__(
        self,
        *,
        table_rows=(),
        view_rows=(),
        column_rows=(),
        sample_rows=(),
        execute_error: Exception | None = None,
    ) -> None:
        self.dialect = FakeOracleDialect()
        self.url = FakeSafeURL()
        self.table_rows = tuple(table_rows)
        self.view_rows = tuple(view_rows)
        self.column_rows = tuple(column_rows)
        self.sample_rows = tuple(sample_rows)
        self.execute_error = execute_error
        self.executed = []
        self.disposed = False

    def connect(self):
        return FakeOracleConnection(self)

    def dispose(self) -> None:
        self.disposed = True


def oracle_uri() -> str:
    return "oracle+oracledb://user:secret@localhost/?service_name=FREEPDB1"


def fake_oracle_adapter(
    request: DatabaseScanRequest,
    engine: FakeOracleEngine,
) -> OracleDatabaseSourceAdapter:
    adapter = object.__new__(OracleDatabaseSourceAdapter)
    adapter.request = request
    adapter.source_name = request.source_name
    adapter._engine = engine
    adapter._inspect = None
    adapter.dialect = "oracle"
    adapter.source_uri = engine.url.render_as_string(hide_password=True)
    adapter._tables = None
    adapter._columns_by_table = {}
    return adapter


def oracle_catalog_engine() -> FakeOracleEngine:
    return FakeOracleEngine(
        table_rows=(
            {"OWNER": "APP", "TABLE_NAME": "CONTACTS", "NUM_ROWS": 3},
            {"OWNER": "HR", "TABLE_NAME": "EMPLOYEES", "NUM_ROWS": None},
            {"OWNER": "SYS", "TABLE_NAME": "SYSTEM_TABLE", "NUM_ROWS": 1},
        ),
        view_rows=(
            {"OWNER": "APP", "VIEW_NAME": "CONTACT_EMAILS"},
            {"OWNER": "SYSTEM", "VIEW_NAME": "SYSTEM_VIEW"},
        ),
        column_rows=(
            {
                "OWNER": "APP",
                "TABLE_NAME": "CONTACTS",
                "COLUMN_NAME": "CONTACT_ID",
                "DATA_TYPE": "NUMBER",
                "DATA_LENGTH": 22,
                "CHAR_LENGTH": None,
                "DATA_PRECISION": 10,
                "DATA_SCALE": 0,
                "NULLABLE": "N",
                "COLUMN_ID": 1,
            },
            {
                "OWNER": "APP",
                "TABLE_NAME": "CONTACTS",
                "COLUMN_NAME": "EMAIL",
                "DATA_TYPE": "VARCHAR2",
                "DATA_LENGTH": 100,
                "CHAR_LENGTH": 100,
                "DATA_PRECISION": None,
                "DATA_SCALE": None,
                "NULLABLE": "Y",
                "COLUMN_ID": 2,
            },
            {
                "OWNER": "APP",
                "TABLE_NAME": "CONTACT_EMAILS",
                "COLUMN_NAME": "EMAIL",
                "DATA_TYPE": "VARCHAR2",
                "DATA_LENGTH": 100,
                "CHAR_LENGTH": 100,
                "DATA_PRECISION": None,
                "DATA_SCALE": None,
                "NULLABLE": "Y",
                "COLUMN_ID": 1,
            },
            {
                "OWNER": "HR",
                "TABLE_NAME": "EMPLOYEES",
                "COLUMN_NAME": "EMPLOYEE_ID",
                "DATA_TYPE": "NUMBER",
                "DATA_LENGTH": 22,
                "CHAR_LENGTH": None,
                "DATA_PRECISION": None,
                "DATA_SCALE": None,
                "NULLABLE": "N",
                "COLUMN_ID": 1,
            },
            {
                "OWNER": "SYS",
                "TABLE_NAME": "SYSTEM_TABLE",
                "COLUMN_NAME": "SECRET",
                "DATA_TYPE": "VARCHAR2",
                "DATA_LENGTH": 10,
                "CHAR_LENGTH": 10,
                "DATA_PRECISION": None,
                "DATA_SCALE": None,
                "NULLABLE": "Y",
                "COLUMN_ID": 1,
            },
        ),
    )


def test_build_database_source_adapter_profiles_sqlite_tables_and_views(
    tmp_path: Path,
) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(connection_uri=uri, source_name="sqlite_fixture")
    adapter = build_database_source_adapter(request)

    try:
        assert isinstance(adapter, SourceAdapter)
        profile = profile_source(adapter)
    finally:
        adapter.close()

    assert profile.source_name == "sqlite_fixture"
    assert profile.source_type == "database"
    assert profile.dialect == "sqlite"
    assert uri not in repr(request)
    assert profile.source_uri == uri
    table_names = {(table.table_name, table.table_type) for table in profile.tables}
    assert table_names == {
        ("audit_logs", "table"),
        ("customers", "table"),
        ("customer_emails", "view"),
    }

    customers = next(table for table in profile.tables if table.table_name == "customers")
    assert customers.row_count is None
    assert [(column.column_name, column.ordinal_position) for column in customers.columns] == [
        ("id", 1),
        ("email", 2),
        ("score", 3),
    ]
    assert customers.columns[1].nullable is False
    assert customers.columns[1].data_type is not None


def test_database_source_adapter_can_exclude_views(tmp_path: Path) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(
        connection_uri=uri,
        source_name="sqlite_fixture",
        include_views=False,
    )
    adapter = build_database_source_adapter(request)

    try:
        profile = profile_source(adapter)
    finally:
        adapter.close()

    assert sorted((table.table_name, table.table_type) for table in profile.tables) == [
        ("audit_logs", "table"),
        ("customers", "table"),
    ]


def test_database_source_adapter_respects_table_filters(tmp_path: Path) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(
        connection_uri=uri,
        source_name="sqlite_fixture",
        include_tables=("customers", "customer_emails"),
        exclude_tables=("customer_emails",),
    )
    adapter = build_database_source_adapter(request)

    try:
        profile = profile_source(adapter)
    finally:
        adapter.close()

    assert [(table.table_name, table.table_type) for table in profile.tables] == [
        ("customers", "table"),
    ]


def test_database_profile_empty_with_explicit_filters_logs_warning(
    tmp_path: Path,
    capsys,
) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(
        connection_uri=uri,
        source_name="sqlite_fixture",
        include_tables=("missing_table",),
    )
    adapter = build_database_source_adapter(request)

    try:
        profile = profile_source(adapter)
    finally:
        adapter.close()

    captured = capsys.readouterr()
    warning = json.loads(captured.err)
    assert profile.tables == ()
    assert captured.out == ""
    assert warning["event"] == "profile_empty"
    assert warning["category"] == "profile_empty"
    assert warning["safe_context"]["include_tables"] == ["missing_table"]


def test_database_source_adapter_samples_sqlite_column_values(tmp_path: Path) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(connection_uri=uri, source_name="sqlite_fixture")
    adapter = build_database_source_adapter(request)

    try:
        table = next(table for table in adapter.iter_tables() if table.table_name == "customers")
        columns = tuple(adapter.iter_columns(table))
        email_column = next(column for column in columns if column.column_name == "email")
        score_column = next(column for column in columns if column.column_name == "score")

        email_sample = adapter.get_column_sample(
            table,
            email_column,
            limit=2,
            max_value_length=10,
        )
        score_sample = adapter.get_column_sample(table, score_column, limit=3)
    finally:
        adapter.close()

    assert email_sample.values == ("a@example.", "long@examp")
    assert email_sample.sampled_count == 2
    assert email_sample.non_null_count == 2
    assert email_sample.truncated
    assert score_sample.values == ("10", "20", "30")
    assert "a@example." not in repr(email_sample)


def test_database_source_adapter_sample_zero_limit_does_not_query_sqlite(
    tmp_path: Path,
) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(connection_uri=uri, source_name="sqlite_fixture")
    adapter = build_database_source_adapter(request)
    table = TableProfile(table_name="customers")
    column = ColumnProfile(column_name="email", ordinal_position=1)

    try:
        sample = adapter.get_column_sample(table, column, limit=0)
    finally:
        adapter.close()

    assert sample.values == ()
    assert sample.sampled_count == 0
    assert sample.non_null_count == 0
    assert not sample.truncated


def test_database_source_adapter_rejects_negative_sample_limit(tmp_path: Path) -> None:
    uri = sqlite_uri(tmp_path)
    create_sqlite_database(uri)
    request = DatabaseScanRequest(connection_uri=uri, source_name="sqlite_fixture")
    adapter = build_database_source_adapter(request)
    table = TableProfile(table_name="customers")
    column = ColumnProfile(column_name="email", ordinal_position=1)

    try:
        with pytest.raises(ValueError, match="limit must be non-negative"):
            adapter.get_column_sample(table, column, limit=-1)
    finally:
        adapter.close()


def test_database_scan_request_normalizes_filters_and_hides_connection_uri() -> None:
    request = DatabaseScanRequest(
        connection_uri="postgresql://user:secret@localhost/db",
        source_name="prod_db",
        dialect="PostgreSQL",
        include_schemas=(" public ", ""),
        exclude_schemas=(" pg_catalog ",),
        include_tables=(" customers ",),
        exclude_tables=(" audit_logs ",),
    )

    assert request.dialect == "postgresql"
    assert request.include_schemas == ("public",)
    assert request.exclude_schemas == ("pg_catalog",)
    assert request.include_tables == ("customers",)
    assert request.exclude_tables == ("audit_logs",)
    assert "secret" not in repr(request)
    assert "postgresql://" not in repr(request)


def test_database_source_adapter_sanitizes_password_in_source_uri() -> None:
    request = DatabaseScanRequest(
        connection_uri="postgresql+psycopg://user:secret@localhost/db",
        source_name="prod_db",
    )
    adapter = build_database_source_adapter(request)

    try:
        assert adapter.dialect == "postgresql"
        assert "secret" not in adapter.source_uri
        assert "postgresql+psycopg://" in adapter.source_uri
    finally:
        adapter.close()


def test_factory_returns_postgresql_adapter_without_connecting() -> None:
    request = DatabaseScanRequest(
        connection_uri="postgresql+psycopg://user:secret@localhost/db",
        source_name="prod_db",
    )
    adapter = build_database_source_adapter(request)

    try:
        assert isinstance(adapter, PostgreSQLDatabaseSourceAdapter)
        assert adapter.dialect == "postgresql"
        assert "secret" not in adapter.source_uri
    finally:
        adapter.close()


def test_postgresql_sample_zero_limit_does_not_query_database() -> None:
    request = DatabaseScanRequest(
        connection_uri="postgresql+psycopg://user:secret@localhost/db",
        source_name="prod_db",
    )
    adapter = build_database_source_adapter(request)
    table = TableProfile(schema_name="public", table_name="customers")
    column = ColumnProfile(column_name="email", ordinal_position=1)

    try:
        sample = adapter.get_column_sample(table, column, limit=0)
    finally:
        adapter.close()

    assert sample.table_name == "customers"
    assert sample.schema_name == "public"
    assert sample.column_name == "email"
    assert sample.values == ()
    assert sample.sampled_count == 0
    assert sample.non_null_count == 0
    assert not sample.truncated


def test_postgresql_sample_rejects_negative_limit_without_querying() -> None:
    request = DatabaseScanRequest(
        connection_uri="postgresql+psycopg://user:secret@localhost/db",
        source_name="prod_db",
    )
    adapter = build_database_source_adapter(request)
    table = TableProfile(schema_name="public", table_name="customers")
    column = ColumnProfile(column_name="email", ordinal_position=1)

    try:
        with pytest.raises(ValueError, match="limit must be non-negative"):
            adapter.get_column_sample(table, column, limit=-1)
    finally:
        adapter.close()


def test_default_schema_filter_excludes_postgresql_system_schemas() -> None:
    class FakeInspector:
        def get_schema_names(self):
            return ("information_schema", "pg_catalog", "pg_toast", "public")

    request = DatabaseScanRequest(
        connection_uri="postgresql+psycopg://user:secret@localhost/db",
        source_name="prod_db",
    )

    from table_extract.sources.database import _schema_names

    assert _schema_names(FakeInspector(), request) == ("public",)


def test_database_connection_error_is_typed_and_sanitized() -> None:
    class FailingInspector:
        def get_schema_names(self):
            raise RuntimeError("could not connect to server password=secret")

    adapter = object.__new__(DatabaseSourceAdapter)
    adapter.request = DatabaseScanRequest(
        connection_uri="postgresql://user:secret@localhost/db",
        source_name="prod_db",
    )
    adapter.source_name = "prod_db"
    adapter._engine = object()
    adapter._inspect = lambda engine: FailingInspector()
    adapter.dialect = "postgresql"
    adapter.source_uri = "postgresql://user:***@localhost/db"
    adapter._tables = None
    adapter._columns_by_table = {}

    with pytest.raises(DatabaseConnectionError) as exc:
        tuple(adapter.iter_tables())

    assert "secret" not in str(exc.value)
    assert exc.value.retryable is True


def test_factory_returns_oracle_adapter_without_connecting(monkeypatch) -> None:
    engine = FakeOracleEngine()
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _uri: engine)
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )

    adapter = build_database_source_adapter(request)

    try:
        assert isinstance(adapter, OracleDatabaseSourceAdapter)
        assert adapter.dialect == "oracle"
        assert "secret" not in adapter.source_uri
        assert "oracle+oracledb://" in adapter.source_uri
        assert "secret" not in repr(request)
    finally:
        adapter.close()

    assert engine.disposed


def test_oracle_adapter_profiles_mocked_catalog_rows() -> None:
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )
    adapter = fake_oracle_adapter(request, oracle_catalog_engine())

    profile = profile_source(adapter)

    assert profile.source_name == "oracle_fixture"
    assert profile.source_type == "database"
    assert profile.dialect == "oracle"
    assert "secret" not in profile.source_uri
    table_names = {
        (table.schema_name, table.table_name, table.table_type)
        for table in profile.tables
    }
    assert table_names == {
        ("APP", "CONTACTS", "table"),
        ("APP", "CONTACT_EMAILS", "view"),
        ("HR", "EMPLOYEES", "table"),
    }

    contacts = next(table for table in profile.tables if table.table_name == "CONTACTS")
    contact_view = next(
        table for table in profile.tables if table.table_name == "CONTACT_EMAILS"
    )
    assert contacts.row_count == 3
    assert contact_view.row_count is None
    assert [
        (column.column_name, column.data_type, column.nullable, column.ordinal_position)
        for column in contacts.columns
    ] == [
        ("CONTACT_ID", "NUMBER(10,0)", False, 1),
        ("EMAIL", "VARCHAR2(100)", True, 2),
    ]
    assert contact_view.columns[0].column_name == "EMAIL"


def test_oracle_permission_error_on_catalog_is_typed_and_sanitized() -> None:
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )
    engine = FakeOracleEngine(
        execute_error=RuntimeError("ORA-01031: insufficient privileges password=secret")
    )
    adapter = fake_oracle_adapter(request, engine)

    with pytest.raises(DatabasePermissionError) as exc:
        profile_source(adapter)

    assert "secret" not in str(exc.value)
    assert exc.value.retryable is False
    assert exc.value.category == "database_permission"
    assert exc.value.safe_context["operation"] == "oracle_all_tables"


def test_oracle_adapter_respects_case_insensitive_filters_and_view_toggle() -> None:
    engine = oracle_catalog_engine()
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
        include_schemas=("app", "hr"),
        exclude_schemas=("hr",),
        include_tables=("contacts", "contact_emails"),
        exclude_tables=("contact_emails",),
    )
    adapter = fake_oracle_adapter(request, engine)

    profile = profile_source(adapter)

    assert [(table.schema_name, table.table_name) for table in profile.tables] == [
        ("APP", "CONTACTS"),
    ]

    no_views_request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
        include_schemas=("app",),
        include_views=False,
    )
    no_views_adapter = fake_oracle_adapter(no_views_request, oracle_catalog_engine())

    no_views_profile = profile_source(no_views_adapter)

    assert [
        (table.schema_name, table.table_name, table.table_type)
        for table in no_views_profile.tables
    ] == [("APP", "CONTACTS", "table")]


def test_oracle_sample_zero_limit_does_not_query_database() -> None:
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )
    engine = FakeOracleEngine(sample_rows=(("a@example.com",),))
    adapter = fake_oracle_adapter(request, engine)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    sample = adapter.get_column_sample(table, column, limit=0)

    assert sample.values == ()
    assert sample.sampled_count == 0
    assert sample.non_null_count == 0
    assert not sample.truncated
    assert engine.executed == []


def test_oracle_sample_rejects_negative_limit_without_querying() -> None:
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )
    engine = FakeOracleEngine(sample_rows=(("a@example.com",),))
    adapter = fake_oracle_adapter(request, engine)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    with pytest.raises(ValueError, match="limit must be non-negative"):
        adapter.get_column_sample(table, column, limit=-1)

    assert engine.executed == []


def test_oracle_sample_mocked_values_truncates_and_hides_repr() -> None:
    request = DatabaseScanRequest(
        connection_uri=oracle_uri(),
        source_name="oracle_fixture",
    )
    engine = FakeOracleEngine(
        sample_rows=((None,), ("   ",), ("abcdef",), (42,), ("xy",)),
    )
    adapter = fake_oracle_adapter(request, engine)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    sample = adapter.get_column_sample(
        table,
        column,
        limit=4,
        max_value_length=4,
    )

    assert sample.values == ("abcd", "42", "xy")
    assert sample.sampled_count == 3
    assert sample.non_null_count == 3
    assert sample.truncated
    assert "abcdef" not in repr(sample)
    assert "abcd" not in repr(sample)
    assert 'SELECT "EMAIL" FROM "APP"."CONTACTS"' in engine.executed[0][0]
    assert "FETCH FIRST 4 ROWS ONLY" in engine.executed[0][0]


@pytest.mark.skipif(
    not os.environ.get("TABLE_EXTRACT_POSTGRES_TEST_URL"),
    reason="TABLE_EXTRACT_POSTGRES_TEST_URL is not set",
)
def test_postgresql_adapter_profiles_and_samples_real_database() -> None:
    url = os.environ["TABLE_EXTRACT_POSTGRES_TEST_URL"]
    schema_name = "table_extract_pg_test"
    engine = sqlalchemy.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        connection.execute(sqlalchemy.text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            sqlalchemy.text(
                f"""
                CREATE TABLE "{schema_name}"."contacts" (
                    contact_id integer PRIMARY KEY,
                    email text NOT NULL,
                    score integer NULL,
                    notes text NULL
                )
                """
            )
        )
        connection.execute(
            sqlalchemy.text(
                f"""
                INSERT INTO "{schema_name}"."contacts" (contact_id, email, score, notes)
                VALUES
                    (1, '  a@example.com  ', 10, 'short'),
                    (2, 'b@example.com', 20, NULL),
                    (3, 'long@example.com', NULL, 'this value is long')
                """
            )
        )
        connection.execute(
            sqlalchemy.text(
                f"""
                CREATE VIEW "{schema_name}"."contact_emails" AS
                SELECT contact_id, email FROM "{schema_name}"."contacts"
                """
            )
        )
        connection.execute(sqlalchemy.text(f'ANALYZE "{schema_name}"."contacts"'))

    request = DatabaseScanRequest(
        connection_uri=url,
        source_name="postgres_fixture",
        include_schemas=(schema_name,),
    )
    adapter = build_database_source_adapter(request)

    try:
        profile = profile_source(adapter)
        table_names = {(table.table_name, table.table_type) for table in profile.tables}
        assert table_names == {
            ("contacts", "table"),
            ("contact_emails", "view"),
        }
        contacts = next(table for table in profile.tables if table.table_name == "contacts")
        contact_emails = next(
            table for table in profile.tables if table.table_name == "contact_emails"
        )
        assert contacts.schema_name == schema_name
        assert contacts.row_count is not None
        assert contacts.row_count >= 0
        assert contact_emails.row_count is None
        assert [(column.column_name, column.ordinal_position) for column in contacts.columns] == [
            ("contact_id", 1),
            ("email", 2),
            ("score", 3),
            ("notes", 4),
        ]
        assert contacts.columns[1].nullable is False
        assert contacts.columns[2].nullable is True

        email_sample = adapter.get_column_sample(
            contacts,
            contacts.columns[1],
            limit=2,
            max_value_length=10,
        )
        score_sample = adapter.get_column_sample(
            contacts,
            contacts.columns[2],
            limit=3,
        )
        assert email_sample.values == ("a@example.", "b@example.")
        assert email_sample.truncated
        assert score_sample.values == ("10", "20")
    finally:
        adapter.close()
        with engine.begin() as connection:
            connection.execute(sqlalchemy.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        engine.dispose()


def drop_oracle_object(connection, object_type: str, object_name: str) -> None:
    drop_statement = f'DROP {object_type} "{object_name}"'
    if object_type == "TABLE":
        drop_statement = f"{drop_statement} PURGE"
    connection.execute(
        sqlalchemy.text(
            f"""
            BEGIN
                EXECUTE IMMEDIATE '{drop_statement}';
            EXCEPTION
                WHEN OTHERS THEN
                    IF SQLCODE != -942 THEN
                        RAISE;
                    END IF;
            END;
            """
        )
    )


@pytest.mark.skipif(
    not os.environ.get("TABLE_EXTRACT_ORACLE_TEST_URL"),
    reason="TABLE_EXTRACT_ORACLE_TEST_URL is not set",
)
def test_oracle_adapter_profiles_and_samples_real_database() -> None:
    url = os.environ["TABLE_EXTRACT_ORACLE_TEST_URL"]
    table_name = "TABLE_EXTRACT_ORA_CONTACTS"
    view_name = "TABLE_EXTRACT_ORA_EMAILS"
    engine = sqlalchemy.create_engine(url)

    with engine.begin() as connection:
        owner = connection.execute(sqlalchemy.text("SELECT USER FROM dual")).scalar_one()
        drop_oracle_object(connection, "VIEW", view_name)
        drop_oracle_object(connection, "TABLE", table_name)
        connection.execute(
            sqlalchemy.text(
                f"""
                CREATE TABLE "{table_name}" (
                    contact_id NUMBER(10) PRIMARY KEY,
                    email VARCHAR2(100) NOT NULL,
                    score NUMBER(10) NULL,
                    notes VARCHAR2(100) NULL
                )
                """
            )
        )
        connection.execute(
            sqlalchemy.text(
                f"""
                INSERT INTO "{table_name}" (contact_id, email, score, notes)
                VALUES (:contact_id, :email, :score, :notes)
                """
            ),
            (
                {
                    "contact_id": 1,
                    "email": "  a@example.com  ",
                    "score": 10,
                    "notes": "short",
                },
                {
                    "contact_id": 2,
                    "email": "b@example.com",
                    "score": 20,
                    "notes": None,
                },
                {
                    "contact_id": 3,
                    "email": "long@example.com",
                    "score": None,
                    "notes": "this value is long",
                },
                {
                    "contact_id": 4,
                    "email": "   ",
                    "score": 30,
                    "notes": "blank email",
                },
            ),
        )
        connection.execute(
            sqlalchemy.text(
                f"""
                CREATE VIEW "{view_name}" AS
                SELECT contact_id, email FROM "{table_name}"
                """
            )
        )
        connection.execute(
            sqlalchemy.text(
                """
                BEGIN
                    DBMS_STATS.GATHER_TABLE_STATS(USER, :table_name);
                EXCEPTION
                    WHEN OTHERS THEN
                        NULL;
                END;
                """
            ),
            {"table_name": table_name},
        )

    request = DatabaseScanRequest(
        connection_uri=url,
        source_name="oracle_fixture",
        include_schemas=(owner,),
        include_tables=(table_name, view_name),
    )
    adapter = build_database_source_adapter(request)

    try:
        profile = profile_source(adapter)
        table_names = {(table.table_name, table.table_type) for table in profile.tables}
        assert table_names == {
            (table_name, "table"),
            (view_name, "view"),
        }
        contacts = next(table for table in profile.tables if table.table_name == table_name)
        contact_emails = next(
            table for table in profile.tables if table.table_name == view_name
        )
        assert contacts.schema_name == owner
        assert contacts.row_count is None or contacts.row_count >= 0
        assert contact_emails.row_count is None
        assert [(column.column_name, column.ordinal_position) for column in contacts.columns] == [
            ("CONTACT_ID", 1),
            ("EMAIL", 2),
            ("SCORE", 3),
            ("NOTES", 4),
        ]
        assert contacts.columns[1].nullable is False
        assert contacts.columns[2].nullable is True

        email_sample = adapter.get_column_sample(
            contacts,
            contacts.columns[1],
            limit=4,
            max_value_length=10,
        )
        score_sample = adapter.get_column_sample(
            contacts,
            contacts.columns[2],
            limit=4,
        )
        assert set(email_sample.values) == {
            "a@example.",
            "b@example.",
            "long@examp",
        }
        assert email_sample.truncated
        assert set(score_sample.values) == {"10", "20", "30"}
    finally:
        adapter.close()
        with engine.begin() as connection:
            drop_oracle_object(connection, "VIEW", view_name)
            drop_oracle_object(connection, "TABLE", table_name)
        engine.dispose()
