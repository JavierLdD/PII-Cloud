from __future__ import annotations

import calendar
import ipaddress
import re
from urllib.parse import urlparse


CANONICAL_ENTITY_TYPES = {
    "AFP": "PENSION_SYSTEM",
    "PENSION_SYSTEM": "PENSION_SYSTEM",
    "SISTEMA_DE_SALUD": "HEALTH_SYSTEM",
    "HEALTH_SYSTEM": "HEALTH_SYSTEM",
    "HEALTH_DATA": "MEDICAL_PROBLEM",
    "GENDER": "GENDER",
    "GENDER_IDENTITY": "GENDER",
    "MARITAL_STATUS": "MARITAL_STATUS",
    "RELIGION_OR_BELIEF": "RELIGION_OR_BELIEF",
    "SEXUAL_ORIENTATION": "SEXUAL_ORIENTATION",
    "POLITICAL_OR_UNION_AFFILIATION": "POLITICAL_OR_UNION_AFFILIATION",
    "BIOMETRIC_OR_BIOLOGICAL": "BIOMETRIC_OR_BIOLOGICAL",
    "EMAIL": "EMAIL",
    "EMAIL_ADDRESS": "EMAIL",
    "EMAIL_REGEX": "EMAIL",
    "GLINER2_EMAIL": "EMAIL",
    "TELEFONO": "PHONE",
    "PHONE": "PHONE",
    "PHONE_CL": "PHONE",
    "PHONE_NUMBER": "PHONE",
    "GLINER2_PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "PAYMENT_CARD",
    "PAYMENT_CARD": "PAYMENT_CARD",
    "PAYMENT_CARD_REGEX": "PAYMENT_CARD",
    "GLINER2_PAYMENT_CARD": "PAYMENT_CARD",
    "GLINER2_CARD_NUMBER": "PAYMENT_CARD",
    "PATENTE": "LICENSE_PLATE",
    "PATENTE_NUEVA": "LICENSE_PLATE",
    "LICENSE_PLATE": "LICENSE_PLATE",
    "LICENSE_PLATE_REGEX": "LICENSE_PLATE",
    "RUT": "RUT",
    "RUT_REGEX": "RUT",
    "PERSON": "NAME",
    "FULL_NAME": "NAME",
    "FIRST_NAME": "NAME",
    "MIDDLE_NAME": "NAME",
    "LAST_NAME": "NAME",
    "GLINER2_PERSON": "NAME",
    "GLINER2_FULL_NAME": "NAME",
    "GLINER2_FIRST_NAME": "NAME",
    "GLINER2_MIDDLE_NAME": "NAME",
    "GLINER2_LAST_NAME": "NAME",
    "DATE": "DATE",
    "DATE_TIME": "DATE",
    "DATE_OF_BIRTH": "DATE",
    "SENSITIVE_DATE": "DATE",
    "DOCUMENT_DATE": "DATE",
    "EXPIRATION_DATE": "DATE",
    "TRANSACTION_DATE": "DATE",
    "GLINER2_DATE_OF_BIRTH": "DATE",
    "GLINER2_SENSITIVE_DATE": "DATE",
    "GLINER2_DOCUMENT_DATE": "DATE",
    "GLINER2_EXPIRATION_DATE": "DATE",
    "GLINER2_TRANSACTION_DATE": "DATE",
    "ADDRESS": "ADDRESS",
    "STREET_ADDRESS": "ADDRESS",
    "POSTAL_CODE": "ADDRESS",
    "GLINER2_ADDRESS": "ADDRESS",
    "GLINER2_STREET_ADDRESS": "ADDRESS",
    "GLINER2_POSTAL_CODE": "ADDRESS",
    "LOCATION": "LOCATION",
    "CITY": "LOCATION",
    "STATE_OR_REGION": "LOCATION",
    "COUNTRY": "LOCATION",
    "GLINER2_CITY": "LOCATION",
    "GLINER2_STATE_OR_REGION": "LOCATION",
    "GLINER2_COUNTRY": "LOCATION",
    "ORGANIZATION": "ORGANIZATION",
    "EMPLOYER": "ORGANIZATION",
    "DOCUMENT_ID": "DOCUMENT_ID",
    "DOCUMENT_NUMBER": "DOCUMENT_ID",
    "ID": "DOCUMENT_ID",
    "ES_NIF": "DOCUMENT_ID",
    "ES_NIE": "DOCUMENT_ID",
    "MEDICAL_LICENSE": "DOCUMENT_ID",
    "GOVERNMENT_ID": "DOCUMENT_ID",
    "NATIONAL_ID_NUMBER": "DOCUMENT_ID",
    "PASSPORT_NUMBER": "DOCUMENT_ID",
    "DRIVERS_LICENSE_NUMBER": "DOCUMENT_ID",
    "LICENSE_NUMBER": "DOCUMENT_ID",
    "TAX_ID": "DOCUMENT_ID",
    "TAX_NUMBER": "DOCUMENT_ID",
    "GLINER2_GOVERNMENT_ID": "DOCUMENT_ID",
    "GLINER2_NATIONAL_ID_NUMBER": "DOCUMENT_ID",
    "GLINER2_PASSPORT_NUMBER": "DOCUMENT_ID",
    "GLINER2_DRIVERS_LICENSE_NUMBER": "DOCUMENT_ID",
    "GLINER2_LICENSE_NUMBER": "DOCUMENT_ID",
    "GLINER2_TAX_ID": "DOCUMENT_ID",
    "GLINER2_TAX_NUMBER": "DOCUMENT_ID",
    "CARD_EXPIRY": "CARD_EXPIRY",
    "GLINER2_CARD_EXPIRY": "CARD_EXPIRY",
    "CARD_CVV": "CARD_CVV",
    "GLINER2_CARD_CVV": "CARD_CVV",
    "BANK_ACCOUNT": "BANK_ACCOUNT",
    "ACCOUNT_NUMBER": "BANK_ACCOUNT",
    "IBAN_CODE": "BANK_ACCOUNT",
    "GLINER2_BANK_ACCOUNT": "BANK_ACCOUNT",
    "GLINER2_ACCOUNT_NUMBER": "BANK_ACCOUNT",
    "CRYPTO": "CRYPTO_WALLET",
    "CRYPTO_WALLET": "CRYPTO_WALLET",
    "ACCOUNT_ID": "ACCOUNT_ID",
    "SENSITIVE_ACCOUNT_ID": "ACCOUNT_ID",
    "GLINER2_ACCOUNT_ID": "ACCOUNT_ID",
    "GLINER2_SENSITIVE_ACCOUNT_ID": "ACCOUNT_ID",
    "USERNAME": "USERNAME",
    "GLINER2_USERNAME": "USERNAME",
    "PASSWORD": "PASSWORD",
    "GLINER2_PASSWORD": "PASSWORD",
    "SECRET": "SECRET",
    "GLINER2_SECRET": "SECRET",
    "API_KEY": "API_KEY",
    "GLINER2_API_KEY": "API_KEY",
    "ACCESS_TOKEN": "ACCESS_TOKEN",
    "GLINER2_ACCESS_TOKEN": "ACCESS_TOKEN",
    "RECOVERY_CODE": "RECOVERY_CODE",
    "GLINER2_RECOVERY_CODE": "RECOVERY_CODE",
    "AGE": "AGE",
    "IP_ADDRESS": "IP_ADDRESS",
    "GLINER2_IP_ADDRESS": "IP_ADDRESS",
    "MAC_ADDRESS": "MAC_ADDRESS",
    "URL": "URL",
    "MEDICAL_PROBLEM": "MEDICAL_PROBLEM",
    "MEDICAL_TEST": "MEDICAL_TEST",
    "MEDICAL_TREATMENT": "MEDICAL_TREATMENT",
}

