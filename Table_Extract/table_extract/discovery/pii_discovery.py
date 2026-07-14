from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import re
import unicodedata

from table_extract.models import ColumnProfile, DiscoveredPII, ScanSession, TableProfile
from table_extract.operational import OperationalException


RUT = "RUT"
PAYMENT_CARD = "PAYMENT_CARD"
DATE = "DATE"
LICENSE_PLATE = "LICENSE_PLATE"
EMAIL = "EMAIL"
HEALTH_SYSTEM = "HEALTH_SYSTEM"
AFP = "AFP"
SEXUAL_ORIENTATION = "SEXUAL_ORIENTATION"
GENDER_IDENTITY = "GENDER_IDENTITY"
RELIGION_OR_BELIEF = "RELIGION_OR_BELIEF"
PHONE_CL = "PHONE_CL"
FULL_NAME = "FULL_NAME"
FIRST_NAME = "FIRST_NAME"
LAST_NAME = "LAST_NAME"
ADDRESS = "ADDRESS"
ONLINE_IDENTIFIER = "ONLINE_IDENTIFIER"

VERY_CONFIDENT = "VERY_CONFIDENT"
CONFIDENT = "CONFIDENT"
PROBABLE = "PROBABLE"

MAX_DISCOVERY_SAMPLE_VALUES = 50
VERY_CONFIDENT_THRESHOLD = 0.85
CONFIDENT_THRESHOLD = 0.50

ENTITY_ORDER = (
    RUT,
    PAYMENT_CARD,
    EMAIL,
    PHONE_CL,
    LICENSE_PLATE,
    DATE,
    HEALTH_SYSTEM,
    AFP,
    SEXUAL_ORIENTATION,
    GENDER_IDENTITY,
    RELIGION_OR_BELIEF,
    ONLINE_IDENTIFIER,
    FULL_NAME,
    FIRST_NAME,
    LAST_NAME,
    ADDRESS,
)

DETERMINISTIC_ENTITY_ORDER = (
    RUT,
    PAYMENT_CARD,
    EMAIL,
    PHONE_CL,
    LICENSE_PLATE,
    DATE,
    HEALTH_SYSTEM,
    AFP,
    SEXUAL_ORIENTATION,
    GENDER_IDENTITY,
    RELIGION_OR_BELIEF,
)

CATALOG_ENTITY_TYPES = {
    HEALTH_SYSTEM,
    AFP,
    SEXUAL_ORIENTATION,
    GENDER_IDENTITY,
    RELIGION_OR_BELIEF,
}

HEADER_TOKENS = {
    RUT: {"rut", "run", "dni", "cedula"},
    PAYMENT_CARD: {"tarjeta", "card", "pan"},
    DATE: {"birthdate", "dob", "nac", "nacimiento"},
    LICENSE_PLATE: {"patente", "placa", "matricula"},
    EMAIL: {"correo", "email", "mail"},
    HEALTH_SYSTEM: {"fonasa", "isapre", "salud"},
    AFP: {"afp", "pension", "pensiones"},
    SEXUAL_ORIENTATION: {"orientacion"},
    GENDER_IDENTITY: {"genero", "gender", "sex", "sexo"},
    RELIGION_OR_BELIEF: {"credo", "culto", "religion"},
    PHONE_CL: {"celular", "fono", "movil", "phone", "telefono", "telefonos"},
    FULL_NAME: {"fullname"},
    FIRST_NAME: {"nombre", "nombres", "firstname", "givenname"},
    LAST_NAME: {"apellido", "apellidos", "lastname", "surname"},
    ADDRESS: {"address", "calle", "direccion", "domicilio", "residencia"},
    ONLINE_IDENTIFIER: {
        "alias",
        "handle",
        "login",
        "nickname",
        "usuario",
        "username",
    },
}

HEADER_PHRASES = {
    RUT: {
        "cedula_identidad",
        "rol_unico_nacional",
        "rol_unico_tributario",
        "run_cliente",
        "rut_cliente",
        "rut_persona",
    },
    PAYMENT_CARD: {
        "card_number",
        "credit_card",
        "numero_tarjeta",
        "tarjeta_credito",
    },
    DATE: {
        "birth_date",
        "date_of_birth",
        "fecha_nac",
        "fecha_nacimiento",
        "f_nac",
        "f_nacimiento",
    },
    LICENSE_PLATE: {"patente_vehiculo", "placa_patente"},
    EMAIL: {
        "correo_contacto",
        "correo_electronico",
        "email_cliente",
        "mail_cliente",
    },
    HEALTH_SYSTEM: {"institucion_salud", "prevision_salud", "sistema_salud"},
    AFP: {
        "administradora_fondos_pensiones",
        "afp_cliente",
        "prevision_pension",
        "sistema_afp",
        "sistema_pension",
    },
    SEXUAL_ORIENTATION: {"orientacion_sexual", "sexual_orientation"},
    GENDER_IDENTITY: {"identidad_genero", "gender_identity"},
    RELIGION_OR_BELIEF: {"creencia_religiosa", "religion_creencia"},
    PHONE_CL: {
        "celular_cliente",
        "numero_contacto",
        "numero_telefono",
        "telefono_cliente",
    },
    FULL_NAME: {
        "beneficiario",
        "contacto",
        "full_name",
        "nombre_cliente",
        "nombre_completo",
        "nombre_paciente",
        "nombre_persona",
        "nombres_apellidos",
        "persona",
        "titular",
    },
    FIRST_NAME: {
        "first_name",
        "given_name",
        "primer_nombre",
        "segundo_nombre",
    },
    LAST_NAME: {
        "apellido_materno",
        "apellido_paterno",
        "family_name",
        "last_name",
        "primer_apellido",
        "segundo_apellido",
    },
    ADDRESS: {
        "address",
        "calle",
        "direccion",
        "direccion_particular",
        "domicilio",
        "residencia",
    },
    ONLINE_IDENTIFIER: {
        "alias",
        "handle",
        "login",
        "nickname",
        "user_name",
        "usuario",
        "username",
    },
}

