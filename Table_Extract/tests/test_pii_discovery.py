from __future__ import annotations

from types import SimpleNamespace

from table_extract.discovery import discover_pii
import table_extract.discovery.pii_discovery as pii_discovery
from table_extract.discovery.pii_discovery import (
    ZeroShotDiscoveryError,
    parse_text_date,
    parse_date,
    validate_address_heuristic,
    validate_email,
    validate_license_plate,
    validate_payment_card,
    validate_phone_cl,
    validate_rut,
)
from table_extract.models import (
    ColumnProfile,
    ColumnSample,
    DataSourceProfile,
    ScanConfig,
    ScanSession,
    TableProfile,
)
from table_extract.sources import SourceAdapter


class FakeDiscoverySourceAdapter:
    source_name = "fake_source"
    source_type = "database"
    dialect = "postgresql"
    source_uri = "postgresql://example"

    def __init__(
        self,
        tables: tuple[TableProfile, ...],
        samples: dict[tuple[str, str], tuple[str, ...]],
    ) -> None:
        self._tables = tables
        self._samples = samples
        self.sample_calls: list[tuple[str, str, int, int]] = []
        self.closed = False

    def iter_tables(self):
        return iter(self._tables)

    def iter_columns(self, table):
        return iter(table.columns)

    def get_column_sample(self, table, column, *, limit=1000, max_value_length=256):
        self.sample_calls.append(
            (table.table_name, column.column_name, limit, max_value_length)
        )
        raw_values = self._samples.get((table.table_name, column.column_name), ())
        values = []
        truncated = False
        for raw_value in raw_values:
            value = str(raw_value).strip()
            if not value:
                continue
            if len(value) > max_value_length:
                value = value[:max_value_length]
                truncated = True
            values.append(value)
            if len(values) >= limit:
                break
        return ColumnSample(
            table_name=table.table_name,
            schema_name=table.schema_name,
            column_name=column.column_name,
            values=tuple(values),
            sampled_count=len(values),
            non_null_count=len(values),
            max_value_length=max_value_length,
            truncated=truncated,
        )

    def close(self) -> None:
        self.closed = True


def make_session(
    tables: tuple[TableProfile, ...],
    samples: dict[tuple[str, str], tuple[str, ...]],
    *,
    config: ScanConfig | None = None,
) -> tuple[ScanSession, FakeDiscoverySourceAdapter]:
    source = FakeDiscoverySourceAdapter(tables, samples)
    profile = DataSourceProfile(
        source_name=source.source_name,
        source_type=source.source_type,
        dialect=source.dialect,
        source_uri=source.source_uri,
        tables=tables,
    )
    return (
        ScanSession(
            run_id="run-001",
            source=source,
            profile=profile,
            config=config or ScanConfig(sample_limit=20, max_value_length=64),
        ),
        source,
    )


def by_column(findings):
    return {finding.column_name: finding for finding in findings}


class FakeZeroShotClassifier:
    def __init__(self, scores_by_label: dict[str, float]) -> None:
        self.scores_by_label = scores_by_label
        self.calls = []

    def __call__(
        self,
        values,
        *,
        candidate_labels,
        hypothesis_template,
        multi_label,
        batch_size,
        truncation,
    ):
        self.calls.append(
            {
                "values_count": len(values),
                "candidate_labels": tuple(candidate_labels),
                "hypothesis_template": hypothesis_template,
                "multi_label": multi_label,
                "batch_size": batch_size,
                "truncation": truncation,
            }
        )
        label = candidate_labels[0]
        score = self.scores_by_label[label]
        return [{"scores": [score], "labels": [label]} for _value in values]


def test_deterministic_validators_accept_and_reject_values() -> None:
    assert validate_rut("12.378.895-8")
    assert validate_rut("12378895-8")
    assert not validate_rut("12.378.895-9")
    assert not validate_rut("123788958")

    assert validate_payment_card("4012 0010 3714 1112")
    assert not validate_payment_card("4012 0010 3714 1113")

    assert validate_email("persona@example.com")
    assert not validate_email("persona @example.com")

    assert validate_phone_cl("+56 9 1234 5678")
    assert not validate_phone_cl("12345")

    assert validate_license_plate("JV-TG-56")
    assert not validate_license_plate("ABC")

    assert parse_date("31/12/69") is not None
    assert parse_date("31 de Diciembre de 1969") is None
    assert parse_date("31/02/2020") is None
    assert parse_date("01/01/2100") is None
    assert parse_text_date("31 de Diciembre de 1969") is not None
    assert parse_text_date("31 de NoExiste de 1969") is None
    assert validate_address_heuristic("Av. Siempre Viva 742")
    assert not validate_address_heuristic("Av. Siempre Viva")


