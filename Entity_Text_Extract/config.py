from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_RESULTS_DIR = Path(
    os.getenv("PII_ENTITY_OUTPUT_DIR", "/tmp/pii-entity-results")
).expanduser()
DEFAULT_TEMP_DIR = Path(
    os.getenv("TEXT_MATERIALIZE_SCRATCH_DIR", "/tmp/pii-text-materialization")
).expanduser()


@dataclass(frozen=True)
class RegexPatternSpec:
    entity_type: str
    raw_entity_type: str
    regex: str
    score: float
    group: int = 0


@dataclass(frozen=True)
class EntityDetectionConfig:
    spacy_model: str = "es_core_news_lg"
    gliner2_model: str = "fastino/gliner2-privacy-filter-PII-multi"
    medical_model: str = "HUMADEX/spanish_medical_ner"
    gliner2_use_gpu: bool = False
    model_device: str | None = None
    enable_presidio: bool = True
    enable_deterministic: bool = True
    enable_gliner2: bool = True
    enable_medical: bool = True
    model_batch_size: int = 8
    language: str = "es"

    @classmethod
    def from_env(cls) -> "EntityDetectionConfig":
        return cls(
            spacy_model=os.getenv("PII_ENTITY_SPACY_MODEL", "es_core_news_lg"),
            gliner2_model=os.getenv(
                "PII_ENTITY_GLINER2_MODEL",
                "fastino/gliner2-privacy-filter-PII-multi",
            ),
            medical_model=os.getenv(
                "PII_ENTITY_MEDICAL_MODEL",
                "HUMADEX/spanish_medical_ner",
            ),
            gliner2_use_gpu=optional_bool_env("PII_ENTITY_GLINER2_USE_GPU", False),
            model_device=optional_text_env("PII_ENTITY_MODEL_DEVICE"),
            enable_presidio=optional_bool_env("PII_ENTITY_ENABLE_PRESIDIO", True),
            enable_deterministic=optional_bool_env(
                "PII_ENTITY_ENABLE_DETERMINISTIC",
                True,
            ),
            enable_gliner2=optional_bool_env("PII_ENTITY_ENABLE_GLINER2", True),
            enable_medical=optional_bool_env("PII_ENTITY_ENABLE_MEDICAL", True),
            model_batch_size=optional_int_env("PII_ENTITY_MODEL_BATCH_SIZE", 8),
        )


REGEX_PATTERNS: tuple[RegexPatternSpec, ...] = (
    RegexPatternSpec(
        entity_type="RUT",
        raw_entity_type="RUT_REGEX",
        regex=r"(?<![A-Za-z0-9])(?:\d{1,2}\.?\d{3}\.?\d{3}\s*-\s*[0-9Kk])(?![A-Za-z0-9])",
        score=0.99,
    ),
    RegexPatternSpec(
        entity_type="EMAIL",
        raw_entity_type="EMAIL_REGEX",
        regex=r"(?i)(?<![A-Za-z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Za-z0-9._%+-])",
        score=0.99,
    ),
    RegexPatternSpec(
        entity_type="PHONE_CL",
        raw_entity_type="PHONE_CL_REGEX",
        regex=r"(?<!\d)(?:(?:\+?56|0056)[\s.-]*)?(?:9|[2-7])(?:[\s.-]*\d){8}(?![\s.-]*\d)",
        score=0.95,
    ),
    RegexPatternSpec(
        entity_type="PAYMENT_CARD",
        raw_entity_type="PAYMENT_CARD_REGEX",
        regex=r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)",
        score=0.99,
    ),
    RegexPatternSpec(
        entity_type="LICENSE_PLATE",
        raw_entity_type="LICENSE_PLATE_REGEX",
        regex=r"(?<![A-Za-z0-9])(?:[A-Z]{2}[-\s]?[A-Z]{2}[-\s]?\d{2}|[A-Z]{2}[-\s]?\d{2}[-\s]?\d{2}|[A-Z]{3}[-\s]?\d{2}|[A-Z]{4,5}[-\s]?\d)(?![A-Za-z0-9])",
        score=0.90,
    ),
    RegexPatternSpec(
        entity_type="GENDER_IDENTITY",
        raw_entity_type="GENDER_IDENTITY_CONTEXT_REGEX",
        regex=r"(?i)\b(?:sexo|g[eé]nero|identidad\s+de\s+g[eé]nero)\s*(?:[:=#-]|\bes\b)?\s*(H|M|F|hombre|mujer|masculino|femenino)\b",
        score=0.96,
        group=1,
    ),
    RegexPatternSpec(
        entity_type="DOCUMENT_NUMBER",
        raw_entity_type="DOCUMENT_NUMBER_CONTEXT_REGEX",
        regex=r"(?i)\b(?:numero\s+de\s+documento|num\.?\s+documento|documento|carnet)\s*(?:n[ro\u00ba\u00b0]*\.?\s*)?[:#-]?\s*(\d{1,3}(?:\.\d{3}){2,3}|\d{7,12})\b",
        score=0.88,
        group=1,
    ),
    RegexPatternSpec(
        entity_type="SECRET",
        raw_entity_type="SECRET_CONTEXT_REGEX",
        regex=r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|bearer\s+token)\b\s*[:=]\s*([A-Za-z0-9_./+=-]{6,})",
        score=0.99,
        group=1,
    ),
)


