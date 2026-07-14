from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_LIMIT = 50
DEFAULT_MAX_TEXT_CHARS = 800


@dataclass(frozen=True)
class ReviewConfig:
    database_url: str
    text_extract_dir: Path
    show_text: bool = False
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS


def resolve_text_extract_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()

    current = Path.cwd().resolve()
    if current.name == "notebooks" and (current.parent / "schema.sql").exists():
        return current.parent
    if (current / "schema.sql").exists() and current.name == "Text_Extract":
        return current

    return Path(__file__).resolve().parents[1]


def load_config(
    text_extract_dir: str | Path | None = None,
    database_url: str | None = None,
    show_text: bool = False,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
) -> ReviewConfig:
    resolved_dir = resolve_text_extract_dir(text_extract_dir)
    load_local_env(resolved_dir)
    resolved_database_url = database_url or os.environ.get("DATABASE_URL", "")
    if not resolved_database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Create Text_Extract/.env or "
            "set DATABASE_URL in the notebook kernel before loading config."
        )

    return ReviewConfig(
        database_url=resolved_database_url,
        text_extract_dir=resolved_dir,
        show_text=show_text,
        max_text_chars=max_text_chars,
    )


def load_local_env(text_extract_dir: Path) -> None:
    env_candidates = [
        text_extract_dir / ".env",
        text_extract_dir.parent / "Router" / ".env",
        text_extract_dir.parent / "File_Discovery" / ".env",
    ]

    try:
        from dotenv import load_dotenv
    except ImportError:
        for env_path in env_candidates:
            _load_env_without_dependency(env_path)
        return

    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)


def list_files(
    config: ReviewConfig,
    run_id: str | None = None,
    status: str | None = None,
    file_name_contains: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    where, params = _build_filters(
        [
            ("tef.run_id = %s", run_id),
            ("tef.status = %s", status),
            ("f.file_name ILIKE %s", _like(file_name_contains)),
        ]
    )
    params.append(limit)
    return fetch_rows(
        config,
        f"""
        SELECT
            tef.file_id::text AS file_id,
            tef.run_id::text AS run_id,
            f.file_name,
            f.relative_path,
            tef.status,
            tef.total_pages,
            tef.completed_pages,
            tef.pending_ocr_pages,
            tef.failed_pages,
            tef.chunk_count,
            tef.started_at,
            tef.completed_at,
            tef.processing_seconds,
            tef.error
        FROM text_extraction_files tef
        JOIN files f ON f.file_id = tef.file_id
        {where}
        ORDER BY tef.updated_at DESC, f.file_name
        LIMIT %s
        """,
        params,
    )


def list_pages(
    config: ReviewConfig,
    file_id: str,
) -> list[dict[str, Any]]:
    return fetch_rows(
        config,
        """
        SELECT
            page_number,
            page_index,
            method,
            status,
            reason,
            char_count,
            word_count,
            ROUND(total_image_ratio::numeric, 4) AS total_image_ratio,
            ROUND(largest_image_ratio::numeric, 4) AS largest_image_ratio,
            chunk_count,
            ocr_outbox_id::text AS ocr_outbox_id,
            error
        FROM text_extraction_pages
        WHERE file_id = %s
        ORDER BY page_number
        """,
        [file_id],
    )


def list_chunks(
    config: ReviewConfig,
    file_id: str,
    show_text: bool | None = None,
    max_text_chars: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    should_show_text = config.show_text if show_text is None else show_text
    max_chars = config.max_text_chars if max_text_chars is None else max_text_chars
    text_column = (
        "LEFT(text, %s) AS text_preview,"
        if should_show_text
        else "NULL::text AS text_preview,"
    )
    params: list[Any] = []
    if should_show_text:
        params.append(max_chars)
    params.extend([file_id, limit])

    return fetch_rows(
        config,
        f"""
        SELECT
            chunk_id,
            chunk_index,
            page_start,
            page_end,
            method,
            status,
            LENGTH(text) AS text_chars,
            text_hash_sha256,
            {text_column}
            expires_at,
            created_at,
            updated_at
        FROM text_chunks_staging
        WHERE file_id = %s
        ORDER BY chunk_index
        LIMIT %s
        """,
        params,
    )


def get_chunk(
    config: ReviewConfig,
    chunk_id: str,
    show_text: bool | None = None,
    max_text_chars: int | None = None,
) -> dict[str, Any] | None:
    should_show_text = config.show_text if show_text is None else show_text
    max_chars = config.max_text_chars if max_text_chars is None else max_text_chars
    text_column = (
        "LEFT(text, %s) AS text_preview,"
        if should_show_text
        else "NULL::text AS text_preview,"
    )
    params: list[Any] = []
    if should_show_text:
        params.append(max_chars)
    params.append(chunk_id)

    rows = fetch_rows(
        config,
        f"""
        SELECT
            chunk_id,
            run_id::text AS run_id,
            file_id::text AS file_id,
            chunk_index,
            page_start,
            page_end,
            method,
            status,
            LENGTH(text) AS text_chars,
            text_hash_sha256,
            {text_column}
            source_map,
            expires_at,
            created_at,
            updated_at
        FROM text_chunks_staging
        WHERE chunk_id = %s
        """,
        params,
    )
    return rows[0] if rows else None


def list_outbox(
    config: ReviewConfig,
    file_id: str | None = None,
    queue_name: str | None = None,
    status: str | None = "pending",
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    where, params = _build_filters(
        [
            ("file_id = %s", file_id),
            ("queue_name = %s", queue_name),
            ("status = %s", status),
        ]
    )
    params.append(limit)
    return fetch_rows(
        config,
        f"""
        SELECT
            outbox_id::text AS outbox_id,
            run_id::text AS run_id,
            file_id::text AS file_id,
            queue_name,
            status,
            attempts,
            last_error,
            created_at,
            updated_at,
            published_at,
            payload
        FROM queue_outbox
        {where}
        ORDER BY created_at DESC, outbox_id
        LIMIT %s
        """,
        params,
    )


def summarize_file(
    config: ReviewConfig,
    file_id: str,
    show_text: bool | None = None,
    max_text_chars: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    file_rows = fetch_rows(
        config,
        """
        SELECT
            tef.file_id::text AS file_id,
            tef.run_id::text AS run_id,
            f.source_type,
            f.source_uri,
            f.file_name,
            f.relative_path,
            tef.status,
            tef.total_pages,
            tef.completed_pages,
            tef.pending_ocr_pages,
            tef.failed_pages,
            tef.chunk_count,
            tef.started_at,
            tef.completed_at,
            tef.processing_seconds,
            tef.error
        FROM text_extraction_files tef
        JOIN files f ON f.file_id = tef.file_id
        WHERE tef.file_id = %s
        """,
        [file_id],
    )
    pages = list_pages(config, file_id)
    chunks = list_chunks(
        config,
        file_id,
        show_text=show_text,
        max_text_chars=max_text_chars,
        limit=500,
    )
    outbox = list_outbox(config, file_id=file_id, status=None, limit=200)

    display_rows(file_rows, title="Archivo")
    display_rows(pages, title="Paginas")
    display_rows(chunks, title="Chunks temporales")
    display_rows(outbox, title="Outbox del archivo")

    return {
        "file": file_rows,
        "pages": pages,
        "chunks": chunks,
        "outbox": outbox,
    }


def show_chunk_source_map(
    config: ReviewConfig,
    chunk_id: str,
    show_text: bool | None = None,
    max_text_chars: int | None = None,
) -> dict[str, Any] | None:
    chunk = get_chunk(
        config,
        chunk_id,
        show_text=show_text,
        max_text_chars=max_text_chars,
    )
    if chunk is None:
        display_message(f"No chunk found for {chunk_id}")
        return None

    segments = flatten_source_map_segments(chunk.get("source_map") or {})
    display_rows([_without_source_map(chunk)], title="Chunk")
    display_rows(segments, title="Source map segments")
    return chunk


def flatten_source_map_segments(source_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in source_map.get("segments", []) or []:
        row = dict(segment)
        bbox = row.get("bbox")
        if bbox is not None:
            row["bbox"] = json.dumps(bbox, ensure_ascii=False)
        rows.append(row)
    return rows


def fetch_rows(
    config: ReviewConfig,
    sql: str,
    params: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    _validate_select_sql(sql)
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency: install Text_Extract requirements in this "
            "kernel, e.g. `python -m pip install -r requirements.txt`."
        ) from exc

    with psycopg.connect(config.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, list(params or []))
            return [dict(row) for row in cursor.fetchall()]


def display_rows(
    rows: list[Mapping[str, Any]],
    title: str | None = None,
    max_cell_chars: int = 320,
) -> None:
    if not rows:
        display_message(f"{title or 'Result'}: sin filas")
        return

    columns = list(rows[0].keys())
    header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
    body_rows = []
    for row in rows:
        cells = []
        for column in columns:
            cells.append(
                "<td>"
                + _format_cell(row.get(column), max_cell_chars=max_cell_chars)
                + "</td>"
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    caption = f"<h3>{escape(title)}</h3>" if title else ""
    html = (
        caption
        + "<div style='overflow-x:auto'>"
        + "<table style='border-collapse:collapse;font-size:13px'>"
        + "<thead><tr>"
        + header
        + "</tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )
    _display_html(html)


def display_message(message: str) -> None:
    _display_html(f"<p>{escape(message)}</p>")


def _format_cell(value: Any, max_cell_chars: int) -> str:
    if value is None:
        return "<span style='color:#777'>NULL</span>"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = str(value)
    if len(text) > max_cell_chars:
        text = text[:max_cell_chars] + "..."
    return (
        "<pre style='white-space:pre-wrap;margin:0;max-width:540px'>"
        + escape(text)
        + "</pre>"
    )


def _display_html(html: str) -> None:
    try:
        from IPython.display import HTML, display
    except ImportError:
        print(html)
        return

    display(HTML(html))


def _validate_select_sql(sql: str) -> None:
    normalized = sql.strip().lower()
    if not (normalized.startswith("select") or normalized.startswith("with")):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if ";" in normalized.rstrip(";"):
        raise ValueError("Multiple SQL statements are not allowed.")


def _build_filters(filters: list[tuple[str, Any]]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for clause, value in filters:
        if value is None:
            continue
        clauses.append(clause)
        params.append(value)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _like(value: str | None) -> str | None:
    if not value:
        return None
    return f"%{value}%"


def _without_source_map(row: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    copied.pop("source_map", None)
    return copied


def _load_env_without_dependency(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
