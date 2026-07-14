from __future__ import annotations


class MessageScopeError(ValueError):
    """Raised when a message does not belong to the expected user/run."""


class PermanentProcessingError(RuntimeError):
    """Raised for message failures that should be poisoned and acknowledged."""


class TransientProcessingError(RuntimeError):
    """Raised for message failures that should be retried later."""
