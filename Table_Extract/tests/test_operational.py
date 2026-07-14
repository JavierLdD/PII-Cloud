from __future__ import annotations

import json

from table_extract.operational import (
    OperationalErrorInfo,
    classify_operational_exception,
    emit_operational_log,
)


def test_operational_info_sanitizes_messages_and_context() -> None:
    info = classify_operational_exception(
        RuntimeError(
            "failed postgresql://user:secret@localhost/db?access_token=abc "
            "Bearer bearer-secret"
        ),
        default_component="database",
        safe_context={
            "database_url": "postgresql://user:secret@localhost/db",
            "source_uri": "https://example.com/ords/app/_/sql?access_token=abc",
            "plain": "ok",
        },
    )

    rendered = json.dumps(
        {
            "message": info.message,
            "context": dict(info.safe_context),
        },
        sort_keys=True,
    )
    assert info.component == "database"
    assert info.retryable is True
    assert "secret" not in rendered
    assert "bearer-secret" not in rendered
    assert "access_token=abc" not in rendered
    assert info.safe_context["database_url"] == "***"
    assert info.safe_context["plain"] == "ok"


def test_emit_operational_log_writes_jsonl_to_stderr(capsys) -> None:
    emit_operational_log(
        "profile_failed",
        OperationalErrorInfo(
            component="ords",
            category="ords_timeout",
            retryable=True,
            message="ORDS timed out",
            safe_context={"source_name": "ords_cli"},
        ),
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert captured.out == ""
    assert payload["event"] == "profile_failed"
    assert payload["component"] == "ords"
    assert payload["category"] == "ords_timeout"
    assert payload["retryable"] is True
    assert payload["safe_context"]["source_name"] == "ords_cli"