DENY_LISTS: dict[str, list[str]] = {
    "HEALTH_SYSTEM": [
        "Banmedica S.A.",
        "Banmedica",
        "Isalud Ltda.",
        "Isalud",
        "Colmena Golden Cross S.A.",
        "Colmena Golden Cross",
        "Consalud S.A.",
        "Consalud",
        "Cruz Blanca S.A.",
        "Cruz Blanca",
        "Cruz del Norte Ltda.",
        "Cruz del Norte",
        "Nueva MasVida S.A.",
        "Nueva MasVida",
        "Fundacion Ltda.",
        "Fundacion",
        "Vida Tres S.A.",
        "Vida Tres",
        "Esencial S.A.",
        "Fonasa",
    ],
    "PENSION_SYSTEM": [
        "AFP Capital",
        "AFP Cuprum",
        "AFP Habitat",
        "AFP Modelo",
        "AFP Planvital",
        "AFP Provida",
        "AFP Uno",
        "AFP UNO",
    ],
    "HEALTH_DATA": [
        "asma",
        "cancer",
        "diabetes",
        "hipertension",
        "leucemia",
        "vih",
        "sida",
        "depresion",
        "ansiedad",
    ],
    "RELIGION_OR_BELIEF": [
        "agnostico",
        "agnostica",
        "agnosticismo",
        "ateo",
        "atea",
        "ateismo",
        "bahai",
        "catolico",
        "catolica",
        "cristiano",
        "cristiana",
        "cristianismo",
        "evangelico",
        "evangelica",
        "hindu",
        "hinduismo",
        "hinduista",
        "islam",
        "islamica",
        "islamico",
        "judio",
        "judia",
        "judaismo",
        "musulman",
        "musulmana",
        "budista",
        "budismo",
        "mormon",
        "mormona",
        "ninguna religion",
        "no tengo religion",
        "no tiene religion",
        "no profesa religion",
        "protestante",
        "religion catolica",
        "religion evangelica",
        "sij",
        "sin religion",
        "sikh",
        "testigo de jehova",
        "testigos de jehova",
    ],
    "SEXUAL_ORIENTATION": [
        "heterosexual",
        "homosexual",
        "bisexual",
        "gay",
        "lesbiana",
        "pansexual",
        "asexual",
    ],
    "GENDER_IDENTITY": [
        "hombre",
        "mujer",
        "masculino",
        "femenino",
    ],
    "MARITAL_STATUS": [
        "acuerdo de union civil",
        "anulado",
        "anulada",
        "casado",
        "casada",
        "con acuerdo de union civil",
        "conviviente",
        "conviviente civil",
        "divorciado",
        "divorciada",
        "pareja de hecho",
        "separado",
        "separada",
        "separado judicialmente",
        "separada judicialmente",
        "soltero",
        "soltera",
        "union civil",
        "union libre",
        "viudo",
        "viuda",
    ],
    "POLITICAL_OR_UNION_AFFILIATION": [
        "militante",
        "afiliado sindical",
        "sindicato",
        "partido politico",
    ],
    "BIOMETRIC_OR_BIOLOGICAL": [
        "huella digital",
        "iris",
        "retina",
        "adn",
        "reconocimiento facial",
        "perfil biologico",
    ],
}


