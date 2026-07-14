from __future__ import annotations

import json

import pytest

import main as table_extract_cli
from table_extract.models import (
    ColumnProfile,
    DataSourceProfile,
    DiscoveredPII,
    DiscoveryResult,
    TableProfile,
)
from table_extract.orchestration import (
    DatabaseProfileRequest,
    FileProfileRequest,
    OrdsProfileRequest,
)
from table_extract.runtime import FileScanContext, StoredFile


def fake_profile(
    *,
    source_name: str,
    source_type: str,
    dialect: str | None,
) -> DataSourceProfile:
    return DataSourceProfile(
        source_name=source_name,
        source_type=source_type,
        dialect=dialect,
        source_uri="safe://source",
        tables=(
            TableProfile(
                schema_name="APP",
                table_name="CONTACTS",
                columns=(
                    ColumnProfile(column_name="ID", data_type="NUMBER", ordinal_position=1),
                    ColumnProfile(
                        column_name="EMAIL",
                        data_type="VARCHAR2",
                        ordinal_position=2,
                    ),
                ),
            ),
            TableProfile(
                schema_name="APP",
                table_name="CONTACT_EMAILS",
                table_type="view",
                columns=(ColumnProfile(column_name="EMAIL", ordinal_position=1),),
            ),
        ),
    )


def fake_discovery_result(
    *,
    source_name: str,
    source_type: str,
    dialect: str | None,
    run_id: str,
) -> DiscoveryResult:
    profile = fake_profile(
        source_name=source_name,
        source_type=source_type,
        dialect=dialect,
    )
    return DiscoveryResult(
        run_id=run_id,
        profile=profile,
        findings=(
            DiscoveredPII(
                source_name=source_name,
                source_type=source_type,
                schema_name="APP",
                table_name="CONTACTS",
                column_name="EMAIL",
                pii_type="EMAIL",
                confidence=0.95,
                confidence_level="VERY_CONFIDENT",
                detection_method="regex",
                sampled_count=2,
                matched_count=2,
                evidence_summary="method=regex sampled=2 matched=2",
            ),
        ),
    )


def fake_file_context(
    *,
    file_id: str = "file-001",
    run_id: str = "run-001",
    is_temporary: bool = True,
) -> FileScanContext:
    stored_file = StoredFile(
        file_id=file_id,
        run_id=run_id,
        source_type="local",
        source_uri=f"local://{file_id}",
        external_id=None,
        file_name="contacts.csv",
        relative_path="contacts.csv",
        extension=".csv",
        mime_type="text/csv",
        size_bytes=None,
        checksum_sha256=None,
    )
    return FileScanContext(
        run_id=run_id,
        stored_file=stored_file,
        local_path="/tmp/contacts.csv",
        source_uri=stored_file.source_uri,
        is_temporary=is_temporary,
    )


def disable_operational_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.setattr(
        table_extract_cli,
        "require_env",
        lambda *names: pytest.fail(f"unexpected require_env call: {names}"),
    )