class FakeCuda:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class FakeMps:
    def __init__(self, available: bool) -> None:
        self._available = available

    def is_available(self) -> bool:
        return self._available


class FakeTorch:
    def __init__(self, *, cuda: bool = False, mps: bool = False) -> None:
        self.cuda = FakeCuda(cuda)
        self.backends = SimpleNamespace(mps=FakeMps(mps))

    def device(self, name: str) -> str:
        return name


def test_zero_shot_pipeline_device_auto_prefers_cuda_then_mps_then_cpu() -> None:
    assert pii_discovery._pipeline_device("auto", FakeTorch(cuda=True)) == 0
    assert pii_discovery._pipeline_device("auto", FakeTorch(mps=True)) == "mps"
    assert pii_discovery._pipeline_device("auto", FakeTorch()) == -1


def test_zero_shot_pipeline_device_accepts_explicit_devices() -> None:
    assert pii_discovery._pipeline_device("cpu", FakeTorch()) == -1
    assert pii_discovery._pipeline_device("cuda", FakeTorch()) == 0
    assert pii_discovery._pipeline_device("mps", FakeTorch()) == "mps"
    assert pii_discovery._pipeline_device("2", FakeTorch()) == 2


def test_discover_pii_detects_deterministic_columns_with_very_confident_sample() -> None:
    table = TableProfile(
        table_name="clientes",
        schema_name="public",
        columns=(
            ColumnProfile(column_name="rut_cliente", data_type="varchar"),
            ColumnProfile(column_name="numero_tarjeta", data_type="varchar"),
            ColumnProfile(column_name="correo_electronico", data_type="varchar"),
            ColumnProfile(column_name="fono", data_type="varchar"),
            ColumnProfile(column_name="patente_auto", data_type="varchar"),
        ),
    )
    session, _source = make_session(
        (table,),
        {
            ("clientes", "rut_cliente"): ("12.378.895-8", "1.000.005-k"),
            ("clientes", "numero_tarjeta"): (
                "4012 0010 3714 1112",
                "4111 1111 1111 1111",
            ),
            ("clientes", "correo_electronico"): (
                "persona@example.com",
                "contacto@dominio.cl",
            ),
            ("clientes", "fono"): ("+56 9 1234 5678", "912345678"),
            ("clientes", "patente_auto"): ("JV-TG-56", "AB1234"),
        },
    )

    findings = by_column(discover_pii(session))

    assert findings["rut_cliente"].pii_type == "RUT"
    assert findings["numero_tarjeta"].pii_type == "PAYMENT_CARD"
    assert findings["correo_electronico"].pii_type == "EMAIL"
    assert findings["fono"].pii_type == "PHONE_CL"
    assert findings["patente_auto"].pii_type == "LICENSE_PLATE"
    assert {finding.confidence_level for finding in findings.values()} == {
        "VERY_CONFIDENT"
    }


def test_discover_pii_detects_sensitive_catalogs_with_name_context() -> None:
    table = TableProfile(
        table_name="personas",
        columns=(
            ColumnProfile(column_name="prevision_salud"),
            ColumnProfile(column_name="afp"),
            ColumnProfile(column_name="sexo"),
            ColumnProfile(column_name="religion"),
            ColumnProfile(column_name="orientacion_sexual"),
        ),
    )
    session, _source = make_session(
        (table,),
        {
            ("personas", "prevision_salud"): ("Fonasa", "Isapre", "Cruz Blanca"),
            ("personas", "afp"): ("AFP Uno", "Habitat", "AFP Capital"),
            ("personas", "sexo"): ("Masculino", "Femenino", "Mujer"),
            ("personas", "religion"): ("Católica", "Ateo", "Sin religión"),
            ("personas", "orientacion_sexual"): (
                "heterosexual",
                "bisexual",
                "pansexual",
            ),
        },
    )

    findings = by_column(discover_pii(session))

    assert findings["prevision_salud"].pii_type == "HEALTH_SYSTEM"
    assert findings["afp"].pii_type == "AFP"
    assert findings["sexo"].pii_type == "GENDER_IDENTITY"
    assert findings["religion"].pii_type == "RELIGION_OR_BELIEF"
    assert findings["orientacion_sexual"].pii_type == "SEXUAL_ORIENTATION"
    assert {finding.confidence_level for finding in findings.values()} == {
        "VERY_CONFIDENT"
    }


