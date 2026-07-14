from __future__ import annotations

from dataclasses import replace
import os

import pytest
import requests

from table_extract.models import ColumnProfile, TableProfile
from table_extract.operational import classify_operational_exception
from table_extract.profiling import profile_source
from table_extract.sources import (
    OrdsAuthError,
    OrdsError,
    OrdsHttpError,
    OrdsResponseError,
    OrdsScanRequest,
    OrdsSourceAdapter,
    OrdsTimeoutError,
    SourceAdapter,
    build_ords_source_adapter,
)


def ords_url() -> str:
    return "https://user:secret@example.com/ords/app/_/sql?access_token=abc"


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, json_error: bool = False) -> None:
        self._payload = payload
        self.status_code = status_code
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls = []
        self.closed = False

    def post(self, url, *, json, headers, timeout, auth):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "auth": auth,
            }
        )
        return self.handler(json)

    def close(self) -> None:
        self.closed = True


def ords_payload(
    *,
    metadata,
    rows,
    has_more: bool = False,
    offset: int = 0,
    limit: int = 500,
):
    return {
        "items": [
            {
                "statementType": "query",
                "resultSet": {
                    "metadata": metadata,
                    "items": rows,
                    "hasMore": has_more,
                    "count": len(rows),
                    "offset": offset,
                    "limit": limit,
                },
            }
        ]
    }


def metadata_for(*columns: str):
    return [
        {
            "columnName": column,
            "jsonColumnName": column.lower(),
            "columnTypeName": "VARCHAR2",
            "isNullable": 1,
        }
        for column in columns
    ]


def paginated_response(metadata, rows):
    def respond(request_json):
        offset = request_json["offset"]
        limit = request_json["limit"]
        page = rows[offset : offset + limit]
        return FakeResponse(
            ords_payload(
                metadata=metadata,
                rows=page,
                has_more=offset + limit < len(rows),
                offset=offset,
                limit=limit,
            )
        )

    return respond


def catalog_session():
    table_rows = [
        {"owner": "SYS", "table_name": "SYSTEM_TABLE", "num_rows": 1},
        {"owner": "APP", "table_name": "CONTACTS", "num_rows": 3},
        {"owner": "HR", "table_name": "EMPLOYEES", "num_rows": None},
    ]
    view_rows = [
        {"owner": "APP", "view_name": "CONTACT_EMAILS"},
        {"owner": "SYSTEM", "view_name": "SYSTEM_VIEW"},
    ]
    column_rows = [
        {
            "owner": "APP",
            "table_name": "CONTACTS",
            "column_name": "CONTACT_ID",
            "data_type": "NUMBER",
            "data_length": 22,
            "char_length": None,
            "data_precision": 10,
            "data_scale": 0,
            "nullable": "N",
            "column_id": 1,
        },
        {
            "owner": "APP",
            "table_name": "CONTACTS",
            "column_name": "EMAIL",
            "data_type": "VARCHAR2",
            "data_length": 100,
            "char_length": 100,
            "data_precision": None,
            "data_scale": None,
            "nullable": "Y",
            "column_id": 2,
        },
        {
            "owner": "APP",
            "table_name": "CONTACT_EMAILS",
            "column_name": "EMAIL",
            "data_type": "VARCHAR2",
            "data_length": 100,
            "char_length": 100,
            "data_precision": None,
            "data_scale": None,
            "nullable": "Y",
            "column_id": 1,
        },
        {
            "owner": "HR",
            "table_name": "EMPLOYEES",
            "column_name": "EMPLOYEE_ID",
            "data_type": "NUMBER",
            "data_length": 22,
            "char_length": None,
            "data_precision": None,
            "data_scale": None,
            "nullable": "N",
            "column_id": 1,
        },
    ]

    def handler(request_json):
        statement = request_json["statementText"].lower()
        if "from all_tables" in statement:
            return paginated_response(
                metadata_for("OWNER", "TABLE_NAME", "NUM_ROWS"),
                table_rows,
            )(request_json)
        if "from all_views" in statement:
            return paginated_response(
                metadata_for("OWNER", "VIEW_NAME"),
                view_rows,
            )(request_json)
        if "from all_tab_columns" in statement:
            return paginated_response(
                metadata_for(
                    "OWNER",
                    "TABLE_NAME",
                    "COLUMN_NAME",
                    "DATA_TYPE",
                    "DATA_LENGTH",
                    "CHAR_LENGTH",
                    "DATA_PRECISION",
                    "DATA_SCALE",
                    "NULLABLE",
                    "COLUMN_ID",
                ),
                column_rows,
            )(request_json)
        raise AssertionError(f"unexpected SQL: {statement}")

    return FakeSession(handler)