def test_database_url_discovers_source_to_stdout_via_orchestrator(
    monkeypatch,
    capsys,
) -> None:
    disable_operational_dependencies(monkeypatch)
    captured = {}

    def fake_discover_table_source(request, *, run_id=None, config=None):
        captured["request"] = request
        captured["run_id"] = run_id
        captured["config"] = config
        assert isinstance(request, DatabaseProfileRequest)
        return fake_discovery_result(
            source_name=request.request.source_name,
            source_type="database",
            dialect=request.request.dialect or "sqlite",
            run_id=run_id,
        )

    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fake_discover_table_source,
    )

    result = table_extract_cli.main(
        [
            "--database-url",
            "sqlite:///source.db",
            "--database-dialect",
            "SQLite",
            "--source-name",
            "db_cli",
            "--include-schema",
            "main",
            "--exclude-schema",
            "sys",
            "--include-table",
            "contacts",
            "--exclude-table",
            "audit",
            "--exclude-views",
            "--run-id",
            "run-cli",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    request = captured["request"].request
    assert result == 0
    assert captured["run_id"] == "run-cli"
    assert captured["config"].zero_shot_enabled is True
    assert request.connection_uri == "sqlite:///source.db"
    assert request.source_name == "db_cli"
    assert request.dialect == "sqlite"
    assert request.include_schemas == ("main",)
    assert request.exclude_schemas == ("sys",)
    assert request.include_tables == ("contacts",)
    assert request.exclude_tables == ("audit",)
    assert request.include_views is False
    assert output["artifact_type"] == "table_extract.discovery"
    assert output["schema_version"] == "1.0"
    assert output["run_id"] == "run-cli"
    assert output["summary"]["source_name"] == "db_cli"
    assert output["summary"]["source_type"] == "database"
    assert output["summary"]["table_count"] == 1
    assert output["summary"]["view_count"] == 1
    assert output["summary"]["finding_count"] == 1
    assert output["summary"]["findings_by_pii_type"] == {"EMAIL": 1}
    assert output["profile"]["tables"][0]["columns"][1]["column_name"] == "EMAIL"
    assert output["findings"][0]["confidence_level"] == "VERY_CONFIDENT"


def test_database_url_profile_only_preserves_profile_artifact(
    monkeypatch,
    capsys,
) -> None:
    disable_operational_dependencies(monkeypatch)
    captured = {}

    def fake_profile_table_source(request):
        captured["request"] = request
        assert isinstance(request, DatabaseProfileRequest)
        return fake_profile(
            source_name=request.request.source_name,
            source_type="database",
            dialect=request.request.dialect or "sqlite",
        )

    monkeypatch.setattr(
        table_extract_cli,
        "profile_table_source",
        fake_profile_table_source,
    )

    result = table_extract_cli.main(
        [
            "--database-url",
            "sqlite:///source.db",
            "--source-name",
            "db_cli",
            "--profile-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert captured["request"].request.source_name == "db_cli"
    assert output["artifact_type"] == "table_extract.profile"
    assert "findings" not in output
    assert output["profile"]["source_name"] == "db_cli"


def test_ords_url_discovers_source_with_env_auth_fallbacks(monkeypatch, capsys) -> None:
    disable_operational_dependencies(monkeypatch)
    monkeypatch.setenv("TABLE_EXTRACT_ORDS_AUTH_MODE", "bearer")
    monkeypatch.setenv("TABLE_EXTRACT_ORDS_USERNAME", "ignored-user")
    monkeypatch.setenv("TABLE_EXTRACT_ORDS_PASSWORD", "ignored-password")
    monkeypatch.setenv("TABLE_EXTRACT_ORDS_BEARER_TOKEN", "env-token")
    captured = {}

    def fake_discover_table_source(request, *, run_id=None, config=None):
        captured["request"] = request
        captured["run_id"] = run_id
        captured["config"] = config
        assert isinstance(request, OrdsProfileRequest)
        return fake_discovery_result(
            source_name=request.request.source_name,
            source_type="ords",
            dialect="oracle",
            run_id=run_id,
        )

    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fake_discover_table_source,
    )

    result = table_extract_cli.main(
        [
            "--ords-url",
            "https://example.com/ords/app/_/sql",
            "--source-name",
            "ords_cli",
            "--include-schema",
            "app",
            "--include-table",
            "contacts",
            "--ords-timeout-seconds",
            "9.5",
            "--ords-page-size",
            "25",
            "--ords-max-pages",
            "7",
            "--disable-zero-shot",
            "--zero-shot-model-name",
            "/models/xnli",
            "--run-id",
            "ords-run",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    request = captured["request"].request
    assert result == 0
    assert captured["run_id"] == "ords-run"
    assert captured["config"].zero_shot_enabled is False
    assert captured["config"].zero_shot_model_name == "/models/xnli"
    assert request.rest_sql_url == "https://example.com/ords/app/_/sql"
    assert request.source_name == "ords_cli"
    assert request.auth_mode == "bearer"
    assert request.bearer_token == "env-token"
    assert request.timeout_seconds == 9.5
    assert request.page_size == 25
    assert request.max_pages == 7
    assert request.include_schemas == ("app",)
    assert request.include_tables == ("contacts",)
    assert output["artifact_type"] == "table_extract.discovery"
    assert output["summary"]["source_type"] == "ords"
    assert output["summary"]["dialect"] == "oracle"
    assert output["profile"]["source_type"] == "ords"
    assert output["profile"]["dialect"] == "oracle"


def test_output_path_writes_discovery_json_without_stdout(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    disable_operational_dependencies(monkeypatch)
    output_path = tmp_path / "nested" / "discovery.json"

    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        lambda request, **kwargs: fake_discovery_result(
            source_name=request.request.source_name,
            source_type="database",
            dialect="sqlite",
            run_id=kwargs["run_id"],
        ),
    )

    result = table_extract_cli.main(
        [
            "--database-url",
            "sqlite:///source.db",
            "--source-name",
            "db_cli",
            "--run-id",
            "run-output",
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["artifact_type"] == "table_extract.discovery"
    assert output["run_id"] == "run-output"
    assert output["profile"]["source_name"] == "db_cli"


def test_dev_mode_for_direct_sources_prints_discovery_summary_and_pretty_json(
    monkeypatch,
    capsys,
) -> None:
    disable_operational_dependencies(monkeypatch)
    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        lambda request, **kwargs: fake_discovery_result(
            source_name=request.request.source_name,
            source_type="database",
            dialect="sqlite",
            run_id=kwargs["run_id"],
        ),
    )

    result = table_extract_cli.main(
        [
            "--database-url",
            "sqlite:///source.db",
            "--source-name",
            "db_cli",
            "--run-id",
            "run-dev",
            "--dev-mode",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Discovery summary:" in captured.err
    assert "run_id=run-dev" in captured.err
    assert "source_name=db_cli" in captured.err
    assert "findings=1" in captured.err
    assert '\n  "artifact_type": "table_extract.discovery"' in captured.out
    assert '\n    "source_name": "db_cli"' in captured.out
    assert json.loads(captured.out)["profile"]["source_name"] == "db_cli"


def test_dev_mode_profile_only_keeps_profile_summary(monkeypatch, capsys) -> None:
    disable_operational_dependencies(monkeypatch)
    monkeypatch.setattr(
        table_extract_cli,
        "profile_table_source",
        lambda request: fake_profile(
            source_name=request.request.source_name,
            source_type="database",
            dialect="sqlite",
        ),
    )

    result = table_extract_cli.main(
        [
            "--database-url",
            "sqlite:///source.db",
            "--source-name",
            "db_cli",
            "--profile-only",
            "--dev-mode",
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    assert "Profile summary:" in captured.err
    assert "tables=1" in captured.err
    assert "views=1" in captured.err
    assert '\n  "artifact_type": "table_extract.profile"' in captured.out


def test_file_id_discovery_writes_single_artifact_and_releases_temporary_context(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    output_path = tmp_path / "file.discovery.json"
    released: list[str] = []
    context = fake_file_context()
    captured = {}

    class FakeRepository:
        def __init__(self, database_url):
            self.database_url = database_url
            self.records = []
            captured["repository"] = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def save_table_extraction_record(self, record):
            self.records.append(record)

    class FakeMaterializer:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

        def release_context(self, file_id):
            released.append(file_id)

    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.setattr(table_extract_cli, "require_env", lambda *names: "postgresql://ops")
    monkeypatch.setattr(table_extract_cli, "PostgresTableExtractRepository", FakeRepository)
    monkeypatch.setattr(table_extract_cli, "FileMaterializer", FakeMaterializer)
    monkeypatch.setattr(table_extract_cli, "process_file_id", lambda *args, **kwargs: context)

    def fake_discover_table_source(request, *, run_id=None, config=None):
        captured["request"] = request
        captured["config"] = config
        assert isinstance(request, FileProfileRequest)
        return fake_discovery_result(
            source_name="contacts.csv",
            source_type="csv",
            dialect=None,
            run_id=request.context.run_id,
        )

    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fake_discover_table_source,
    )

    result = table_extract_cli.main(
        [
            "--file-id",
            "file-001",
            "--output",
            str(output_path),
            "--disable-zero-shot",
        ]
    )

    captured_io = capsys.readouterr()
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert captured_io.out == ""
    assert output["artifact_type"] == "table_extract.discovery"
    assert output["run_id"] == "run-001"
    assert output["table_processing_seconds"] is not None
    assert captured["repository"].records[0].status == "table_discovery_completed"
    assert captured["repository"].records[0].discovery_json_path == str(output_path)
    assert captured["repository"].records[0].processing_seconds is not None
    assert released == ["file-001"]
    assert captured["request"].context is context
    assert captured["config"].zero_shot_enabled is False


def test_file_id_profile_only_writes_profile_artifact(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    output_path = tmp_path / "file.profile.json"
    released: list[str] = []
    context = fake_file_context()
    captured = {}

    class FakeRepository:
        def __init__(self, database_url):
            self.database_url = database_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class FakeMaterializer:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

        def release_context(self, file_id):
            released.append(file_id)

    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.setattr(table_extract_cli, "require_env", lambda *names: "postgresql://ops")
    monkeypatch.setattr(table_extract_cli, "PostgresTableExtractRepository", FakeRepository)
    monkeypatch.setattr(table_extract_cli, "FileMaterializer", FakeMaterializer)
    monkeypatch.setattr(table_extract_cli, "process_file_id", lambda *args, **kwargs: context)

    def fake_profile_table_source(request):
        captured["request"] = request
        assert isinstance(request, FileProfileRequest)
        return fake_profile(
            source_name="contacts.csv",
            source_type="csv",
            dialect=None,
        )

    monkeypatch.setattr(
        table_extract_cli,
        "profile_table_source",
        fake_profile_table_source,
    )

    result = table_extract_cli.main(
        [
            "--file-id",
            "file-001",
            "--profile-only",
            "--output",
            str(output_path),
            "--dev-mode",
        ]
    )

    captured_io = capsys.readouterr()
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert captured_io.out == ""
    assert "Profile summary:" in captured_io.err
    assert output["artifact_type"] == "table_extract.profile"
    assert "findings" not in output
    assert released == ["file-001"]
    assert captured["request"].context is context


def test_queue_discovery_requires_output_dir(monkeypatch) -> None:
    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.delenv("TABLE_EXTRACT_OUTPUT_DIR", raising=False)

    with pytest.raises(ValueError, match="--output-dir or TABLE_EXTRACT_OUTPUT_DIR"):
        table_extract_cli.main(["--max-messages", "1"])


def test_queue_discovery_uses_output_dir_from_environment(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    context = fake_file_context(file_id="file:/001", run_id="run/001")
    captured = {}

    class FakeRepository:
        def __init__(self, database_url):
            self.database_url = database_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class FakeMaterializer:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

    class FakeConsumer:
        def __init__(self, url):
            self.url = url

        def close(self):
            return None

    def fake_run_table_listener(**kwargs):
        captured.update(kwargs)
        kwargs["handle_context"](context)

    def fake_discover_table_source(request, *, run_id=None, config=None):
        assert isinstance(request, FileProfileRequest)
        return fake_discovery_result(
            source_name="contacts.csv",
            source_type="csv",
            dialect=None,
            run_id=request.context.run_id,
        )

    monkeypatch.setenv("TABLE_EXTRACT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.setattr(
        table_extract_cli,
        "require_env",
        lambda *names: "amqp://rabbit" if "RABBITMQ_URL" in names else "postgresql://ops",
    )
    monkeypatch.setattr(table_extract_cli, "PostgresTableExtractRepository", FakeRepository)
    monkeypatch.setattr(table_extract_cli, "FileMaterializer", FakeMaterializer)
    monkeypatch.setattr(table_extract_cli, "RabbitMQConsumer", FakeConsumer)
    monkeypatch.setattr(table_extract_cli, "run_table_listener", fake_run_table_listener)
    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fake_discover_table_source,
    )

    result = table_extract_cli.main(["--max-messages", "1", "--disable-zero-shot"])

    captured_io = capsys.readouterr()
    output_path = tmp_path / "run_001_file_001.discovery.json"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert captured_io.out == ""
    assert output["artifact_type"] == "table_extract.discovery"
    assert captured["source_queue_name"] == "Queue-Tables"


def test_queue_discovery_writes_one_artifact_per_message(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    context = fake_file_context(file_id="file:/001", run_id="run/001")
    closed = []
    captured = {}

    class FakeRepository:
        def __init__(self, database_url):
            self.database_url = database_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class FakeMaterializer:
        def __init__(self, repository, config):
            self.repository = repository
            self.config = config

    class FakeConsumer:
        def __init__(self, url):
            self.url = url

        def close(self):
            closed.append(self.url)

    def fake_run_table_listener(**kwargs):
        captured.update(kwargs)
        kwargs["handle_context"](context)

    def fake_discover_table_source(request, *, run_id=None, config=None):
        assert isinstance(request, FileProfileRequest)
        return fake_discovery_result(
            source_name="contacts.csv",
            source_type="csv",
            dialect=None,
            run_id=request.context.run_id,
        )

    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)
    monkeypatch.setattr(
        table_extract_cli,
        "require_env",
        lambda *names: "amqp://rabbit" if "RABBITMQ_URL" in names else "postgresql://ops",
    )
    monkeypatch.setattr(table_extract_cli, "PostgresTableExtractRepository", FakeRepository)
    monkeypatch.setattr(table_extract_cli, "FileMaterializer", FakeMaterializer)
    monkeypatch.setattr(table_extract_cli, "RabbitMQConsumer", FakeConsumer)
    monkeypatch.setattr(table_extract_cli, "run_table_listener", fake_run_table_listener)
    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fake_discover_table_source,
    )

    result = table_extract_cli.main(
        [
            "--max-messages",
            "1",
            "--output-dir",
            str(tmp_path),
            "--disable-zero-shot",
        ]
    )

    captured_io = capsys.readouterr()
    output_path = tmp_path / "run_001_file_001.discovery.json"
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert result == 0
    assert captured_io.out == ""
    assert output["artifact_type"] == "table_extract.discovery"
    assert output["run_id"] == "run/001"
    assert captured["max_messages"] == 1
    assert captured["requeue_messages"] is False
    assert closed == ["amqp://rabbit"]


def test_dev_mode_for_queue_still_requires_max_messages(monkeypatch) -> None:
    monkeypatch.setattr(table_extract_cli, "load_environment", lambda env_file=None: None)

    with pytest.raises(ValueError, match="--dev-mode requires --max-messages"):
        table_extract_cli.main(["--dev-mode", "--output-dir", "/tmp/table-extract"])


def test_scan_config_uses_table_zero_shot_environment(monkeypatch) -> None:
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_MODEL", "local/table-zero-shot")
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_DEVICE", "cpu")
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE", "16")
    args = table_extract_cli.parse_args([])

    config = table_extract_cli._scan_config_from_args(args)

    assert config.zero_shot_model_name == "local/table-zero-shot"
    assert config.zero_shot_device == "cpu"
    assert config.zero_shot_batch_size == 16


def test_scan_config_gpu_flag_uses_auto_device(monkeypatch) -> None:
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_DEVICE", "cpu")
    args = table_extract_cli.parse_args(["--gpu"])

    config = table_extract_cli._scan_config_from_args(args)

    assert config.zero_shot_device == "auto"


def test_scan_config_device_overrides_environment(monkeypatch) -> None:
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_DEVICE", "cpu")
    args = table_extract_cli.parse_args(["--device", "cuda"])

    config = table_extract_cli._scan_config_from_args(args)

    assert config.zero_shot_device == "cuda"


def test_scan_config_rejects_conflicting_gpu_and_device() -> None:
    args = table_extract_cli.parse_args(["--device", "cpu", "--gpu"])

    with pytest.raises(ValueError, match="--gpu cannot be combined"):
        table_extract_cli._scan_config_from_args(args)


def test_scan_config_falls_back_to_shared_zero_shot_model(monkeypatch) -> None:
    monkeypatch.delenv("TABLE_EXTRACT_ZERO_SHOT_MODEL", raising=False)
    monkeypatch.setenv("PII_ENTITY_ZERO_SHOT_MODEL", "local/shared-zero-shot")
    args = table_extract_cli.parse_args([])

    config = table_extract_cli._scan_config_from_args(args)

    assert config.zero_shot_model_name == "local/shared-zero-shot"


def test_scan_config_rejects_invalid_table_zero_shot_batch_size(monkeypatch) -> None:
    monkeypatch.setenv("TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE", "many")
    args = table_extract_cli.parse_args([])

    with pytest.raises(ValueError, match="TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE"):
        table_extract_cli._scan_config_from_args(args)


def test_direct_modes_and_file_id_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        table_extract_cli.parse_args(
            [
                "--database-url",
                "sqlite:///source.db",
                "--ords-url",
                "https://example.com/ords/app/_/sql",
            ]
        )

    with pytest.raises(SystemExit):
        table_extract_cli.parse_args(
            [
                "--file-id",
                "file-001",
                "--database-url",
                "sqlite:///source.db",
            ]
        )


def test_cli_error_output_does_not_include_direct_source_secret(monkeypatch, capsys) -> None:
    disable_operational_dependencies(monkeypatch)

    def fail_discovery(request, **kwargs):
        raise RuntimeError("connection failed")

    monkeypatch.setattr(
        table_extract_cli,
        "discover_table_source",
        fail_discovery,
    )

    with pytest.raises(RuntimeError, match="connection failed"):
        table_extract_cli.main(
            [
                "--database-url",
                "postgresql://user:secret@localhost/db",
            ]
        )

    captured = capsys.readouterr()
    assert "secret" not in captured.out
    assert "secret" not in captured.err
    assert "discovery_failed" in captured.err
    assert "connection failed" in captured.err