BASE_ENTITY_TYPES = {
    "RUT",
    "PHONE",
    "EMAIL",
    "PAYMENT_CARD",
    "LICENSE_PLATE",
    "PENSION_SYSTEM",
    "HEALTH_SYSTEM",
    "GENDER",
    "MARITAL_STATUS",
    "RELIGION_OR_BELIEF",
    "SEXUAL_ORIENTATION",
    "POLITICAL_OR_UNION_AFFILIATION",
}

LOCAL_VALIDATION_ENTITY_TYPES = {"AGE", "DATE", "IP_ADDRESS", "MAC_ADDRESS", "URL"}

SCORE_THRESHOLD_ENTITY_TYPES = {
    "DOCUMENT_ID",
    "CARD_EXPIRY",
    "CARD_CVV",
    "BANK_ACCOUNT",
    "CRYPTO_WALLET",
    "ACCOUNT_ID",
    "USERNAME",
    "PASSWORD",
    "SECRET",
    "API_KEY",
    "ACCESS_TOKEN",
    "RECOVERY_CODE",
    "BIOMETRIC_OR_BIOLOGICAL",
}

ZERO_SHOT_ENTITY_TYPES = {
    "NAME",
    "ORGANIZATION",
    "LOCATION",
    "ADDRESS",
    "MEDICAL_PROBLEM",
    "MEDICAL_TEST",
    "MEDICAL_TREATMENT",
}