GLINER_LABELS: tuple[str, ...] = (
    "person",
    "full_name",
    "first_name",
    "middle_name",
    "last_name",
    "date_of_birth",
    "email",
    "phone_number",
    "address",
    "street_address",
    "city",
    "state_or_region",
    "postal_code",
    "country",
    "government_id",
    "national_id_number",
    "passport_number",
    "drivers_license_number",
    "license_number",
    "tax_id",
    "tax_number",
    "bank_account",
    "account_number",
    "payment_card",
    "card_number",
    "card_expiry",
    "card_cvv",
    "username",
    "ip_address",
    "account_id",
    "sensitive_account_id",
    "password",
    "secret",
    "api_key",
    "access_token",
    "recovery_code",
    "sensitive_date",
    "document_date",
    "expiration_date",
    "transaction_date",
)


GLINER_LABEL_MAPPING: dict[str, str] = {
    "person": "GLINER2_PERSON",
    "full_name": "GLINER2_FULL_NAME",
    "first_name": "GLINER2_FIRST_NAME",
    "middle_name": "GLINER2_MIDDLE_NAME",
    "last_name": "GLINER2_LAST_NAME",
    "date_of_birth": "GLINER2_DATE_OF_BIRTH",
    "sensitive_date": "GLINER2_SENSITIVE_DATE",
    "document_date": "GLINER2_DOCUMENT_DATE",
    "expiration_date": "GLINER2_EXPIRATION_DATE",
    "transaction_date": "GLINER2_TRANSACTION_DATE",
    "email": "GLINER2_EMAIL",
    "phone_number": "GLINER2_PHONE_NUMBER",
    "address": "GLINER2_ADDRESS",
    "street_address": "GLINER2_STREET_ADDRESS",
    "city": "GLINER2_CITY",
    "state_or_region": "GLINER2_STATE_OR_REGION",
    "postal_code": "GLINER2_POSTAL_CODE",
    "country": "GLINER2_COUNTRY",
    "government_id": "GLINER2_GOVERNMENT_ID",
    "national_id_number": "GLINER2_NATIONAL_ID_NUMBER",
    "passport_number": "GLINER2_PASSPORT_NUMBER",
    "drivers_license_number": "GLINER2_DRIVERS_LICENSE_NUMBER",
    "license_number": "GLINER2_LICENSE_NUMBER",
    "tax_id": "GLINER2_TAX_ID",
    "tax_number": "GLINER2_TAX_NUMBER",
    "bank_account": "GLINER2_BANK_ACCOUNT",
    "account_number": "GLINER2_ACCOUNT_NUMBER",
    "payment_card": "GLINER2_PAYMENT_CARD",
    "card_number": "GLINER2_CARD_NUMBER",
    "card_expiry": "GLINER2_CARD_EXPIRY",
    "card_cvv": "GLINER2_CARD_CVV",
    "username": "GLINER2_USERNAME",
    "ip_address": "GLINER2_IP_ADDRESS",
    "account_id": "GLINER2_ACCOUNT_ID",
    "sensitive_account_id": "GLINER2_SENSITIVE_ACCOUNT_ID",
    "password": "GLINER2_PASSWORD",
    "secret": "GLINER2_SECRET",
    "api_key": "GLINER2_API_KEY",
    "access_token": "GLINER2_ACCESS_TOKEN",
    "recovery_code": "GLINER2_RECOVERY_CODE",
}


def load_environment(env_file: str | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install python-dotenv with "
            "`python -m pip install -r requirements.txt`."
        ) from exc

    dotenv_path = Path(env_file).expanduser() if env_file else DEFAULT_ENV_FILE
    load_dotenv(dotenv_path=dotenv_path, override=False)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def optional_text_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def optional_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    parsed = int(value)
    if parsed < 1:
        raise RuntimeError(f"{name} must be greater than or equal to 1")
    return parsed
