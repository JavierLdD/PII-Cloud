from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import re
import sys
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_SENSITIVE_KEY_PARTS = (
    "authorization",
    "bearer",
    "client_secret",
    "connection_uri",
    "database_url",
    "password",
    "secret",
    "token",
)


@dataclass(frozen=True)
class OperationalErrorInfo:
    component: str
    category: str
    retryable: bool
    message: str
    safe_context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "component", _safe_token(self.component, "unknown"))
        object.__setattr__(self, "category", _safe_token(self.category, "unknown"))
        object.__setattr__(self, "message", sanitize_text(self.message))
        object.__setattr__(
            self,
            "safe_context",
            sanitize_context(dict(self.safe_context)),
        )


class OperationalException(RuntimeError):
    component = "runtime"
    category = "unexpected_error"
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        category: str | None = None,
        retryable: bool | None = None,
        safe_context: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(sanitize_text(message))
        self.component = component or self.component
        self.category = category or self.category
        self.retryable = self.retryable if retryable is None else bool(retryable)
        self.safe_context = sanitize_context(dict(safe_context or {}))

    def operational_info(self) -> OperationalErrorInfo:
        return OperationalErrorInfo(
            component=self.component,
            category=self.category,
            retryable=self.retryable,
            message=str(self),
            safe_context=self.safe_context,
        )


def classify_operational_exception(
    exc: Exception,
    *,
    default_component: str = "runtime",
    safe_context: Mapping[str, Any] | None = None,
) -> OperationalErrorInfo:
    context = dict(safe_context or {})
    if isinstance(exc, OperationalException):
        context.update(dict(exc.safe_context))
        return OperationalErrorInfo(
            component=exc.component,
            category=exc.category,
            retryable=exc.retryable,
            message=str(exc),
            safe_context=context,
        )

    if isinstance(exc, ValueError):
        return OperationalErrorInfo(
            component=default_component,
            category="invalid_payload",
            retryable=False,
            message=str(exc),
            safe_context=context,
        )

    return OperationalErrorInfo(
        component=default_component,
        category=exc.__class__.__name__,
        retryable=True,
        message=str(exc) or exc.__class__.__name__,
        safe_context=context,
    )


def emit_operational_log(
    event: str,
    info: OperationalErrorInfo | Exception,
    *,
    safe_context: Mapping[str, Any] | None = None,
    stream=None,
) -> None:
    if isinstance(info, Exception):
        info = classify_operational_exception(info, safe_context=safe_context)
    else:
        merged_context = dict(safe_context or {})
        merged_context.update(dict(info.safe_context))
        info = OperationalErrorInfo(
            component=info.component,
            category=info.category,
            retryable=info.retryable,
            message=info.message,
            safe_context=merged_context,
        )

    payload = {
        "event": _safe_token(event, "operational_event"),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "component": info.component,
        "category": info.category,
        "retryable": info.retryable,
        "message": info.message,
        "safe_context": dict(info.safe_context),
    }
    print(
        json.dumps(payload, ensure_ascii=True, sort_keys=True),
        file=stream or sys.stderr,
    )


def sanitize_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _sanitize_context_value(str(key), value)
        for key, value in context.items()
    }


def sanitize_text(value: object) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    text = _redact_urls(text)
    text = re.sub(
        r"(?i)(password|passwd|access_token|bearer_token|client_secret|token)=([^&\s]+)",
        r"\1=***",
        text,
    )
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", text)
    text = re.sub(r"(?i)basic\s+[A-Za-z0-9._~+/=-]+", "Basic ***", text)
    text = re.sub(r"(?i)\bsecret[-_][A-Za-z0-9._-]+\b", "***", text)
    return text


def _sanitize_context_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return "***"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Mapping):
        return sanitize_context(value)
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_context_value("", item) for item in value]
    return sanitize_text(value)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _safe_token(value: str, fallback: str) -> str:
    text = sanitize_text(value)
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")
    return text or fallback


def _redact_urls(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return _safe_url(match.group(0))

    return re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", replace, text)


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return value

    hostname = parsed.hostname or ""
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"

    query_items = []
    for key, raw_value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            query_items.append((key, "***"))
        else:
            query_items.append((key, raw_value))

    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(query_items),
            parsed.fragment,
        )
    )
