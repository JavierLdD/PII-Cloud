from __future__ import annotations

from dataclasses import dataclass, field

from table_extract.models._validation import (
    require_non_blank,
    require_non_negative_int,
)


@dataclass(frozen=True)
class ColumnSample:
    table_name: str
    column_name: str
    values: tuple[str, ...] = field(default_factory=tuple, repr=False)
    schema_name: str | None = None
    sampled_count: int | None = None
    non_null_count: int | None = None
    max_value_length: int | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        require_non_blank(self.table_name, "table_name")
        require_non_blank(self.column_name, "column_name")

        values = tuple(self.values)
        if not all(isinstance(value, str) for value in values):
            raise ValueError("values must contain strings only")
        object.__setattr__(self, "values", values)

        sampled_count = len(values) if self.sampled_count is None else self.sampled_count
        non_null_count = len(values) if self.non_null_count is None else self.non_null_count

        require_non_negative_int(sampled_count, "sampled_count")
        require_non_negative_int(non_null_count, "non_null_count")
        if non_null_count > sampled_count:
            raise ValueError("non_null_count cannot be greater than sampled_count")
        if self.max_value_length is not None and self.max_value_length <= 0:
            raise ValueError("max_value_length must be a positive integer")

        object.__setattr__(self, "sampled_count", sampled_count)
        object.__setattr__(self, "non_null_count", non_null_count)