def test_ords_scan_request_hides_secrets_and_sanitizes_url() -> None:
    request = OrdsScanRequest(
        rest_sql_url=ords_url(),
        auth_mode="bearer",
        bearer_token="token-secret",
    )
    session = FakeSession(
        lambda request_json: FakeResponse(
            ords_payload(metadata=metadata_for("DUMMY"), rows=[])
        )
    )
    adapter = OrdsSourceAdapter(request, session=session)

    try:
        assert "secret" not in repr(request)
        assert "token-secret" not in repr(request)
        assert "access_token=abc" not in adapter.source_uri
        assert "secret" not in adapter.source_uri
        assert adapter.source_uri.startswith("https://example.com/ords/app/_/sql")
    finally:
        adapter.close()

    assert session.closed


def test_ords_basic_and_bearer_auth_are_applied_without_repr_leaks() -> None:
    response = FakeResponse(ords_payload(metadata=metadata_for("DUMMY"), rows=[]))
    basic_session = FakeSession(lambda request_json: response)
    basic_request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        auth_mode="basic",
        username="APP",
        password="app-secret",
    )
    basic_adapter = OrdsSourceAdapter(basic_request, session=basic_session)

    basic_adapter._post_sql("select 1 from dual", limit=1, offset=0)

    assert basic_session.calls[0]["auth"] == ("APP", "app-secret")
    assert "Authorization" not in basic_session.calls[0]["headers"]
    assert "app-secret" not in repr(basic_request)

    bearer_session = FakeSession(lambda request_json: response)
    bearer_request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        auth_mode="bearer",
        bearer_token="bearer-secret",
    )
    bearer_adapter = OrdsSourceAdapter(bearer_request, session=bearer_session)

    bearer_adapter._post_sql("select 1 from dual", limit=1, offset=0)

    assert bearer_session.calls[0]["auth"] is None
    assert bearer_session.calls[0]["headers"]["Authorization"] == "Bearer bearer-secret"
    assert "bearer-secret" not in repr(bearer_request)


def test_ords_adapter_profiles_paginated_metadata() -> None:
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        source_name="ords_fixture",
        page_size=1,
    )
    adapter = OrdsSourceAdapter(request, session=catalog_session())

    assert isinstance(adapter, SourceAdapter)

    profile = profile_source(adapter)

    assert profile.source_name == "ords_fixture"
    assert profile.source_type == "ords"
    assert profile.dialect == "oracle"
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


def test_ords_adapter_respects_filters_and_view_toggle() -> None:
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        include_schemas=("app", "hr"),
        exclude_schemas=("hr",),
        include_tables=("contacts", "contact_emails"),
        exclude_tables=("contact_emails",),
    )
    adapter = OrdsSourceAdapter(request, session=catalog_session())

    profile = profile_source(adapter)

    assert [(table.schema_name, table.table_name) for table in profile.tables] == [
        ("APP", "CONTACTS"),
    ]

    no_views_request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        include_schemas=("app",),
        include_views=False,
    )
    no_views_adapter = OrdsSourceAdapter(no_views_request, session=catalog_session())

    no_views_profile = profile_source(no_views_adapter)

    assert [
        (table.schema_name, table.table_name, table.table_type)
        for table in no_views_profile.tables
    ] == [("APP", "CONTACTS", "table")]


def test_ords_sample_zero_limit_does_not_make_http_request() -> None:
    session = FakeSession(lambda request_json: pytest.fail("unexpected HTTP request"))
    request = OrdsScanRequest(rest_sql_url="https://example.com/ords/app/_/sql")
    adapter = OrdsSourceAdapter(request, session=session)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    sample = adapter.get_column_sample(table, column, limit=0)

    assert sample.values == ()
    assert sample.sampled_count == 0
    assert sample.non_null_count == 0
    assert not sample.truncated
    assert session.calls == []