REGEX_VALUE_PATTERNS = {
    RUT: r"(?<![A-Za-z0-9])(?:\d{1,2}\.?\d{3}\.?\d{3}\s*-\s*[0-9Kk])(?![A-Za-z0-9])",
    EMAIL: r"(?i)(?<![A-Za-z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Za-z0-9._%+-])",
    PHONE_CL: r"(?<!\d)(?:(?:\+?56|0056)[\s.-]*)?(?:9|[2-7])(?:[\s.-]*\d){8}(?![\s.-]*\d)",
    PAYMENT_CARD: r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)",
    LICENSE_PLATE: r"(?<![A-Za-z0-9])(?:[A-Z]{2}[-\s]?[A-Z]{2}[-\s]?\d{2}|[A-Z]{2}[-\s]?\d{2}[-\s]?\d{2}|[A-Z]{3}[-\s]?\d{2}|[A-Z]{4,5}[-\s]?\d)(?![A-Za-z0-9])",
    DATE: r"(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.](?:\d{2}|\d{4}))(?:[T\s]+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",
}

BROAD_NAME_CONTEXT_NAMES = {
    "beneficiario",
    "cliente",
    "contacto",
    "paciente",
    "persona",
    "titular",
}

ORGANIZATION_CONTEXT_TOKENS = {
    "empresa",
    "institucion",
    "organizacion",
    "proveedor",
    "razon",
    "social",
}

ADDRESS_VALUE_TOKENS = {
    "av",
    "avda",
    "avenida",
    "block",
    "calle",
    "camino",
    "casa",
    "depto",
    "departamento",
    "local",
    "oficina",
    "pasaje",
    "piso",
    "poblacion",
    "residencia",
    "ruta",
    "villa",
}

TECHNICAL_STRING_NAMES = {
    "hash",
    "checksum",
    "digest",
    "token",
    "uuid",
    "guid",
}

NUMERIC_DATA_TYPE_TOKENS = {
    "bigint",
    "decimal",
    "double",
    "float",
    "int",
    "integer",
    "money",
    "number",
    "numeric",
    "real",
    "serial",
    "smallint",
}

ZERO_SHOT_LABELS = {
    FULL_NAME: "un nombre completo de una persona",
    FIRST_NAME: "un nombre de pila de una persona",
    LAST_NAME: "un apellido de una persona",
    ADDRESS: "una direccion o domicilio",
}

GENERIC_COLUMN_NAMES = {
    "campo",
    "codigo",
    "cod",
    "descripcion",
    "desc",
    "estado",
    "id",
    "numero",
    "num",
    "observacion",
    "observaciones",
    "tipo",
    "valor",
}

NEGATIVE_DATE_TOKENS = {
    "actualizacion",
    "alta",
    "baja",
    "compra",
    "creacion",
    "created",
    "createdat",
    "emision",
    "expiration",
    "expiracion",
    "ingreso",
    "modificacion",
    "modified",
    "pago",
    "registro",
    "updated",
    "updatedat",
    "vencimiento",
}

AFP_VALUES_RAW = {
    "afp capital",
    "capital",
    "afp cuprum",
    "cuprum",
    "afp habitat",
    "habitat",
    "afp modelo",
    "modelo",
    "afp planvital",
    "planvital",
    "afp provida",
    "provida",
    "afp uno",
    "uno",
}

GENERIC_AFP_VALUES = {"capital", "modelo", "uno"}

HEALTH_SYSTEM_VALUES_RAW = {
    "banmedica",
    "banmedica s.a.",
    "colmena",
    "colmena golden cross",
    "colmena golden cross s.a.",
    "consalud",
    "consalud s.a.",
    "cruz blanca",
    "cruz blanca s.a.",
    "cruz del norte",
    "cruz del norte ltda.",
    "esencial",
    "esencial s.a.",
    "fonasa",
    "fundacion",
    "fundacion ltda.",
    "isalud",
    "isalud ltda.",
    "isapre",
    "nueva masvida",
    "nueva masvida s.a.",
    "vida tres",
    "vida tres s.a.",
}

GENERIC_HEALTH_VALUES = {"fundacion"}

SEXUAL_ORIENTATION_VALUES_RAW = {
    "asexual",
    "bisexual",
    "gay",
    "heterosexual",
    "homosexual",
    "lesbiana",
    "pansexual",
    "queer",
}

GENDER_IDENTITY_VALUES_RAW = {
    "f",
    "femenino",
    "h",
    "hombre",
    "m",
    "masculino",
    "mujer",
    "no binario",
    "no-binario",
    "non binary",
    "otro",
    "trans",
    "transgenero",
}

CONTEXT_REQUIRED_GENDER_VALUES = {"f", "h", "m", "otro"}

RELIGION_VALUES_RAW = {
    "agnostica",
    "agnostico",
    "agnosticismo",
    "atea",
    "ateismo",
    "ateo",
    "bahai",
    "budismo",
    "budista",
    "catolica",
    "catolico",
    "cristiana",
    "cristianismo",
    "cristiano",
    "evangelica",
    "evangelico",
    "hindu",
    "hinduismo",
    "hinduista",
    "islam",
    "islamica",
    "islamico",
    "judaismo",
    "judia",
    "judio",
    "mormon",
    "mormona",
    "musulman",
    "musulmana",
    "ninguna religion",
    "no profesa religion",
    "protestante",
    "religion catolica",
    "religion evangelica",
    "sij",
    "sin religion",
    "sikh",
    "testigo de jehova",
    "testigos de jehova",
}

TEXT_MONTHS = {
    "ene": 1,
    "enero": 1,
    "jan": 1,
    "january": 1,
    "feb": 2,
    "febrero": 2,
    "february": 2,
    "mar": 3,
    "marzo": 3,
    "march": 3,
    "abr": 4,
    "abril": 4,
    "apr": 4,
    "april": 4,
    "may": 5,
    "mayo": 5,
    "jun": 6,
    "junio": 6,
    "june": 6,
    "jul": 7,
    "julio": 7,
    "july": 7,
    "ago": 8,
    "agosto": 8,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "septiembre": 9,
    "september": 9,
    "oct": 10,
    "octubre": 10,
    "october": 10,
    "nov": 11,
    "noviembre": 11,
    "november": 11,
    "dic": 12,
    "diciembre": 12,
    "dec": 12,
    "december": 12,
}


class ZeroShotDiscoveryError(OperationalException):
    component = "discovery"
    category = "zero_shot_model_unavailable"
    retryable = False


