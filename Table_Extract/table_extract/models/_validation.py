from __future__ import annotations


def require_non_blank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def require_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def require_optional_probability(value: float | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