def test_ords_sample_rejects_negative_limit_without_http_request() -> None:
    session = FakeSession(lambda request_json: pytest.fail("unexpected HTTP request"))
    request = OrdsScanRequest(rest_sql_url="https://example.com/ords/app/_/sql")
    adapter = OrdsSourceAdapter(request, session=session)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    with pytest.raises(ValueError, match="limit must be non-negative"):
        adapter.get_column_sample(table, column, limit=-1)

    assert session.calls == []


def test_ords_sample_truncates_skips_empty_values_and_hides_repr() -> None:
    rows = [
        {"email": None},
        {"email": "   "},
        {"email": "abcdef"},
        {"email": 42},
        {"email": "xy"},
    ]
    session = FakeSession(
        paginated_response(metadata_for("EMAIL"), rows)
    )
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        page_size=2,
    )
    adapter = OrdsSourceAdapter(request, session=session)
    table = TableProfile(schema_name="APP", table_name="CONTACTS")
    column = ColumnProfile(column_name="EMAIL", ordinal_position=1)

    sample = adapter.get_column_sample(
        table,
        column,
        limit=5,
        max_value_length=4,
    )

    assert sample.values == ("abcd", "42", "xy")
    assert sample.sampled_count == 3
    assert sample.non_null_count == 3
    assert sample.truncated
    assert "abcdef" not in repr(sample)
    assert "abcd" not in repr(sample)
    assert 'SELECT "EMAIL" FROM "APP"."CONTACTS"' in session.calls[0]["json"]["statementText"]


def test_ords_timeout_raises_clear_error() -> None:
    def raise_timeout(request_json):
        raise requests.Timeout()

    request = OrdsScanRequest(rest_sql_url="https://example.com/ords/app/_/sql")
    adapter = OrdsSourceAdapter(request, session=FakeSession(raise_timeout))

    with pytest.raises(OrdsTimeoutError, match="timed out") as exc_info:
        adapter._execute_query("select 1 from dual")

    assert classify_operational_exception(exc_info.value).retryable is True


@pytest.mark.parametrize(
    ("status_code", "error_type", "retryable"),
    [
        (401, OrdsAuthError, False),
        (403, OrdsAuthError, False),
        (500, OrdsHttpError, True),
    ],
)
def test_ords_http_errors_are_typed(status_code, error_type, retryable) -> None:
    session = FakeSession(lambda request_json: FakeResponse({}, status_code=status_code))
    request = OrdsScanRequest(rest_sql_url=ords_url())
    adapter = OrdsSourceAdapter(request, session=session)

    with pytest.raises(error_type) as exc_info:
        adapter._execute_query("select 1 from dual")

    assert "secret" not in str(exc_info.value)
    assert "access_token=abc" not in str(exc_info.value)
    info = classify_operational_exception(exc_info.value)
    assert info.retryable is retryable
    assert info.safe_context["status_code"] == status_code


def test_ords_invalid_json_raises_response_error() -> None:
    session = FakeSession(
        lambda request_json: FakeResponse(None, json_error=True)
    )
    request = OrdsScanRequest(rest_sql_url="https://example.com/ords/app/_/sql")
    adapter = OrdsSourceAdapter(request, session=session)

    with pytest.raises(OrdsResponseError, match="not valid JSON") as exc_info:
        adapter._execute_query("select 1 from dual")

    assert classify_operational_exception(exc_info.value).retryable is False


def test_ords_missing_result_set_raises_response_error() -> None:
    session = FakeSession(lambda request_json: FakeResponse({"items": [{}]}))
    request = OrdsScanRequest(rest_sql_url="https://example.com/ords/app/_/sql")
    adapter = OrdsSourceAdapter(request, session=session)

    with pytest.raises(OrdsResponseError, match="resultSet") as exc_info:
        adapter._execute_query("select 1 from dual")

    assert classify_operational_exception(exc_info.value).retryable is False


def test_ords_pagination_max_pages_raises_response_error() -> None:
    payload = ords_payload(
        metadata=metadata_for("DUMMY"),
        rows=[{"dummy": 1}],
        has_more=True,
        offset=0,
        limit=1,
    )
    session = FakeSession(lambda request_json: FakeResponse(payload))
    request = OrdsScanRequest(
        rest_sql_url="https://example.com/ords/app/_/sql",
        page_size=1,
        max_pages=1,
    )
    adapter = OrdsSourceAdapter(request, session=session)

    with pytest.raises(OrdsResponseError, match="max_pages") as exc_info:
        adapter._execute_query("select 1 from dual")

    assert classify_operational_exception(exc_info.value).retryable is False