@dataclass(frozen=True)
class ColumnContext:
    table: TableProfile
    column: ColumnProfile
    normalized_name: str
    tokens: frozenset[str]
    header_types: frozenset[str]
    data_type: str

    @property
    def date_name_context(self) -> bool:
        return DATE in self.header_types

    @property
    def date_type_context(self) -> bool:
        return any(
            token in self.data_type
            for token in ("date", "datetime", "timestamp", "time")
        )

    @property
    def negative_date_context(self) -> bool:
        return bool(self.tokens & NEGATIVE_DATE_TOKENS)

    @property
    def numeric_type_context(self) -> bool:
        if not self.data_type:
            return False
        return any(token in self.data_type for token in NUMERIC_DATA_TYPE_TOKENS)

    @property
    def strong_name_context(self) -> bool:
        return bool(self.header_types & {FULL_NAME, FIRST_NAME, LAST_NAME})

    @property
    def broad_name_context(self) -> bool:
        return self.normalized_name in BROAD_NAME_CONTEXT_NAMES

    @property
    def organization_context(self) -> bool:
        return bool(self.tokens & ORGANIZATION_CONTEXT_TOKENS)

    @property
    def address_context(self) -> bool:
        return ADDRESS in self.header_types

    @property
    def online_identifier_context(self) -> bool:
        return ONLINE_IDENTIFIER in self.header_types

    @property
    def technical_string_context(self) -> bool:
        return bool(self.tokens & TECHNICAL_STRING_NAMES)

    @property
    def specific_name(self) -> bool:
        if self.normalized_name in GENERIC_COLUMN_NAMES:
            return False
        return not bool(self.tokens) or not self.tokens.issubset(GENERIC_COLUMN_NAMES)


@dataclass(frozen=True)
class Candidate:
    pii_type: str
    confidence_level: str
    confidence: float
    detection_method: str
    evidence_summary: str
    sampled_count: int
    matched_count: int
    match_rate: float
    avg_score: float = 0.0


Validator = Callable[[str, ColumnContext], bool]


def discover_pii(session: ScanSession) -> list[DiscoveredPII]:
    """Detect deterministic PII findings for a profiled table source.

    The function only uses the public discovery contract: the structural
    profile in `ScanSession` and the live `SourceAdapter.get_column_sample(...)`
    method. Sampled values are inspected in memory and are never copied to the
    returned findings.
    """

    contexts = [
        _build_column_context(table, column)
        for table in session.profile.tables
        for column in table.columns
    ]
    findings_by_key: dict[tuple[str, str, str], DiscoveredPII] = {}
    sample_limit = min(session.config.sample_limit, MAX_DISCOVERY_SAMPLE_VALUES)

    for context in contexts:
        candidate = _analyze_column(session, context, sample_limit=sample_limit)
        if candidate is None:
            continue

        key = _column_key(context.table, context.column)
        findings_by_key[key] = _build_finding(
            session,
            context,
            candidate,
            propagated_from=None,
        )

    _apply_semantic_discovery(session, contexts, findings_by_key)
    _propagate_by_column_name(session, contexts, findings_by_key)
    _propagate_by_foreign_key(session, contexts, findings_by_key)
    return list(findings_by_key.values())


def _build_column_context(table: TableProfile, column: ColumnProfile) -> ColumnContext:
    normalized_name = normalize_identifier(column.column_name)
    tokens = frozenset(token for token in normalized_name.split("_") if token)
    header_types = frozenset(_infer_header_types(normalized_name, tokens))
    return ColumnContext(
        table=table,
        column=column,
        normalized_name=normalized_name,
        tokens=tokens,
        header_types=header_types,
        data_type=normalize_identifier(column.data_type or ""),
    )


def _infer_header_types(normalized_name: str, tokens: frozenset[str]) -> tuple[str, ...]:
    inferred: list[str] = []
    for entity_type in ENTITY_ORDER:
        if normalized_name in HEADER_PHRASES.get(entity_type, set()):
            inferred.append(entity_type)
            continue
        if tokens & HEADER_TOKENS.get(entity_type, set()):
            inferred.append(entity_type)
    return tuple(inferred)


def _analyze_column(
    session: ScanSession,
    context: ColumnContext,
    *,
    sample_limit: int,
) -> Candidate | None:
    sample = session.source.get_column_sample(
        context.table,
        context.column,
        limit=sample_limit,
        max_value_length=session.config.max_value_length,
    )
    values = tuple(value for value in sample.values if value.strip())

    candidates = [
        candidate
        for entity_type in DETERMINISTIC_ENTITY_ORDER
        if (
            candidate := _evaluate_entity(
                entity_type,
                context,
                values,
                sampled_count=len(values),
            )
        )
        is not None
    ]
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda candidate: (
            _confidence_rank(candidate.confidence_level),
            candidate.match_rate,
            candidate.matched_count,
            _entity_priority(candidate.pii_type),
        ),
    )


def _evaluate_entity(
    entity_type: str,
    context: ColumnContext,
    values: tuple[str, ...],
    *,
    sampled_count: int,
) -> Candidate | None:
    validator = VALIDATORS[entity_type]
    matched_count = sum(1 for value in values if validator(value, context))
    match_rate = matched_count / sampled_count if sampled_count else 0.0
    header_match = entity_type in context.header_types

    if matched_count:
        confidence_level = _confidence_from_values(
            entity_type,
            context,
            matched_count=matched_count,
            sampled_count=sampled_count,
            match_rate=match_rate,
            header_match=header_match,
        )
        if confidence_level is None:
            return None
        method = "header_and_value" if header_match else "value_validator"
    elif header_match:
        if entity_type == DATE and sampled_count:
            return None
        confidence_level = PROBABLE
        method = "header_only"
    else:
        return None

    return Candidate(
        pii_type=entity_type,
        confidence_level=confidence_level,
        confidence=_confidence_score(confidence_level, match_rate),
        detection_method=method,
        evidence_summary=_evidence_summary(
            context,
            entity_type=entity_type,
            method=method,
            matched_count=matched_count,
            sampled_count=sampled_count,
            match_rate=match_rate,
            propagated=False,
        ),
        sampled_count=sampled_count,
        matched_count=matched_count,
        match_rate=match_rate,
    )