def canonical_entity_type(entity_type: str, raw_entity_type: str | None = None) -> str:
    normalized_entity_type = _normalize_type(entity_type)
    normalized_raw_type = _normalize_type(raw_entity_type or "")

    for candidate in (normalized_entity_type, normalized_raw_type):
        if candidate in CANONICAL_ENTITY_TYPES:
            return CANONICAL_ENTITY_TYPES[candidate]

    if normalized_entity_type.startswith("GLINER2_"):
        return normalized_entity_type.removeprefix("GLINER2_")

    return normalized_entity_type


def is_base_entity_type(entity_type: str) -> bool:
    return canonical_entity_type(entity_type) in BASE_ENTITY_TYPES


def is_local_validation_entity_type(entity_type: str) -> bool:
    return canonical_entity_type(entity_type) in LOCAL_VALIDATION_ENTITY_TYPES


def is_score_threshold_entity_type(entity_type: str) -> bool:
    return canonical_entity_type(entity_type) in SCORE_THRESHOLD_ENTITY_TYPES


def is_zero_shot_entity_type(entity_type: str) -> bool:
    return canonical_entity_type(entity_type) in ZERO_SHOT_ENTITY_TYPES


def normalize_base_value(entity_type: str, value: str) -> str | None:
    canonical = canonical_entity_type(entity_type)

    if canonical == "RUT":
        return normalize_rut(value)
    if canonical == "PHONE":
        return normalize_phone_cl(value)
    if canonical == "PAYMENT_CARD":
        return normalize_payment_card(value)
    if canonical == "LICENSE_PLATE":
        return normalize_license_plate(value)
    if canonical == "EMAIL":
        return normalize_email(value)
    if canonical == "GENDER":
        normalized = normalize_gender_identity(value)
        return normalized or None
    if canonical in {
        "PENSION_SYSTEM",
        "HEALTH_SYSTEM",
        "MARITAL_STATUS",
        "RELIGION_OR_BELIEF",
        "SEXUAL_ORIENTATION",
        "POLITICAL_OR_UNION_AFFILIATION",
    }:
        normalized = normalize_text_key(value)
        return normalized or None

    return None


