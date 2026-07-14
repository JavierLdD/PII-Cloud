from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from table_extract.models import ColumnProfile, ColumnSample, TableProfile


@runtime_checkable
class SourceAdapter(Protocol):
    source_name: str
    source_type: str
    dialect: str | None
    source_uri: str | None

    def iter_tables(self) -> Iterable[TableProfile]:
        ...

    def iter_columns(self, table: TableProfile) -> Iterable[ColumnProfile]:
        ...

    def get_column_sample(
        self,
        table: TableProfile,
        column: ColumnProfile,
        *,
        limit: int = 1000,
        max_value_length: int = 256,
    ) -> ColumnSample:
        ...

    def close(self) -> None:
        ...