def _confidence_from_values(
    entity_type: str,
    context: ColumnContext,
    *,
    matched_count: int,
    sampled_count: int,
    match_rate: float,
    header_match: bool,
) -> str | None:
    if entity_type == DATE:
        return _date_confidence(
            context,
            matched_count=matched_count,
            match_rate=match_rate,
            header_match=header_match,
        )

    if match_rate >= VERY_CONFIDENT_THRESHOLD:
        if entity_type in CATALOG_ENTITY_TYPES and not header_match and matched_count < 3:
            return CONFIDENT
        return VERY_CONFIDENT

    if match_rate >= CONFIDENT_THRESHOLD and matched_count >= 2:
        return CONFIDENT

    if header_match:
        return PROBABLE

    if (
        sampled_count
        and matched_count == sampled_count
        and entity_type not in CATALOG_ENTITY_TYPES
    ):
        return PROBABLE

    return None


def _date_confidence(
    context: ColumnContext,
    *,
    matched_count: int,
    match_rate: float,
    header_match: bool,
) -> str | None:
    if context.negative_date_context and not context.date_name_context:
        return None

    if match_rate >= VERY_CONFIDENT_THRESHOLD:
        if header_match:
            return VERY_CONFIDENT
        if context.date_type_context:
            return CONFIDENT
        return PROBABLE

    if header_match and match_rate >= CONFIDENT_THRESHOLD:
        return CONFIDENT

    if header_match:
        return PROBABLE

    return None


def _build_finding(
    session: ScanSession,
    context: ColumnContext,
    candidate: Candidate,
    *,
    propagated_from: str | None,
) -> DiscoveredPII:
    return DiscoveredPII(
        source_name=session.profile.source_name,
        source_type=session.profile.source_type,
        schema_name=context.table.schema_name,
        table_name=context.table.table_name,
        column_name=context.column.column_name,
        pii_type=candidate.pii_type,
        confidence=candidate.confidence,
        confidence_level=candidate.confidence_level,
        detection_method=candidate.detection_method,
        evidence_summary=candidate.evidence_summary,
        sampled_count=candidate.sampled_count,
        matched_count=candidate.matched_count,
        is_primary_key=context.column.is_primary_key,
        foreign_key=context.column.foreign_key,
        propagated_from=propagated_from,
    )


def _apply_semantic_discovery(
    session: ScanSession,
    contexts: list[ColumnContext],
    findings_by_key: dict[tuple[str, str, str], DiscoveredPII],
) -> None:
    for context in contexts:
        key = _column_key(context.table, context.column)
        if key in findings_by_key:
            continue
        if context.numeric_type_context:
            continue

        initial_limit = min(
            session.config.sample_limit,
            session.config.zero_shot_initial_sample_limit,
        )
        values = _sample_semantic_values(session, context, limit=initial_limit)
        if _looks_like_technical_string_column(context, values):
            continue

        candidate = _analyze_semantic_heuristics(context, values)
        if candidate is None and session.config.zero_shot_enabled:
            candidate = _analyze_zero_shot_column(session, context, values)
        if candidate is None:
            continue

        findings_by_key[key] = _build_finding(
            session,
            context,
            candidate,
            propagated_from=None,
        )


def _sample_semantic_values(
    session: ScanSession,
    context: ColumnContext,
    *,
    limit: int,
) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    sample = session.source.get_column_sample(
        context.table,
        context.column,
        limit=limit,
        max_value_length=session.config.max_value_length,
    )
    return tuple(value for value in sample.values if value.strip())


def _analyze_semantic_heuristics(
    context: ColumnContext,
    values: tuple[str, ...],
) -> Candidate | None:
    if context.online_identifier_context:
        return _semantic_candidate(
            context,
            entity_type=ONLINE_IDENTIFIER,
            method="header_online_identifier",
            matched_count=0,
            sampled_count=len(values),
            match_rate=0.0,
            confidence_level=PROBABLE,
        )

    date_candidate = _text_date_candidate(context, values)
    if date_candidate is not None:
        return date_candidate

    return _address_heuristic_candidate(context, values)


def _text_date_candidate(
    context: ColumnContext,
    values: tuple[str, ...],
) -> Candidate | None:
    if not context.date_name_context or context.negative_date_context or not values:
        return None

    matched_count = sum(1 for value in values if parse_text_date(value))
    match_rate = matched_count / len(values) if values else 0.0
    if matched_count == 0:
        return None

    if match_rate >= VERY_CONFIDENT_THRESHOLD:
        confidence_level = VERY_CONFIDENT
    elif match_rate >= CONFIDENT_THRESHOLD:
        confidence_level = CONFIDENT
    else:
        confidence_level = PROBABLE

    return _semantic_candidate(
        context,
        entity_type=DATE,
        method="text_date_validator",
        matched_count=matched_count,
        sampled_count=len(values),
        match_rate=match_rate,
        confidence_level=confidence_level,
    )


def _address_heuristic_candidate(
    context: ColumnContext,
    values: tuple[str, ...],
) -> Candidate | None:
    if not values:
        return None

    matched_count = sum(1 for value in values if validate_address_heuristic(value))
    match_rate = matched_count / len(values)
    if matched_count == 0:
        return None

    if context.address_context and match_rate >= 0.65:
        confidence_level = CONFIDENT
    elif context.address_context or match_rate >= CONFIDENT_THRESHOLD:
        confidence_level = PROBABLE
    else:
        return None

    return _semantic_candidate(
        context,
        entity_type=ADDRESS,
        method="address_heuristic",
        matched_count=matched_count,
        sampled_count=len(values),
        match_rate=match_rate,
        confidence_level=confidence_level,
    )


def _semantic_candidate(
    context: ColumnContext,
    *,
    entity_type: str,
    method: str,
    matched_count: int,
    sampled_count: int,
    match_rate: float,
    confidence_level: str,
    avg_score: float = 0.0,
    model_name: str | None = None,
    positive_threshold: float | None = None,
    continue_threshold: float | None = None,
) -> Candidate:
    return Candidate(
        pii_type=entity_type,
        confidence_level=confidence_level,
        confidence=_confidence_score(confidence_level, match_rate),
        detection_method=method,
        evidence_summary=_evidence_summary(
            context,
            entity_type=entity_type,
            method=method,
            matched_count=matched_count,
            sampled_count=sampled_count,
            match_rate=match_rate,
            propagated=False,
            avg_score=avg_score,
            model_name=model_name,
            positive_threshold=positive_threshold,
            continue_threshold=continue_threshold,
        ),
        sampled_count=sampled_count,
        matched_count=matched_count,
        match_rate=match_rate,
        avg_score=avg_score,
    )