def normalize_local_validation_value(entity_type: str, value: str) -> str | None:
    canonical = canonical_entity_type(entity_type)

    if canonical == "AGE":
        return normalize_age(value)
    if canonical == "DATE":
        return normalize_date(value)
    if canonical == "IP_ADDRESS":
        return normalize_ip_address(value)
    if canonical == "MAC_ADDRESS":
        return normalize_mac_address(value)
    if canonical == "URL":
        return normalize_url(value)

    return None


def normalize_non_base_value(entity_type: str, value: str, raw_normalized: str | None) -> str:
    canonical = canonical_entity_type(entity_type)
    if canonical in {"CARD_EXPIRY", "CARD_CVV", "BANK_ACCOUNT", "ACCOUNT_ID"}:
        return normalize_text_key(raw_normalized or value)
    return raw_normalized or normalize_text_key(value)


def value_key(value: str | None) -> str:
    return normalize_text_key(value or "")


def normalize_text_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def normalize_gender_identity(value: str) -> str | None:
    normalized = normalize_text_key(strip_accents(value))
    if normalized in {"m", "h", "hombre", "masculino"}:
        return "masculino"
    if normalized in {"f", "mujer", "femenino"}:
        return "femenino"
    return normalized or None


def strip_accents(value: str) -> str:
    replacements = str.maketrans(
        {
            "á": "a",
            "é": "e",
            "í": "i",
            "ó": "o",
            "ú": "u",
            "Á": "A",
            "É": "E",
            "Í": "I",
            "Ó": "O",
            "Ú": "U",
            "ñ": "n",
            "Ñ": "N",
        }
    )
    return value.translate(replacements)


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def normalize_rut(value: str) -> str | None:
    clean = re.sub(r"[^0-9Kk]", "", value).upper()

    if not re.fullmatch(r"\d{7,8}[0-9K]", clean):
        return None
    if not validate_rut(clean):
        return None

    return format_rut(clean)


def format_rut(clean: str) -> str:
    body = clean[:-1]
    dv = clean[-1]
    groups: list[str] = []
    while body:
        groups.insert(0, body[-3:])
        body = body[:-3]
    return f"{'.'.join(groups)}-{dv}"


def validate_rut(value: str) -> bool:
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


def normalize_phone_cl(value: str) -> str | None:
    raw = value.strip()
    digits = digits_only(raw)
    has_country_prefix = (
        raw.startswith("+56")
        or raw.startswith("0056")
        or digits.startswith("56")
    )

    if digits.startswith("0056"):
        digits = "56" + digits[4:]

    if digits.startswith("56"):
        national = digits[2:]
    else:
        national = digits

    if len(national) != 9:
        return None
    if national[0] not in "9234567":
        return None
    if not has_country_prefix and national[0] != "9":
        return None

    return f"+56{national}"


def normalize_payment_card(value: str) -> str | None:
    digits = digits_only(value)
    if not luhn_checksum(digits):
        return None
    return digits


def luhn_checksum(value: str) -> bool:
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


