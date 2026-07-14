from __future__ import annotations

from pathlib import Path
import os


def load_environment(env_file: str | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    if env_file:
        load_dotenv(env_file)
        return

    default_env = Path(__file__).resolve().parents[1] / ".env"
    if default_env.exists():
        load_dotenv(default_env)


def require_env(primary_name: str, fallback_name: str | None = None) -> str:
    value = os.environ.get(primary_name)
    if value:
        return value
    if fallback_name:
        fallback_value = os.environ.get(fallback_name)
        if fallback_value:
            return fallback_value
    names = primary_name if fallback_name is None else f"{primary_name} or {fallback_name}"
    raise RuntimeError(f"Missing required environment variable: {names}")