def _analyze_zero_shot_column(
    session: ScanSession,
    context: ColumnContext,
    initial_values: tuple[str, ...],
) -> Candidate | None:
    entity_types = _zero_shot_candidate_types(context, initial_values)
    if not entity_types:
        return None

    classifier = _load_zero_shot_classifier(
        session.config.zero_shot_model_name,
        session.config.zero_shot_device,
    )
    for entity_type in entity_types:
        candidate = _evaluate_zero_shot_entity(
            session,
            context,
            classifier,
            entity_type,
            initial_values,
        )
        if candidate is not None:
            return candidate
    return None


def _zero_shot_candidate_types(
    context: ColumnContext,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    candidates: list[str] = []
    if _should_try_full_name(context, values):
        candidates.append(FULL_NAME)
    if _should_try_first_name(context, values):
        candidates.append(FIRST_NAME)
    if _should_try_last_name(context, values):
        candidates.append(LAST_NAME)
    if _should_try_address(context, values):
        candidates.append(ADDRESS)
    return tuple(candidates)


def _evaluate_zero_shot_entity(
    session: ScanSession,
    context: ColumnContext,
    classifier: Callable[..., object],
    entity_type: str,
    initial_values: tuple[str, ...],
) -> Candidate | None:
    stats = _score_zero_shot_values(
        classifier,
        initial_values,
        entity_type=entity_type,
        positive_threshold=session.config.zero_shot_positive_threshold,
        batch_size=session.config.zero_shot_batch_size,
    )
    if stats.sampled_count == 0 or stats.match_rate < session.config.zero_shot_continue_threshold:
        return None

    expanded_limit = min(
        session.config.sample_limit,
        session.config.zero_shot_expanded_sample_limit,
    )
    if expanded_limit > len(initial_values):
        expanded_values = _sample_semantic_values(
            session,
            context,
            limit=expanded_limit,
        )
        stats = _score_zero_shot_values(
            classifier,
            expanded_values,
            entity_type=entity_type,
            positive_threshold=session.config.zero_shot_positive_threshold,
            batch_size=session.config.zero_shot_batch_size,
        )

    confidence_level = _zero_shot_confidence(context, entity_type, stats)
    if confidence_level is None:
        return None

    return _semantic_candidate(
        context,
        entity_type=entity_type,
        method="zero_shot",
        matched_count=stats.matched_count,
        sampled_count=stats.sampled_count,
        match_rate=stats.match_rate,
        confidence_level=confidence_level,
        avg_score=stats.avg_score,
        model_name=session.config.zero_shot_model_name,
        positive_threshold=session.config.zero_shot_positive_threshold,
        continue_threshold=session.config.zero_shot_continue_threshold,
    )


@dataclass(frozen=True)
class ZeroShotStats:
    sampled_count: int
    matched_count: int
    match_rate: float
    avg_score: float


def _score_zero_shot_values(
    classifier: Callable[..., object],
    values: tuple[str, ...],
    *,
    entity_type: str,
    positive_threshold: float,
    batch_size: int,
) -> ZeroShotStats:
    if not values:
        return ZeroShotStats(
            sampled_count=0,
            matched_count=0,
            match_rate=0.0,
            avg_score=0.0,
        )

    label = ZERO_SHOT_LABELS[entity_type]
    raw_outputs = classifier(
        list(values),
        candidate_labels=[label],
        hypothesis_template="Este valor corresponde a {}.",
        multi_label=True,
        batch_size=batch_size,
        truncation=True,
    )
    outputs = [raw_outputs] if isinstance(raw_outputs, dict) else list(raw_outputs)
    scores = [float(output["scores"][0]) for output in outputs]
    matched_count = sum(1 for score in scores if score >= positive_threshold)
    sampled_count = len(scores)
    return ZeroShotStats(
        sampled_count=sampled_count,
        matched_count=matched_count,
        match_rate=matched_count / sampled_count if sampled_count else 0.0,
        avg_score=sum(scores) / sampled_count if sampled_count else 0.0,
    )


def _zero_shot_confidence(
    context: ColumnContext,
    entity_type: str,
    stats: ZeroShotStats,
) -> str | None:
    strong_context = _strong_zero_shot_context(context, entity_type)
    if (
        strong_context
        and stats.sampled_count >= 50
        and stats.match_rate >= VERY_CONFIDENT_THRESHOLD
        and stats.avg_score >= 0.80
    ):
        return VERY_CONFIDENT
    if stats.match_rate >= 0.65:
        return CONFIDENT
    if stats.match_rate >= 0.50:
        return PROBABLE
    if strong_context and stats.match_rate >= 0.30 and stats.avg_score >= 0.55:
        return PROBABLE
    return None


def _strong_zero_shot_context(context: ColumnContext, entity_type: str) -> bool:
    if entity_type in {FULL_NAME, FIRST_NAME, LAST_NAME}:
        return context.strong_name_context or context.broad_name_context
    if entity_type == ADDRESS:
        return context.address_context
    return False


@lru_cache(maxsize=4)
def _load_zero_shot_classifier(model_name: str, device: str) -> Callable[..., object]:
    try:
        import torch  # type: ignore[import-not-found]
        from transformers import (  # type: ignore[import-not-found]
            AutoModelForSequenceClassification,
            AutoTokenizer,
            pipeline,
        )
    except Exception as exc:  # pragma: no cover - exercised through unit monkeypatches.
        raise ZeroShotDiscoveryError(
            "Zero-shot discovery dependencies are not available.",
            safe_context={"model_name": model_name},
        ) from exc

    try:
        pipeline_device = _pipeline_device(device, torch)
        tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(
            model_name,
            local_files_only=True,
        )
        return pipeline(
            "zero-shot-classification",
            model=model,
            tokenizer=tokenizer,
            device=pipeline_device,
        )
    except ZeroShotDiscoveryError:
        raise
    except Exception as exc:  # pragma: no cover - exercised through unit monkeypatches.
        raise ZeroShotDiscoveryError(
            "Zero-shot model is not available locally.",
            safe_context={"model_name": model_name},
        ) from exc


def _pipeline_device(device: str, torch_module: object) -> object:
    normalized = device.strip().casefold()
    if normalized == "auto":
        cuda = getattr(torch_module, "cuda", None)
        if cuda is not None and cuda.is_available():
            return 0
        backends = getattr(torch_module, "backends", None)
        mps = getattr(backends, "mps", None) if backends is not None else None
        if mps is not None and mps.is_available():
            return torch_module.device("mps")
        return -1
    if normalized == "cpu":
        return -1
    if normalized == "cuda":
        return 0
    if normalized == "mps":
        return torch_module.device("mps")
    if normalized.isdigit():
        return int(normalized)
    raise ZeroShotDiscoveryError(
        "Unsupported zero-shot device.",
        safe_context={"device": device},
    )


def _propagate_by_column_name(
    session: ScanSession,
    contexts: list[ColumnContext],
    findings_by_key: dict[tuple[str, str, str], DiscoveredPII],
) -> None:
    direct_by_name: dict[str, DiscoveredPII] = {}
    for context in contexts:
        key = _column_key(context.table, context.column)
        finding = findings_by_key.get(key)
        if finding is None or finding.propagated_from is not None:
            continue
        if not context.specific_name:
            continue
        direct_by_name.setdefault(context.normalized_name, finding)

    for context in contexts:
        key = _column_key(context.table, context.column)
        existing_finding = findings_by_key.get(key)
        if existing_finding is not None and existing_finding.confidence_level != PROBABLE:
            continue
        source_finding = direct_by_name.get(context.normalized_name)
        if source_finding is None:
            continue
        if (
            existing_finding is not None
            and _finding_ref(existing_finding) == _finding_ref(source_finding)
        ):
            continue

        findings_by_key[key] = _propagated_finding(
            session,
            context,
            source_finding,
            method="propagated_column_name",
            propagated_from=_finding_ref(source_finding),
        )


def _propagate_by_foreign_key(
    session: ScanSession,
    contexts: list[ColumnContext],
    findings_by_key: dict[tuple[str, str, str], DiscoveredPII],
) -> None:
    direct_findings = {
        key: finding
        for key, finding in findings_by_key.items()
        if finding.propagated_from is None
    }

    for context in contexts:
        key = _column_key(context.table, context.column)
        existing_finding = findings_by_key.get(key)
        if (
            existing_finding is not None
            and existing_finding.confidence_level != PROBABLE
        ) or not context.column.foreign_key:
            continue

        source_finding = _match_foreign_key(context.column.foreign_key, direct_findings)
        if source_finding is None:
            continue

        findings_by_key[key] = _propagated_finding(
            session,
            context,
            source_finding,
            method="propagated_foreign_key",
            propagated_from=_finding_ref(source_finding),
        )


def _match_foreign_key(
    foreign_key: str,
    direct_findings: dict[tuple[str, str, str], DiscoveredPII],
) -> DiscoveredPII | None:
    normalized_fk = normalize_identifier(foreign_key)
    for finding in direct_findings.values():
        parts = [
            normalize_identifier(part)
            for part in (finding.schema_name, finding.table_name, finding.column_name)
            if part
        ]
        table_column = "_".join(parts[-2:])
        schema_table_column = "_".join(parts)
        if normalized_fk.endswith(schema_table_column) or normalized_fk.endswith(table_column):
            return finding
        if parts[-2] in normalized_fk and parts[-1] in normalized_fk:
            return finding
    return None


def _propagated_finding(
    session: ScanSession,
    context: ColumnContext,
    source_finding: DiscoveredPII,
    *,
    method: str,
    propagated_from: str,
) -> DiscoveredPII:
    confidence_level = (
        CONFIDENT
        if source_finding.confidence_level in {VERY_CONFIDENT, CONFIDENT}
        else PROBABLE
    )
    confidence = _confidence_score(confidence_level, 0.0)
    evidence_summary = _evidence_summary(
        context,
        entity_type=source_finding.pii_type,
        method=method,
        matched_count=0,
        sampled_count=0,
        match_rate=0.0,
        propagated=True,
    )
    return DiscoveredPII(
        source_name=session.profile.source_name,
        source_type=session.profile.source_type,
        schema_name=context.table.schema_name,
        table_name=context.table.table_name,
        column_name=context.column.column_name,
        pii_type=source_finding.pii_type,
        confidence=confidence,
        confidence_level=confidence_level,
        detection_method=method,
        evidence_summary=evidence_summary,
        sampled_count=0,
        matched_count=0,
        is_primary_key=context.column.is_primary_key,
        foreign_key=context.column.foreign_key,
        propagated_from=propagated_from,
    )


def _evidence_summary(
    context: ColumnContext,
    *,
    entity_type: str,
    method: str,
    matched_count: int,
    sampled_count: int,
    match_rate: float,
    propagated: bool,
    avg_score: float | None = None,
    model_name: str | None = None,
    positive_threshold: float | None = None,
    continue_threshold: float | None = None,
) -> str:
    parts = [
        f"entity={entity_type}",
        f"method={method}",
        f"matched_count={matched_count}",
        f"sampled_count={sampled_count}",
        f"match_rate={match_rate:.2f}",
    ]
    if entity_type in context.header_types:
        parts.append("column_name_match=true")
    if context.data_type:
        parts.append(f"data_type={context.data_type}")
    if context.column.is_primary_key:
        parts.append("primary_key=true")
    if context.column.foreign_key:
        parts.append("foreign_key=true")
    if propagated:
        parts.append("propagated=true")
    if avg_score is not None:
        parts.append(f"avg_score={avg_score:.2f}")
    if model_name:
        parts.append(f"model={model_name}")
    if positive_threshold is not None:
        parts.append(f"positive_threshold={positive_threshold:.2f}")
    if continue_threshold is not None:
        parts.append(f"continue_threshold={continue_threshold:.2f}")
    return "; ".join(parts)


def _confidence_score(confidence_level: str, match_rate: float) -> float:
    if confidence_level == VERY_CONFIDENT:
        return round(min(0.99, 0.95 + max(0.0, match_rate - 0.85) * 0.1), 4)
    if confidence_level == CONFIDENT:
        return round(min(0.89, 0.75 + match_rate * 0.1), 4)
    return round(min(0.69, 0.55 + match_rate * 0.1), 4)


def _confidence_rank(confidence_level: str) -> int:
    return {VERY_CONFIDENT: 3, CONFIDENT: 2, PROBABLE: 1}[confidence_level]


def _entity_priority(entity_type: str) -> int:
    try:
        return len(ENTITY_ORDER) - ENTITY_ORDER.index(entity_type)
    except ValueError:
        return 0


def _column_key(table: TableProfile, column: ColumnProfile) -> tuple[str, str, str]:
    return (table.schema_name or "", table.table_name, column.column_name)


def _finding_ref(finding: DiscoveredPII) -> str:
    parts = [
        part
        for part in (finding.schema_name, finding.table_name, finding.column_name)
        if part
    ]
    return ".".join(parts)


def normalize_identifier(value: str | None) -> str:
    if value is None:
        return ""
    ascii_value = unicodedata.normalize("NFKD", str(value))
    ascii_value = "".join(
        char for char in ascii_value if not unicodedata.combining(char)
    )
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_").casefold()


def normalize_text_key(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        char for char in ascii_value if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^A-Za-z0-9]+", " ", ascii_value).strip().casefold()
    return " ".join(normalized.split())


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def _looks_like_technical_string_column(
    context: ColumnContext,
    values: tuple[str, ...],
) -> bool:
    if context.online_identifier_context:
        return False
    if context.technical_string_context:
        return True
    if not values:
        return False
    technical_count = sum(1 for value in values if _looks_like_technical_string(value))
    return technical_count / len(values) >= 0.80


def _looks_like_technical_string(value: str) -> bool:
    text = value.strip()
    if not text or any(char.isspace() for char in text):
        return False
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        text,
    ):
        return True
    if re.fullmatch(r"[0-9a-fA-F]{24,128}", text):
        return True
    if len(text) >= 32 and re.fullmatch(r"[A-Za-z0-9_+/=-]+", text):
        return True
    return False


