from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from Entity_Text_Filter.validators import (
    canonical_entity_type,
    is_base_entity_type,
    normalize_base_value,
    normalize_email,
    normalize_license_plate,
    normalize_local_validation_value,
    normalize_payment_card,
    normalize_phone_cl,
    normalize_rut,
)


def test_canonical_entity_type_handles_taxonomy_groups():
    assert canonical_entity_type("AFP") == "PENSION_SYSTEM"
    assert canonical_entity_type("SISTEMA_DE_SALUD") == "HEALTH_SYSTEM"
    assert canonical_entity_type("TELEFONO") == "PHONE"
    assert canonical_entity_type("GLINER2_EMAIL") == "EMAIL"
    assert canonical_entity_type("GLINER2_FULL_NAME") == "NAME"
    assert canonical_entity_type("PERSON") == "NAME"
    assert canonical_entity_type("GLINER2_DATE_OF_BIRTH") == "DATE"
    assert canonical_entity_type("DATE_TIME") == "DATE"
    assert canonical_entity_type("GENDER_IDENTITY_DENY_LIST") == "GENDER"
    assert canonical_entity_type("HEALTH_DATA_DENY_LIST") == "MEDICAL_PROBLEM"
    assert canonical_entity_type("GLINER2_CITY") == "LOCATION"
    assert canonical_entity_type("GLINER2_ADDRESS") == "ADDRESS"


def test_sensitive_deny_list_categories_are_base_text_values():
    for entity_type, value, normalized in [
        ("GENDER_IDENTITY", "Femenino", "femenino"),
        ("RELIGION_OR_BELIEF", "Religion Catolica", "religion catolica"),
        ("MARITAL_STATUS", "Union Civil", "union civil"),
        ("SEXUAL_ORIENTATION", "Bisexual", "bisexual"),
        ("POLITICAL_OR_UNION_AFFILIATION", "Sindicato", "sindicato"),
    ]:
        assert is_base_entity_type(entity_type)
        assert normalize_base_value(entity_type, value) == normalized


def test_gender_identity_base_value_normalizes_contextual_codes():
    for value, normalized in [
        ("M", "masculino"),
        ("H", "masculino"),
        ("hombre", "masculino"),
        ("masculino", "masculino"),
        ("F", "femenino"),
        ("mujer", "femenino"),
        ("femenino", "femenino"),
    ]:
        assert normalize_base_value("GENDER_IDENTITY", value) == normalized


def test_normalize_rut_validates_modulo_11():
    assert normalize_rut("12.378.895-8") == "12.378.895-8"
    assert normalize_rut("12.378.895-9") is None


def test_normalize_phone_cl_accepts_mobile_and_rejects_local_without_prefix():
    assert normalize_phone_cl("+56 9 1234 5678") == "+56912345678"
    assert normalize_phone_cl("2 2345 6789") is None


def test_normalize_payment_card_uses_luhn_and_repeated_digit_guard():
    assert normalize_payment_card("4111 1111 1111 1111") == "4111111111111111"
    assert normalize_payment_card("1111 1111 1111 1111") is None


def test_normalize_license_plate_and_email():
    assert normalize_license_plate("AB-CD-12") == "ABCD12"
    assert normalize_email("Persona@Example.COM") == "Persona@example.com"
    assert normalize_email("persona@example") is None


def test_local_validation_age_date_ip_mac_url():
    assert normalize_local_validation_value("AGE", "45") == "45"
    assert normalize_local_validation_value("AGE", "145") is None
    assert normalize_local_validation_value("DATE", "31/12/25") == "2025-12-31"
    assert normalize_local_validation_value("DATE", "12/31/2025") == "2025-12-31"
    assert normalize_local_validation_value("DATE", "2025-12-31") == "2025-12-31"
    assert normalize_local_validation_value("DATE", "31 de diciembre de 2025") == "2025-12-31"
    assert normalize_local_validation_value("IP_ADDRESS", "192.168.0.1") == "192.168.0.1"
    assert normalize_local_validation_value("MAC_ADDRESS", "aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_local_validation_value("URL", "https://example.com/path") == "https://example.com/path"
