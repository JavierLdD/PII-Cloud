from table_extract.models import (
    ColumnProfile,
    ColumnSample,
    DataSourceProfile,
    DiscoveredPII,
    DiscoveryResult,
    ScanConfig,
    ScanSession,
    TableProfile,
)


def test_build_core_models() -> None:
    column = ColumnProfile(
        column_name="email",
        data_type="varchar",
        nullable=False,
        ordinal_position=1,
    )
    table = TableProfile(
        table_name="customers",
        schema_name="public",
        columns=(column,),
        row_count=100,
    )
    profile = DataSourceProfile(
        source_name="customer_db",
        source_type="database",
        dialect="postgresql",
        tables=(table,),
    )
    sample = ColumnSample(
        table_name="customers",
        column_name="email",
        values=("a@example.com", "b@example.com"),
    )
    finding = DiscoveredPII(
        source_name="customer_db",
        source_type="database",
        schema_name="public",
        table_name="customers",
        column_name="email",
        pii_type="EMAIL",
        confidence=0.95,
        confidence_level="VERY_CONFIDENT",
        detection_method="regex",
        sampled_count=2,
        matched_count=2,
        is_primary_key=False,
        foreign_key=None,
        propagated_from=None,
    )
    config = ScanConfig(sample_limit=500, max_value_length=128)
    result = DiscoveryResult(
        run_id="run-001",
        profile=profile,
        findings=[finding],
    )
    session = ScanSession(
        run_id="run-001",
        source=object(),
        profile=profile,
        config=config,
    )

    assert profile.tables[0].columns[0] == column
    assert sample.sampled_count == 2
    assert sample.non_null_count == 2
    assert finding.pii_type == "EMAIL"
    assert finding.confidence_level == "VERY_CONFIDENT"
    assert result.findings == (finding,)
    assert session.config.sample_limit == 500
    assert ScanConfig().zero_shot_enabled is True
    assert ScanConfig().zero_shot_initial_sample_limit == 50
    assert ScanConfig().zero_shot_expanded_sample_limit == 200


def test_column_sample_values_are_hidden_from_repr() -> None:
    sample = ColumnSample(
        table_name="customers",
        column_name="rut",
        values=("12.345.678-5",),
    )

    assert "12.345.678-5" not in repr(sample)
    assert "values=" not in repr(sample)


def test_datasource_profile_represents_database_csv_and_excel() -> None:
    database_profile = DataSourceProfile(
        source_name="customer_db",
        source_type="database",
        dialect="postgresql",
        tables=(
            TableProfile(
                table_name="customers",
                schema_name="public",
                columns=(ColumnProfile(column_name="email"),),
            ),
            TableProfile(
                table_name="payments",
                schema_name="public",
                columns=(ColumnProfile(column_name="card_last4"),),
            ),
        ),
    )
    csv_profile = DataSourceProfile(
        source_name="customers.csv",
        source_type="csv",
        source_uri="/tmp/customers.csv",
        tables=(
            TableProfile(
                table_name="customers.csv",
                table_type="csv",
                columns=(ColumnProfile(column_name="email"),),
            ),
        ),
    )
    excel_profile = DataSourceProfile(
        source_name="workbook.xlsx",
        source_type="excel",
        source_uri="/tmp/workbook.xlsx",
        tables=(
            TableProfile(
                table_name="Clientes",
                table_type="sheet",
                columns=(ColumnProfile(column_name="rut"),),
            ),
            TableProfile(
                table_name="Pagos",
                table_type="sheet",
                columns=(ColumnProfile(column_name="card_number"),),
            ),
        ),
    )

    assert len(database_profile.tables) == 2
    assert csv_profile.tables[0].table_name == "customers.csv"
    assert [table.table_name for table in excel_profile.tables] == ["Clientes", "Pagos"]


def test_model_validation_rejects_bad_values() -> None:
    try:
        ColumnProfile(column_name="")
    except ValueError as exc:
        assert "column_name" in str(exc)
    else:
        raise AssertionError("empty column_name should fail")

    try:
        ColumnSample(
            table_name="customers",
            column_name="email",
            values=("a@example.com",),
            sampled_count=1,
            non_null_count=2,
        )
    except ValueError as exc:
        assert "non_null_count" in str(exc)
    else:
        raise AssertionError("non_null_count greater than sampled_count should fail")

    try:
        DiscoveredPII(
            source_name="customer_db",
            table_name="customers",
            column_name="email",
            pii_type="EMAIL",
            confidence_level="CERTAIN",
        )
    except ValueError as exc:
        assert "confidence_level" in str(exc)
    else:
        raise AssertionError("unknown confidence_level should fail")