def test_date_accepts_numeric_formats_and_database_date_strings() -> None:
    table = TableProfile(
        table_name="personas",
        columns=(
            ColumnProfile(column_name="fecha_nacimiento", data_type="date"),
            ColumnProfile(column_name="dob", data_type="timestamp"),
        ),
    )
    session, _source = make_session(
        (table,),
        {
            ("personas", "fecha_nacimiento"): (
                "31/12/69",
                "12/31/1969",
                "1969-12-31",
                "1969-12-31 00:00:00",
                "1.1.05",
            ),
            ("personas", "dob"): ("03/04/1980", "1981-05-06"),
        },
    )

    findings = by_column(discover_pii(session))

    assert findings["fecha_nacimiento"].pii_type == "DATE"
    assert findings["fecha_nacimiento"].confidence_level == "VERY_CONFIDENT"
    assert findings["dob"].pii_type == "DATE"
    assert findings["dob"].confidence_level == "VERY_CONFIDENT"


def test_date_text_month_detects_only_with_date_context() -> None:
    table = TableProfile(
        table_name="eventos",
        columns=(
            ColumnProfile(column_name="comentario", data_type="text"),
            ColumnProfile(column_name="created_at", data_type="date"),
            ColumnProfile(column_name="fecha_nacimiento", data_type="date"),
        ),
    )
    session, _source = make_session(
        (table,),
        {
            ("eventos", "comentario"): (
                "31 de Diciembre de 1969",
                "31/02/2020",
                "01/01/2100",
            ),
            ("eventos", "created_at"): ("1969-12-31", "1970-01-01"),
            ("eventos", "fecha_nacimiento"): (
                "31 de Diciembre de 1969",
                "1 enero 2005",
            ),
        },
    )

    findings = by_column(discover_pii(session))

    assert findings["fecha_nacimiento"].pii_type == "DATE"
    assert findings["fecha_nacimiento"].confidence_level == "VERY_CONFIDENT"
    assert "comentario" not in findings
    assert "created_at" not in findings


def test_header_candidate_without_confirming_sample_is_probable() -> None:
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="correo_electronico"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "correo_electronico"): ("no aplica", "sin correo")},
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "EMAIL"
    assert findings[0].confidence_level == "PROBABLE"
    assert findings[0].matched_count == 0


def test_discovery_does_not_expose_raw_sample_values_in_findings() -> None:
    secret_email = "persona.secreta@example.com"
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="correo_electronico"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "correo_electronico"): (secret_email,)},
    )

    finding = discover_pii(session)[0]

    assert secret_email not in repr(finding)
    assert secret_email not in (finding.evidence_summary or "")


def test_discovery_respects_sample_limit_and_max_value_length() -> None:
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="correo_electronico"),),
    )
    session, source = make_session(
        (table,),
        {("clientes", "correo_electronico"): ("persona@example.com",) * 10},
        config=ScanConfig(sample_limit=7, max_value_length=12),
    )

    discover_pii(session)

    assert source.sample_calls == [("clientes", "correo_electronico", 7, 12)]