def ords_request_from_env() -> OrdsScanRequest:
    auth_mode = os.environ.get("TABLE_EXTRACT_ORDS_AUTH_MODE", "none")
    return OrdsScanRequest(
        rest_sql_url=os.environ["TABLE_EXTRACT_ORDS_TEST_URL"],
        source_name="ords_fixture",
        auth_mode=auth_mode,
        username=os.environ.get("TABLE_EXTRACT_ORDS_USERNAME"),
        password=os.environ.get("TABLE_EXTRACT_ORDS_PASSWORD"),
        bearer_token=os.environ.get("TABLE_EXTRACT_ORDS_BEARER_TOKEN"),
    )


def try_ords_statement(adapter: OrdsSourceAdapter, statement: str) -> bool:
    try:
        adapter._post_sql(statement, limit=1, offset=0)
    except OrdsError:
        return False
    return True


@pytest.mark.skipif(
    not os.environ.get("TABLE_EXTRACT_ORDS_TEST_URL"),
    reason="TABLE_EXTRACT_ORDS_TEST_URL is not set",
)
def test_ords_adapter_profiles_and_samples_real_rest_enabled_sql() -> None:
    setup_request = ords_request_from_env()
    setup_adapter = build_ords_source_adapter(setup_request)
    table_name = "TABLE_EXTRACT_ORDS_CONTACTS"
    view_name = "TABLE_EXTRACT_ORDS_EMAILS"

    try:
        owner_result = setup_adapter._execute_query(
            "SELECT USER AS owner FROM dual",
            max_rows=1,
        )
        owner = str(owner_result.rows[0]["owner"])

        try_ords_statement(setup_adapter, f'DROP VIEW "{view_name}"')
        try_ords_statement(setup_adapter, f'DROP TABLE "{table_name}" PURGE')

        created = try_ords_statement(
            setup_adapter,
            f"""
            CREATE TABLE "{table_name}" (
                contact_id NUMBER(10) PRIMARY KEY,
                email VARCHAR2(100) NOT NULL,
                score NUMBER(10) NULL,
                notes VARCHAR2(100) NULL
            )
            """,
        )
        if not created:
            pytest.skip("Could not create synthetic ORDS test table")

        for statement in (
            f"""INSERT INTO "{table_name}" (contact_id, email, score, notes)
                VALUES (1, '  a@example.com  ', 10, 'short')""",
            f"""INSERT INTO "{table_name}" (contact_id, email, score, notes)
                VALUES (2, 'b@example.com', 20, NULL)""",
            f"""INSERT INTO "{table_name}" (contact_id, email, score, notes)
                VALUES (3, 'long@example.com', NULL, 'this value is long')""",
            f"""INSERT INTO "{table_name}" (contact_id, email, score, notes)
                VALUES (4, '   ', 30, 'blank email')""",
        ):
            if not try_ords_statement(setup_adapter, statement):
                pytest.skip("Could not insert synthetic ORDS test rows")

        if not try_ords_statement(
            setup_adapter,
            f"""
            CREATE VIEW "{view_name}" AS
            SELECT contact_id, email FROM "{table_name}"
            """,
        ):
            pytest.skip("Could not create synthetic ORDS test view")

        request = replace(
            setup_request,
            include_schemas=(owner,),
            include_tables=(table_name, view_name),
        )
        adapter = build_ords_source_adapter(request)
        try:
            profile = profile_source(adapter)
            table_names = {(table.table_name, table.table_type) for table in profile.tables}
            assert table_names == {
                (table_name, "table"),
                (view_name, "view"),
            }
            contacts = next(
                table for table in profile.tables if table.table_name == table_name
            )
            assert [(column.column_name, column.ordinal_position) for column in contacts.columns] == [
                ("CONTACT_ID", 1),
                ("EMAIL", 2),
                ("SCORE", 3),
                ("NOTES", 4),
            ]
            assert contacts.columns[1].nullable is False

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
    finally:
        try_ords_statement(setup_adapter, f'DROP VIEW "{view_name}"')
        try_ords_statement(setup_adapter, f'DROP TABLE "{table_name}" PURGE')
        setup_adapter.close()
