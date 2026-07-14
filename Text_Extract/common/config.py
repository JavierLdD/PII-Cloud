from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


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


def optional_int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    return int(value)