def test_online_identifier_header_is_probable_without_zero_shot(monkeypatch) -> None:
    def fail_loader(_model_name, _device):
        raise AssertionError("zero-shot model should not be loaded")

    monkeypatch.setattr(pii_discovery, "_load_zero_shot_classifier", fail_loader)
    table = TableProfile(
        table_name="cuentas",
        columns=(ColumnProfile(column_name="username"),),
    )
    session, _source = make_session(
        (table,),
        {("cuentas", "username"): ("jperez", "mrojas")},
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "ONLINE_IDENTIFIER"
    assert findings[0].confidence_level == "PROBABLE"
    assert findings[0].detection_method == "header_online_identifier"


def test_address_heuristic_runs_before_zero_shot(monkeypatch) -> None:
    def fail_loader(_model_name, _device):
        raise AssertionError("zero-shot model should not be loaded")

    monkeypatch.setattr(pii_discovery, "_load_zero_shot_classifier", fail_loader)
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="domicilio"),),
    )
    session, _source = make_session(
        (table,),
        {
            ("clientes", "domicilio"): (
                "Av. Siempre Viva 742",
                "Pasaje Los Robles 123 Depto 4",
                "Camino Interior 55",
            )
        },
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "ADDRESS"
    assert findings[0].confidence_level == "CONFIDENT"
    assert findings[0].detection_method == "address_heuristic"


def test_zero_shot_full_name_expands_to_200_and_blocks_first_last(monkeypatch) -> None:
    full_name_label = pii_discovery.ZERO_SHOT_LABELS["FULL_NAME"]
    classifier = FakeZeroShotClassifier({full_name_label: 0.82})
    monkeypatch.setattr(
        pii_discovery,
        "_load_zero_shot_classifier",
        lambda _model_name, _device: classifier,
    )
    values = tuple(f"Nombre Apellido {index}" for index in range(250))
    values = tuple(value.replace(str(index), "Test") for index, value in enumerate(values))
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="nombre_completo"),),
    )
    session, source = make_session(
        (table,),
        {("clientes", "nombre_completo"): values},
        config=ScanConfig(sample_limit=250, max_value_length=64),
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "FULL_NAME"
    assert findings[0].confidence_level == "VERY_CONFIDENT"
    assert findings[0].matched_count == 200
    assert "avg_score=0.82" in (findings[0].evidence_summary or "")
    assert len(classifier.calls) == 2
    assert classifier.calls[0]["values_count"] == 50
    assert classifier.calls[1]["values_count"] == 200
    assert classifier.calls[0]["candidate_labels"] == (full_name_label,)
    assert classifier.calls[0]["multi_label"] is True
    assert source.sample_calls == [
        ("clientes", "nombre_completo", 50, 64),
        ("clientes", "nombre_completo", 50, 64),
        ("clientes", "nombre_completo", 200, 64),
    ]


def test_zero_shot_tries_first_name_when_full_name_is_not_candidate(monkeypatch) -> None:
    first_name_label = pii_discovery.ZERO_SHOT_LABELS["FIRST_NAME"]
    classifier = FakeZeroShotClassifier({first_name_label: 0.78})
    monkeypatch.setattr(
        pii_discovery,
        "_load_zero_shot_classifier",
        lambda _model_name, _device: classifier,
    )
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="nombre"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "nombre"): ("Ana", "Maria", "Pedro", "Camila") * 60},
        config=ScanConfig(sample_limit=200, max_value_length=64),
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "FIRST_NAME"
    assert findings[0].confidence_level == "CONFIDENT"
    assert classifier.calls[0]["candidate_labels"] == (first_name_label,)


def test_zero_shot_tries_last_name_when_column_points_to_surname(monkeypatch) -> None:
    last_name_label = pii_discovery.ZERO_SHOT_LABELS["LAST_NAME"]
    classifier = FakeZeroShotClassifier({last_name_label: 0.78})
    monkeypatch.setattr(
        pii_discovery,
        "_load_zero_shot_classifier",
        lambda _model_name, _device: classifier,
    )
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="apellido_paterno"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "apellido_paterno"): ("Perez", "Soto", "Rojas") * 70},
        config=ScanConfig(sample_limit=200, max_value_length=64),
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "LAST_NAME"
    assert findings[0].confidence_level == "CONFIDENT"
    assert classifier.calls[0]["candidate_labels"] == (last_name_label,)


def test_zero_shot_can_classify_address_without_heuristic_match(monkeypatch) -> None:
    address_label = pii_discovery.ZERO_SHOT_LABELS["ADDRESS"]
    classifier = FakeZeroShotClassifier({address_label: 0.81})
    monkeypatch.setattr(
        pii_discovery,
        "_load_zero_shot_classifier",
        lambda _model_name, _device: classifier,
    )
    raw_address = "Los Aromos sector norte"
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="direccion"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "direccion"): (raw_address,) * 80},
        config=ScanConfig(sample_limit=200, max_value_length=64),
    )

    findings = discover_pii(session)

    assert len(findings) == 1
    assert findings[0].pii_type == "ADDRESS"
    assert findings[0].confidence_level == "VERY_CONFIDENT"
    assert findings[0].detection_method == "zero_shot"
    assert raw_address not in repr(findings[0])
    assert raw_address not in (findings[0].evidence_summary or "")
    assert classifier.calls[0]["candidate_labels"] == (address_label,)