def normalize_license_plate(value: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    patterns = (
        r"[A-Z]{2}[A-Z]{2}\d{2}",
        r"[A-Z]{2}\d{2}\d{2}",
        r"[A-Z]{3}\d{2}",
        r"[A-Z]{4,5}\d",
    )

    if not any(re.fullmatch(pattern, cleaned) for pattern in patterns):
        return None

    return cleaned


def normalize_email(value: str) -> str | None:
    email = value.strip()

    if any(ch.isspace() for ch in email):
        return None
    if email.count("@") != 1:
        return None

    local_part, domain = email.rsplit("@", 1)
    if not local_part or not domain:
        return None
    if "." not in domain:
        return None
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return None

    try:
        normalized_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None

    if not re.fullmatch(r"[A-Za-z0-9.-]+", normalized_domain):
        return None

    return f"{local_part}@{normalized_domain}"


def normalize_age(value: str) -> str | None:
    text = value.strip()
    if not re.fullmatch(r"\d{1,3}", text):
        return None
    age = int(text)
    if not 0 <= age <= 130:
        return None
    return str(age)


def normalize_date(value: str) -> str | None:
    text = normalize_text_key(value)
    text = re.sub(r"[t\s]+(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s*(?:am|pm))?$", "", text)
    text = text.strip(",.;")

    parsed = _parse_numeric_date(text) or _parse_month_name_date(text)
    if parsed is None:
        return None
    year, month, day = parsed
    if not _valid_date_parts(year, month, day):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_ip_address(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def normalize_mac_address(value: str) -> str | None:
    text = value.strip()
    colon_or_dash = re.fullmatch(
        r"([0-9A-Fa-f]{2})([:-])([0-9A-Fa-f]{2})(?:\2[0-9A-Fa-f]{2}){4}",
        text,
    )
    dotted = re.fullmatch(r"[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}", text)
    if not colon_or_dash and not dotted:
        return None
    hex_digits = re.sub(r"[^0-9A-Fa-f]", "", text).upper()
    return ":".join(hex_digits[idx : idx + 2] for idx in range(0, 12, 2))


def normalize_url(value: str) -> str | None:
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return None

    candidate = text
    if "://" not in candidate:
        if "." not in candidate:
            return None
        candidate = f"http://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https", "ftp"}:
        return None
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    if parsed.netloc.startswith(".") or parsed.netloc.endswith("."):
        return None

    return text


def _parse_numeric_date(text: str) -> tuple[int, int, int] | None:
    year_first = re.fullmatch(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", text)
    if year_first:
        year, month, day = (int(part) for part in year_first.groups())
        return year, month, day

    parts_match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text)
    if not parts_match:
        return None

    first, second, year_raw = (int(part) for part in parts_match.groups())
    year = _expand_year(year_raw)
    for day, month in ((first, second), (second, first)):
        if _valid_date_parts(year, month, day):
            return year, month, day
    return None


def _parse_month_name_date(text: str) -> tuple[int, int, int] | None:
    month_names = _month_names()
    normalized = strip_accents(text).casefold()
    normalized = re.sub(r"\bde\b|,", " ", normalized)
    normalized = normalize_text_key(normalized)

    match = re.fullmatch(r"(\d{1,2}) ([a-z]+) (\d{2,4})", normalized)
    if match:
        day = int(match.group(1))
        month = month_names.get(match.group(2))
        year = _expand_year(int(match.group(3)))
        return (year, month, day) if month is not None else None

    match = re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{2,4})", normalized)
    if match:
        month = month_names.get(match.group(1))
        day = int(match.group(2))
        year = _expand_year(int(match.group(3)))
        return (year, month, day) if month is not None else None

    return None


def _month_names() -> dict[str, int]:
    names = {
        "enero": 1,
        "ene": 1,
        "febrero": 2,
        "feb": 2,
        "marzo": 3,
        "mar": 3,
        "abril": 4,
        "abr": 4,
        "mayo": 5,
        "may": 5,
        "junio": 6,
        "jun": 6,
        "julio": 7,
        "jul": 7,
        "agosto": 8,
        "ago": 8,
        "septiembre": 9,
        "setiembre": 9,
        "sep": 9,
        "octubre": 10,
        "oct": 10,
        "noviembre": 11,
        "nov": 11,
        "diciembre": 12,
        "dic": 12,
        "january": 1,
        "jan": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "june": 6,
        "july": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sept": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "dec": 12,
    }
    return names


def _expand_year(year: int) -> int:
    if year >= 100:
        return year
    return 2000 + year if year <= 30 else 1900 + year


def _valid_date_parts(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12:
        return False
    if not 1 <= year <= 9999:
        return False
    _, last_day = calendar.monthrange(year, month)
    return 1 <= day <= last_day


def _normalize_type(entity_type: str) -> str:
    normalized = entity_type.strip().upper()
    for suffix in ("_CONTEXT_REGEX", "_DENY_LIST", "_REGEX"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized
