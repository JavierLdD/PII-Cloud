from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocr import main as ocr_main  # noqa: E402


def test_ocr_main_accepts_mineru_api_options() -> None:
    args = ocr_main.parse_args(
        [
            "--mineru-api-url",
            "http://mineru.local",
            "--mineru-api-poll-interval",
            "0.5",
            "--mineru-api-submit-timeout",
            "20",
            "--mineru-timeout",
            "90",
        ]
    )

    assert args.mineru_api_url == "http://mineru.local"
    assert args.mineru_api_poll_interval == 0.5
    assert args.mineru_api_submit_timeout == 20
    assert args.mineru_timeout == 90


def test_ocr_main_resolves_result_timeout_from_api_env(monkeypatch) -> None:
    monkeypatch.setenv("MINERU_API_RESULT_TIMEOUT_SECONDS", "123")

    assert ocr_main._resolve_result_timeout(None) == 123


def test_ocr_main_result_timeout_argument_wins(monkeypatch) -> None:
    monkeypatch.setenv("MINERU_API_RESULT_TIMEOUT_SECONDS", "123")

    assert ocr_main._resolve_result_timeout(45) == 45
