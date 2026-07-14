from __future__ import annotations

from dataclasses import dataclass, field

from table_extract.models._validation import (
    require_non_blank,
    require_positive_int,
)


@dataclass(frozen=True)
class ColumnProfile:
    column_name: str
    data_type: str | None = None
    nullable: bool | None = None
    ordinal_position: int | None = None
    comment: str | None = None
    is_primary_key: bool = False
    foreign_key: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.column_name, "column_name")
        if self.ordinal_position is not None:
            require_positive_int(self.ordinal_position, "ordinal_position")


@dataclass(frozen=True)
class TableProfile:
    table_name: str
    schema_name: str | None = None
    table_type: str = "table"
    columns: tuple[ColumnProfile, ...] = field(default_factory=tuple)
    row_count: int | None = None
    comment: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.table_name, "table_name")
        require_non_blank(self.table_type, "table_type")
        object.__setattr__(self, "columns", tuple(self.columns))
        if self.row_count is not None and self.row_count < 0:
            raise ValueError("row_count must be a non-negative integer")


@dataclass(frozen=True)
class DataSourceProfile:
    source_name: str
    source_type: str
    tables: tuple[TableProfile, ...] = field(default_factory=tuple)
    dialect: str | None = None
    source_uri: str | None = None

    def __post_init__(self) -> None:
        require_non_blank(self.source_name, "source_name")
        require_non_blank(self.source_type, "source_type")
        object.__setattr__(self, "tables", tuple(self.tables))