def test_zero_shot_cuts_off_after_initial_sample_when_scores_are_low(monkeypatch) -> None:
    full_name_label = pii_discovery.ZERO_SHOT_LABELS["FULL_NAME"]
    classifier = FakeZeroShotClassifier({full_name_label: 0.20})
    monkeypatch.setattr(
        pii_discovery,
        "_load_zero_shot_classifier",
        lambda _model_name, _device: classifier,
    )
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="nombre_completo"),),
    )
    session, source = make_session(
        (table,),
        {("clientes", "nombre_completo"): ("Uno Dos",) * 250},
        config=ScanConfig(sample_limit=250, max_value_length=64),
    )

    findings = discover_pii(session)

    assert findings == []
    assert len(classifier.calls) == 1
    assert classifier.calls[0]["values_count"] == 50
    assert source.sample_calls == [
        ("clientes", "nombre_completo", 50, 64),
        ("clientes", "nombre_completo", 50, 64),
    ]


def test_semantic_discovery_skips_numeric_and_hash_columns(monkeypatch) -> None:
    def fail_loader(_model_name, _device):
        raise AssertionError("zero-shot model should not be loaded")

    monkeypatch.setattr(pii_discovery, "_load_zero_shot_classifier", fail_loader)
    table = TableProfile(
        table_name="tecnico",
        columns=(
            ColumnProfile(column_name="cliente", data_type="integer"),
            ColumnProfile(column_name="nombre", data_type="varchar"),
        ),
    )
    session, source = make_session(
        (table,),
        {
            ("tecnico", "cliente"): ("123",),
            ("tecnico", "nombre"): (
                "9f86d081884c7d659a2feaa0c55ad015",
                "e3b0c44298fc1c149afbf4c8996fb924",
            ),
        },
    )

    assert discover_pii(session) == []
    assert source.sample_calls == [
        ("tecnico", "cliente", 20, 64),
        ("tecnico", "nombre", 20, 64),
        ("tecnico", "nombre", 20, 64),
    ]


def test_zero_shot_model_missing_fails_clearly(monkeypatch) -> None:
    def missing_loader(_model_name, _device):
        raise ZeroShotDiscoveryError("Zero-shot model is not available locally.")

    monkeypatch.setattr(pii_discovery, "_load_zero_shot_classifier", missing_loader)
    table = TableProfile(
        table_name="clientes",
        columns=(ColumnProfile(column_name="nombre_completo"),),
    )
    session, _source = make_session(
        (table,),
        {("clientes", "nombre_completo"): ("Ana Perez", "Juan Soto")},
    )

    try:
        discover_pii(session)
    except ZeroShotDiscoveryError as exc:
        assert "locally" in str(exc)
    else:
        raise AssertionError("missing local zero-shot model should fail")


def test_propagates_by_specific_column_name_and_foreign_key_but_not_generic() -> None:
    customers = TableProfile(
        table_name="customers",
        schema_name="public",
        columns=(
            ColumnProfile(column_name="rut", is_primary_key=True),
            ColumnProfile(column_name="id", is_primary_key=True),
        ),
    )
    payments = TableProfile(
        table_name="payments",
        schema_name="public",
        columns=(
            ColumnProfile(column_name="rut"),
            ColumnProfile(column_name="customer_rut", foreign_key="public.customers.rut"),
            ColumnProfile(column_name="id"),
        ),
    )
    session, _source = make_session(
        (customers, payments),
        {
            ("customers", "rut"): ("12.378.895-8",),
            ("customers", "id"): ("1",),
            ("payments", "rut"): ("sin dato",),
            ("payments", "customer_rut"): ("sin dato",),
            ("payments", "id"): ("2",),
        },
    )

    findings = by_column(discover_pii(session))

    assert findings["rut"].pii_type == "RUT"
    assert findings["rut"].propagated_from == "public.customers.rut"
    assert findings["rut"].confidence_level == "CONFIDENT"
    assert findings["customer_rut"].pii_type == "RUT"
    assert findings["customer_rut"].propagated_from == "public.customers.rut"
    assert findings["customer_rut"].foreign_key == "public.customers.rut"
    assert "id" not in findings


def test_source_adapter_runtime_protocol_accepts_discovery_fake() -> None:
    session, source = make_session(
        (TableProfile(table_name="t", columns=(ColumnProfile(column_name="c"),)),),
        {},
    )

    assert isinstance(source, SourceAdapter)
    assert discover_pii(session) == []
