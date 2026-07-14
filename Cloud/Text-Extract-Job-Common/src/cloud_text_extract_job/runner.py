from __future__ import annotations

from collections.abc import Callable
import logging
import time

from .config import TextExtractJobConfig
from .errors import TransientProcessingError
from .pubsub import PulledMessage, PubSubPuller


LOGGER = logging.getLogger("cloud_text_extract_job")

MessageHandler = Callable[[PulledMessage], None]


def drain_subscription(
    *,
    config: TextExtractJobConfig,
    puller: PubSubPuller,
    handle_message: MessageHandler,
) -> int:
    processed_count = 0
    idle_started = time.monotonic()

    while True:
        if config.max_messages and processed_count >= config.max_messages:
            return processed_count

        pulled = puller.pull_one(
            config.subscription_id,
            timeout_seconds=config.pull_timeout_seconds,
        )
        if pulled is None:
            if time.monotonic() - idle_started >= config.idle_timeout_seconds:
                return processed_count
            continue

        idle_started = time.monotonic()
        try:
            handle_message(pulled)
        except TransientProcessingError as exc:
            LOGGER.warning("transient_message_failure error=%s", exc)
            puller.nack(config.subscription_id, pulled.ack_id)
            continue
        except Exception:
            LOGGER.exception("message_processing_failed")
            raise

        puller.ack(config.subscription_id, pulled.ack_id)
        processed_count += 1