def _should_try_full_name(context: ColumnContext, values: tuple[str, ...]) -> bool:
    if context.organization_context or not values:
        return False
    has_context = (
        FULL_NAME in context.header_types
        or context.broad_name_context
        or context.normalized_name in {"nombre", "nombres", "name"}
    )
    if not has_context:
        return False
    return _name_value_ratio(values, min_tokens=2, max_tokens=5) >= 0.50


def _should_try_first_name(context: ColumnContext, values: tuple[str, ...]) -> bool:
    if context.organization_context or not values:
        return False
    if (
        context.normalized_name in HEADER_PHRASES[FULL_NAME]
        and context.normalized_name not in HEADER_PHRASES[FIRST_NAME]
    ):
        return False
    has_context = (
        FIRST_NAME in context.header_types
        or context.broad_name_context
        or context.normalized_name in {"nombre", "nombres", "name"}
    )
    if not has_context:
        return False
    return _name_value_ratio(values, min_tokens=1, max_tokens=2) >= 0.50


def _should_try_last_name(context: ColumnContext, values: tuple[str, ...]) -> bool:
    if context.organization_context or not values:
        return False
    if (
        context.normalized_name in HEADER_PHRASES[FULL_NAME]
        and context.normalized_name not in HEADER_PHRASES[LAST_NAME]
    ):
        return False
    has_context = LAST_NAME in context.header_types or context.broad_name_context
    if not has_context:
        return False
    return _name_value_ratio(values, min_tokens=1, max_tokens=2) >= 0.50


