from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from table_extract.models._validation import (
    require_non_blank,
    require_optional_probability,
    require_positive_int,
)
from table_extract.models.profiles import DataSourceProfile

if TYPE_CHECKING:
    from table_extract.sources import SourceAdapter


@dataclass(frozen=True)
class ScanConfig:
    sample_limit: int = 1000
    max_value_length: int = 256
    sample_only_when_needed: bool = True
    zero_shot_enabled: bool = True
    zero_shot_model_name: str = (
        "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
    )
    zero_shot_device: str = "cpu"
    zero_shot_initial_sample_limit: int = 50
    zero_shot_expanded_sample_limit: int = 200
    zero_shot_positive_threshold: float = 0.75
    zero_shot_continue_threshold: float = 0.30
    zero_shot_batch_size: int = 8

    def __post_init__(self) -> None:
        require_positive_int(self.sample_limit, "sample_limit")
        require_positive_int(self.max_value_length, "max_value_length")
        require_non_blank(self.zero_shot_model_name, "zero_shot_model_name")
        require_non_blank(self.zero_shot_device, "zero_shot_device")
        require_positive_int(
            self.zero_shot_initial_sample_limit,
            "zero_shot_initial_sample_limit",
        )
        require_positive_int(
            self.zero_shot_expanded_sample_limit,
            "zero_shot_expanded_sample_limit",
        )
        require_optional_probability(
            self.zero_shot_positive_threshold,
            "zero_shot_positive_threshold",
        )
        require_optional_probability(
            self.zero_shot_continue_threshold,
            "zero_shot_continue_threshold",
        )
        require_positive_int(self.zero_shot_batch_size, "zero_shot_batch_size")


@dataclass(frozen=True)
class ScanSession:
    run_id: str
    source: SourceAdapter
    profile: DataSourceProfile
    config: ScanConfig = field(default_factory=ScanConfig)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        require_non_blank(self.run_id, "run_id")
        if self.source is None:
            raise ValueError("source cannot be None")
