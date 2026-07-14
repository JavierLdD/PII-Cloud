from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_IDLE_TIMEOUT_SECONDS = 60
DEFAULT_PULL_TIMEOUT_SECONDS = 5
DEFAULT_PER_FILE_TIMEOUT_SECONDS = 540
DEFAULT_MAX_MESSAGES = 0
DEFAULT_TEXT_SCRATCH_DIR = "/tmp/pii-text"


@dataclass(frozen=True)
class TextExtractJobConfig:
    subscription_id: str
    database_url: str
    topic_pii_entities: str
    topic_text_poison: str
    expected_user_id: str
    expected_run_id: str
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    pull_timeout_seconds: int = DEFAULT_PULL_TIMEOUT_SECONDS
    per_file_timeout_seconds: int = DEFAULT_PER_FILE_TIMEOUT_SECONDS
    max_messages: int = DEFAULT_MAX_MESSAGES
    scratch_dir: str = DEFAULT_TEXT_SCRATCH_DIR

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "TextExtractJobConfig":
        return cls(
            subscription_id=_required_env(env, "SUBSCRIPTION_ID"),
            database_url=_required_env(env, "DATABASE_URL"),
            topic_pii_entities=_required_env(env, "TOPIC_PII_ENTITIES"),
            topic_text_poison=_required_env(env, "TOPIC_TEXT_POISON"),
            expected_user_id=_required_env(env, "EXPECTED_USER_ID"),
            expected_run_id=_required_env(env, "EXPECTED_RUN_ID"),
            idle_timeout_seconds=_int_env(
                env,
                "PUBSUB_IDLE_TIMEOUT_SECONDS",
                DEFAULT_IDLE_TIMEOUT_SECONDS,
            ),
            pull_timeout_seconds=_int_env(
                env,
                "PUBSUB_PULL_TIMEOUT_SECONDS",
                DEFAULT_PULL_TIMEOUT_SECONDS,
            ),
            per_file_timeout_seconds=_int_env(
                env,
                "PER_FILE_TIMEOUT_SECONDS",
                DEFAULT_PER_FILE_TIMEOUT_SECONDS,
            ),
            max_messages=_int_env(env, "MAX_MESSAGES", DEFAULT_MAX_MESSAGES),
            scratch_dir=env.get("TEXT_MATERIALIZE_SCRATCH_DIR")
            or DEFAULT_TEXT_SCRATCH_DIR,
        )

    def apply_runtime_defaults(self) -> None:
        import os

        os.environ.setdefault("TEXT_MATERIALIZE_SCRATCH_DIR", self.scratch_dir)


def _required_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    parsed = int(value)
    if parsed < 0:
        raise RuntimeError(f"{name} must be greater than or equal to zero")
    return parsed


def ensure_scratch_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)