def _should_try_address(context: ColumnContext, values: tuple[str, ...]) -> bool:
    if not values:
        return False
    if context.address_context:
        return _textual_value_ratio(values) >= 0.50
    return sum(1 for value in values if _address_like_value(value)) / len(values) >= 0.30


def _name_value_ratio(
    values: tuple[str, ...],
    *,
    min_tokens: int,
    max_tokens: int,
) -> float:
    return (
        sum(
            1
            for value in values
            if _name_like_value(value, min_tokens=min_tokens, max_tokens=max_tokens)
        )
        / len(values)
        if values
        else 0.0
    )


def _name_like_value(value: str, *, min_tokens: int, max_tokens: int) -> bool:
    if re.search(r"\d", value):
        return False
    normalized = normalize_text_key(value)
    tokens = normalized.split()
    if not min_tokens <= len(tokens) <= max_tokens:
        return False
    if len(normalized) > 80:
        return False
    return all(re.fullmatch(r"[a-z]+", token) and len(token) >= 2 for token in tokens)


def _textual_value_ratio(values: tuple[str, ...]) -> float:
    if not values:
        return 0.0
    textual_count = sum(1 for value in values if re.search(r"[A-Za-z]", value))
    return textual_count / len(values)


def validate_address_heuristic(value: str) -> bool:
    return _address_like_value(value) and bool(re.search(r"\d", value))


def _address_like_value(value: str) -> bool:
    normalized = normalize_text_key(value)
    tokens = set(normalized.split())
    return bool(tokens & ADDRESS_VALUE_TOKENS)


def matches_entity_regex(entity_type: str, value: str) -> bool:
    pattern = REGEX_VALUE_PATTERNS.get(entity_type)
    if pattern is None:
        return True
    return re.fullmatch(pattern, value.strip()) is not None


def validate_rut(value: str) -> bool:
    if not matches_entity_regex(RUT, value):
        return False
    clean = re.sub(r"[^0-9Kk]", "", value).upper()
    if not re.fullmatch(r"\d{7,8}[0-9K]", clean):
        return False

    body = clean[:-1]
    expected = clean[-1]
    total = 0
    multiplier = 2
    for digit in reversed(body):
        total += int(digit) * multiplier
        multiplier = 2 if multiplier == 7 else multiplier + 1

    remainder = 11 - (total % 11)
    if remainder == 11:
        calculated = "0"
    elif remainder == 10:
        calculated = "K"
    else:
        calculated = str(remainder)
    return calculated == expected


def validate_payment_card(value: str) -> bool:
    if not matches_entity_regex(PAYMENT_CARD, value):
        return False
    digits = [int(ch) for ch in digits_only(value)]
    if not 13 <= len(digits) <= 19:
        return False
    if len({str(digit) for digit in digits}) == 1:
        return False

    checksum = 0
    parity = len(digits) % 2
    for idx, digit in enumerate(digits):
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def validate_email(value: str) -> bool:
    if not matches_entity_regex(EMAIL, value):
        return False
    email = value.strip()
    if any(ch.isspace() for ch in email) or email.count("@") != 1:
        return False
    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain or "." not in domain:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    try:
        normalized_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9.-]+", normalized_domain))


