from __future__ import annotations

from contextlib import contextmanager
import signal
from types import FrameType
from typing import Iterator


class FileProcessingTimeout(TimeoutError):
    """Raised when one file exceeds the configured processing timeout."""


@contextmanager
def per_file_timeout(seconds: int) -> Iterator[None]:
    if seconds <= 0 or not hasattr(signal, "setitimer"):
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def handler(signum: int, frame: FrameType | None) -> None:
        raise FileProcessingTimeout(f"per_file_timeout_exceeded seconds={seconds}")

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