def validate_phone_cl(value: str) -> bool:
    if not matches_entity_regex(PHONE_CL, value):
        return False
    raw = value.strip()
    digits = digits_only(raw)
    has_country_prefix = (
        raw.startswith("+56") or raw.startswith("0056") or digits.startswith("56")
    )

    if digits.startswith("0056"):
        digits = "56" + digits[4:]
    if digits.startswith("56"):
        national = digits[2:]
    else:
        national = digits

    if len(national) != 9:
        return False
    if national[0] not in "9234567":
        return False
    if not has_country_prefix and national[0] != "9":
        return False
    return True


def validate_license_plate(value: str) -> bool:
    if not matches_entity_regex(LICENSE_PLATE, value):
        return False
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    patterns = (
        r"[A-Z]{2}[A-Z]{2}\d{2}",
        r"[A-Z]{2}\d{2}\d{2}",
        r"[A-Z]{3}\d{2}",
        r"[A-Z]{4,5}\d",
    )
    return any(re.fullmatch(pattern, cleaned) for pattern in patterns)


def validate_date(value: str, context: ColumnContext) -> bool:
    if not matches_entity_regex(DATE, value):
        return False
    if context.negative_date_context and not context.date_name_context:
        return False
    return parse_date(value) is not None


def parse_date(value: str, *, today: date | None = None) -> date | None:
    today = today or date.today()
    text = value.strip()
    if not text:
        return None

    text_without_time_separator = text.replace("T", " ")
    if re.search(r"[A-Za-z]", text_without_time_separator):
        return None

    iso_match = re.fullmatch(
        r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})"
        r"(?:\s+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",
        text_without_time_separator,
    )
    if iso_match:
        return _valid_date(
            int(iso_match.group(1)),
            int(iso_match.group(2)),
            int(iso_match.group(3)),
            today=today,
        )

    numeric_match = re.fullmatch(
        r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})"
        r"(?:\s+\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?",
        text_without_time_separator,
    )
    if not numeric_match:
        return None

    first = int(numeric_match.group(1))
    second = int(numeric_match.group(2))
    year = _expand_year(int(numeric_match.group(3)), today=today)
    candidates = [
        candidate
        for candidate in (
            _valid_date(year, second, first, today=today),
            _valid_date(year, first, second, today=today),
        )
        if candidate is not None
    ]
    if not candidates:
        return None
    return candidates[0]


def parse_text_date(value: str, *, today: date | None = None) -> date | None:
    today = today or date.today()
    normalized = normalize_text_key(value)
    if not normalized:
        return None

    day_first = re.fullmatch(
        r"(\d{1,2})\s+(?:de\s+)?([a-z]+)\s+(?:de\s+)?(\d{2}|\d{4})",
        normalized,
    )
    if day_first:
        day = int(day_first.group(1))
        month = TEXT_MONTHS.get(day_first.group(2))
        year = _expand_year(int(day_first.group(3)), today=today)
        return _valid_date(year, month or 0, day, today=today)

    month_first = re.fullmatch(
        r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{2}|\d{4})",
        normalized,
    )
    if month_first:
        month = TEXT_MONTHS.get(month_first.group(1))
        day = int(month_first.group(2))
        year = _expand_year(int(month_first.group(3)), today=today)
        return _valid_date(year, month or 0, day, today=today)

    return None


def _expand_year(year: int, *, today: date) -> int:
    if year >= 100:
        return year
    current_two_digits = today.year % 100
    century = 2000 if year <= current_two_digits else 1900
    return century + year


def _valid_date(year: int, month: int, day: int, *, today: date) -> date | None:
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    if parsed.year < 1900 or parsed > today:
        return None
    return parsed


def validate_afp(value: str, context: ColumnContext) -> bool:
    normalized = normalize_text_key(value)
    if normalized in GENERIC_AFP_VALUES and AFP not in context.header_types:
        return False
    return normalized in AFP_VALUES


def validate_health_system(value: str, context: ColumnContext) -> bool:
    normalized = normalize_text_key(value)
    if normalized in GENERIC_HEALTH_VALUES and HEALTH_SYSTEM not in context.header_types:
        return False
    return normalized in HEALTH_SYSTEM_VALUES


def validate_gender_identity(value: str, context: ColumnContext) -> bool:
    normalized = normalize_text_key(value)
    if (
        normalized in CONTEXT_REQUIRED_GENDER_VALUES
        and GENDER_IDENTITY not in context.header_types
    ):
        return False
    return normalized in GENDER_IDENTITY_VALUES


def validate_religion(value: str, context: ColumnContext) -> bool:
    return normalize_text_key(value) in RELIGION_VALUES


def validate_sexual_orientation(value: str, context: ColumnContext) -> bool:
    return normalize_text_key(value) in SEXUAL_ORIENTATION_VALUES


AFP_VALUES = frozenset(normalize_text_key(value) for value in AFP_VALUES_RAW)
HEALTH_SYSTEM_VALUES = frozenset(
    normalize_text_key(value) for value in HEALTH_SYSTEM_VALUES_RAW
)
SEXUAL_ORIENTATION_VALUES = frozenset(
    normalize_text_key(value) for value in SEXUAL_ORIENTATION_VALUES_RAW
)
GENDER_IDENTITY_VALUES = frozenset(
    normalize_text_key(value) for value in GENDER_IDENTITY_VALUES_RAW
)
RELIGION_VALUES = frozenset(normalize_text_key(value) for value in RELIGION_VALUES_RAW)

VALIDATORS: dict[str, Validator] = {
    RUT: lambda value, context: validate_rut(value),
    PAYMENT_CARD: lambda value, context: validate_payment_card(value),
    DATE: validate_date,
    LICENSE_PLATE: lambda value, context: validate_license_plate(value),
    EMAIL: lambda value, context: validate_email(value),
    HEALTH_SYSTEM: validate_health_system,
    AFP: validate_afp,
    SEXUAL_ORIENTATION: validate_sexual_orientation,
    GENDER_IDENTITY: validate_gender_identity,
    RELIGION_OR_BELIEF: validate_religion,
    PHONE_CL: lambda value, context: validate_phone_cl(value),
}
