from __future__ import annotations

from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
from typing import Any, Iterable


DEFAULT_RESULTS_DIR_NAME = "Resultados-Test"
MODULE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = MODULE_DIR / DEFAULT_RESULTS_DIR_NAME
CARD_STYLE = (
    "border:1px solid #374151;border-radius:6px;padding:10px 12px;"
    "min-width:140px;background:#111827;color:#f9fafb"
)
CARD_LABEL_STYLE = "font-size:12px;color:#cbd5e1"
CARD_VALUE_STYLE = (
    "font-size:16px;font-weight:600;color:#f9fafb;overflow-wrap:anywhere"
)
TABLE_STYLE = (
    "border-collapse:collapse;font-size:13px;width:100%;"
    "background:#0f172a;color:#e5e7eb"
)
TABLE_HEADER_STYLE = (
    "text-align:left;border-bottom:2px solid #475569;padding:6px 8px;"
    "background:#1e293b;color:#f8fafc"
)
TABLE_CELL_STYLE = (
    "border-bottom:1px solid #334155;padding:6px 8px;vertical-align:top;"
    "max-width:520px;color:#e5e7eb"
)


def resolve_entity_text_extract_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()

    current = Path.cwd().resolve()
    if current.name == "notebooks" and (current.parent / "models.py").exists():
        return current.parent
    if (current / "models.py").exists() and current.name == "Entity_Text_Extract":
        return current

    return MODULE_DIR


def resolve_results_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    return DEFAULT_OUTPUT_DIR


def resolve_filtered_results_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    return DEFAULT_OUTPUT_DIR


def list_result_files(
    results_dir: str | Path | None = None,
    file_name_contains: str | None = None,
) -> list[dict[str, Any]]:
    root = resolve_results_dir(results_dir)
    if not root.exists():
        return []

    rows: list[dict[str, Any]] = []
    needle = (file_name_contains or "").casefold()
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith("_filtrado.json"):
            continue
        try:
            payload = load_result(path)
        except Exception as exc:
            rows.append(
                {
                    "path": path,
                    "relative_result_path": path.relative_to(root).as_posix(),
                    "file_name": path.name,
                    "relative_path": "",
                    "entity_count": "ERROR",
                    "chunk_count": "",
                    "generated_at": "",
                    "error": str(exc),
                }
            )
            continue

        file_name = str(payload.get("file_name") or path.name)
        relative_path = str(payload.get("relative_path") or "")
        searchable = f"{file_name} {relative_path} {path.name}".casefold()
        if needle and needle not in searchable:
            continue

        rows.append(
            {
                "path": path,
                "relative_result_path": path.relative_to(root).as_posix(),
                "file_name": file_name,
                "relative_path": relative_path,
                "entity_count": int(payload.get("entity_count") or 0),
                "chunk_count": int(payload.get("chunk_count") or 0),
                "generated_at": payload.get("generated_at") or "",
                "error": "",
            }
        )
    rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    for index, row in enumerate(rows):
        row["index"] = index
    return rows


def list_filtered_result_files(
    results_dir: str | Path | None = None,
    file_name_contains: str | None = None,
) -> list[dict[str, Any]]:
    root = resolve_filtered_results_dir(results_dir)
    if not root.exists():
        return []

    rows: list[dict[str, Any]] = []
    needle = (file_name_contains or "").casefold()
    for path in sorted(root.rglob("*_filtrado.json")):
        try:
            payload = load_result(path)
        except Exception as exc:
            rows.append(
                {
                    "path": path,
                    "relative_result_path": path.relative_to(root).as_posix(),
                    "file_name": path.name,
                    "relative_path": "",
                    "accepted_entity_count": "ERROR",
                    "raw_entity_count": "",
                    "chunk_count": "",
                    "generated_at": "",
                    "error": str(exc),
                }
            )
            continue

        file_name = str(payload.get("file_name") or path.name)
        relative_path = str(payload.get("relative_path") or "")
        searchable = f"{file_name} {relative_path} {path.name}".casefold()
        if needle and needle not in searchable:
            continue

        rows.append(
            {
                "path": path,
                "relative_result_path": path.relative_to(root).as_posix(),
                "file_name": file_name,
                "relative_path": relative_path,
                "accepted_entity_count": int(
                    payload.get("accepted_entity_count") or 0
                ),
                "raw_entity_count": int(payload.get("raw_entity_count") or 0),
                "chunk_count": int(payload.get("chunk_count") or 0),
                "generated_at": payload.get("generated_at") or "",
                "error": "",
            }
        )
    rows.sort(key=lambda row: str(row.get("generated_at") or ""), reverse=True)
    for index, row in enumerate(rows):
        row["index"] = index
    return rows


def load_result(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def flatten_entities(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for chunk in result.get("chunks", []):
        for entity in chunk.get("entities", []):
            trace = entity.get("trace") or []
            pages = sorted(
                {
                    item.get("page_number")
                    for item in trace
                    if item.get("page_number") is not None
                }
            )
            blocks = [
                item.get("source_block_id")
                for item in trace
                if item.get("source_block_id") is not None
            ]
            rows.append(
                {
                    "entity_type": entity.get("entity_type"),
                    "value": entity.get("text"),
                    "source": entity.get("source"),
                    "raw_entity_type": entity.get("raw_entity_type"),
                    "score": entity.get("score"),
                    "chunk_index": chunk.get("chunk_index"),
                    "page": ", ".join(str(page) for page in pages),
                    "is_overlap": any(bool(item.get("is_overlap")) for item in trace),
                    "chunk_id": chunk.get("chunk_id"),
                    "blocks": ", ".join(str(block) for block in blocks[:3]),
                    "trace_count": len(trace),
                    "start": entity.get("start"),
                    "end": entity.get("end"),
                    "normalized_value": entity.get("normalized_value"),
                }
            )
    return rows


def flatten_filtered_entities(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, entity in enumerate(result.get("accepted_entities", [])):
        primary_location = entity.get("primary_location") or {}
        trace = primary_location.get("trace") or []
        pages = _trace_pages(trace)
        blocks = _trace_blocks(trace)
        rows.append(
            {
                "index": index,
                "entity_type": entity.get("entity_type"),
                "value": entity.get("text"),
                "source": entity.get("source"),
                "raw_entity_type": entity.get("raw_entity_type"),
                "score": entity.get("score"),
                "is_base": entity.get("is_base"),
                "validation_status": entity.get("validation_status"),
                "evidence_count": entity.get("evidence_count"),
                "chunk_index": primary_location.get("chunk_index"),
                "page": ", ".join(str(page) for page in pages),
                "chunk_id": primary_location.get("chunk_id"),
                "blocks": ", ".join(str(block) for block in blocks[:3]),
                "start": primary_location.get("start"),
                "end": primary_location.get("end"),
                "normalized_value": entity.get("normalized_value"),
            }
        )
    return rows


def flatten_filtered_evidence(
    result: dict[str, Any],
    entity_index: int,
) -> list[dict[str, Any]]:
    entities = result.get("accepted_entities", [])
    if not isinstance(entities, list) or not 0 <= entity_index < len(entities):
        return []

    rows: list[dict[str, Any]] = []
    for evidence in entities[entity_index].get("evidence", []):
        trace = evidence.get("trace") or []
        pages = _trace_pages(trace)
        blocks = _trace_blocks(trace)
        rows.append(
            {
                "entity_type": evidence.get("entity_type"),
                "value": evidence.get("text"),
                "source": evidence.get("source"),
                "raw_entity_type": evidence.get("raw_entity_type"),
                "score": evidence.get("score"),
                "chunk_index": evidence.get("chunk_index"),
                "page": ", ".join(str(page) for page in pages),
                "chunk_id": evidence.get("chunk_id"),
                "blocks": ", ".join(str(block) for block in blocks[:3]),
                "start": evidence.get("start"),
                "end": evidence.get("end"),
                "normalized_value": evidence.get("normalized_value"),
            }
        )
    return rows


def summarize_by_type(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(str(row.get("entity_type") or "") for row in rows)
    return [
        {"entity_type": entity_type, "count": count}
        for entity_type, count in counter.most_common()
    ]


def summarize_by_source(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(str(row.get("source") or "") for row in rows)
    return [
        {"source": source, "count": count}
        for source, count in counter.most_common()
    ]


def summarize_by_type_and_source(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(
        (str(row.get("entity_type") or ""), str(row.get("source") or ""))
        for row in rows
    )
    return [
        {"entity_type": entity_type, "source": source, "count": count}
        for (entity_type, source), count in counter.most_common()
    ]


def filter_entities(
    rows: Iterable[dict[str, Any]],
    entity_type: str | None = None,
    source: str | None = None,
    value_contains: str | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    value_needle = (value_contains or "").casefold()
    for row in rows:
        if entity_type and row.get("entity_type") != entity_type:
            continue
        if source and row.get("source") != source:
            continue
        if value_needle and value_needle not in str(row.get("value") or "").casefold():
            continue
        output.append(row)
    return output


def display_results_index(rows: list[dict[str, Any]], limit: int = 100) -> None:
    display_html_table(
        rows[:limit],
        columns=[
            "index",
            "file_name",
            "relative_path",
            "entity_count",
            "chunk_count",
            "generated_at",
            "relative_result_path",
            "error",
        ],
        title="Resultados disponibles",
    )


def display_filtered_results_index(
    rows: list[dict[str, Any]],
    limit: int = 100,
) -> None:
    display_html_table(
        rows[:limit],
        columns=[
            "index",
            "file_name",
            "relative_path",
            "accepted_entity_count",
            "raw_entity_count",
            "chunk_count",
            "generated_at",
            "relative_result_path",
            "error",
        ],
        title="Resultados filtrados disponibles",
    )


def display_result_dashboard(
    result: dict[str, Any],
    *,
    entity_type: str | None = None,
    source: str | None = None,
    value_contains: str | None = None,
    max_rows: int = 200,
) -> None:
    rows = flatten_entities(result)
    filtered_rows = filter_entities(
        rows,
        entity_type=entity_type,
        source=source,
        value_contains=value_contains,
    )

    display_summary_cards(result, rows, filtered_rows)
    display_html_columns(
        [
            (
                "Tipos de entidad",
                summarize_by_type(filtered_rows),
                ["entity_type", "count"],
            ),
            (
                "Herramientas",
                summarize_by_source(filtered_rows),
                ["source", "count"],
            ),
            (
                "Tipo x herramienta",
                summarize_by_type_and_source(filtered_rows),
                ["entity_type", "source", "count"],
            ),
        ]
    )
    display_html_table(
        filtered_rows[:max_rows],
        columns=[
            "entity_type",
            "value",
            "source",
            "raw_entity_type",
            "score",
            "chunk_index",
            "page",
            "is_overlap",
            "blocks",
            "start",
            "end",
            "normalized_value",
        ],
        title="Entidades raw",
    )


def display_filtered_result_dashboard(
    result: dict[str, Any],
    *,
    entity_type: str | None = None,
    source: str | None = None,
    value_contains: str | None = None,
    max_rows: int = 200,
) -> None:
    rows = flatten_filtered_entities(result)
    filtered_rows = filter_entities(
        rows,
        entity_type=entity_type,
        source=source,
        value_contains=value_contains,
    )

    display_filtered_summary_cards(result, rows, filtered_rows)
    display_html_columns(
        [
            (
                "Tipos de entidad",
                summarize_by_type(filtered_rows),
                ["entity_type", "count"],
            ),
            (
                "Herramientas",
                summarize_by_source(filtered_rows),
                ["source", "count"],
            ),
            (
                "Tipo x herramienta",
                summarize_by_type_and_source(filtered_rows),
                ["entity_type", "source", "count"],
            ),
        ]
    )
    display_html_table(
        filtered_rows[:max_rows],
        columns=[
            "index",
            "entity_type",
            "value",
            "source",
            "raw_entity_type",
            "score",
            "is_base",
            "validation_status",
            "evidence_count",
            "chunk_index",
            "page",
            "blocks",
            "start",
            "end",
            "normalized_value",
        ],
        title="Entidades filtradas",
    )


def display_filtered_evidence(
    result: dict[str, Any],
    entity_index: int,
    max_rows: int = 100,
) -> None:
    rows = flatten_filtered_evidence(result, entity_index)
    display_html_table(
        rows[:max_rows],
        columns=[
            "entity_type",
            "value",
            "source",
            "raw_entity_type",
            "score",
            "chunk_index",
            "page",
            "blocks",
            "start",
            "end",
            "normalized_value",
        ],
        title=f"Evidencia de entidad filtrada #{entity_index}",
    )


def display_summary_cards(
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    filtered_rows: list[dict[str, Any]],
) -> None:
    cards = [
        ("Archivo", result.get("file_name", "")),
        ("Entidades", len(rows)),
        ("Filtradas", len(filtered_rows)),
        ("Chunks", result.get("chunk_count", "")),
        ("Generado", _format_dt(result.get("generated_at", ""))),
    ]
    html = ["<div style='display:flex;gap:10px;flex-wrap:wrap;margin:12px 0'>"]
    for label, value in cards:
        html.append(
            f"<div style='{CARD_STYLE}'>"
            f"<div style='{CARD_LABEL_STYLE}'>{escape(str(label))}</div>"
            f"<div style='{CARD_VALUE_STYLE}'>{escape(str(value))}</div>"
            "</div>"
        )
    html.append("</div>")
    _display_html("".join(html))


def display_filtered_summary_cards(
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    filtered_rows: list[dict[str, Any]],
) -> None:
    cards = [
        ("Archivo", result.get("file_name", "")),
        ("Raw", result.get("raw_entity_count", "")),
        ("Aceptadas", len(rows)),
        ("Mostradas", len(filtered_rows)),
        ("Chunks", result.get("chunk_count", "")),
        ("Generado", _format_dt(result.get("generated_at", ""))),
    ]
    html = ["<div style='display:flex;gap:10px;flex-wrap:wrap;margin:12px 0'>"]
    for label, value in cards:
        html.append(
            f"<div style='{CARD_STYLE}'>"
            f"<div style='{CARD_LABEL_STYLE}'>{escape(str(label))}</div>"
            f"<div style='{CARD_VALUE_STYLE}'>{escape(str(value))}</div>"
            "</div>"
        )
    html.append("</div>")
    _display_html("".join(html))


def display_html_columns(
    tables: list[tuple[str, list[dict[str, Any]], list[str]]],
) -> None:
    html = ["<div style='display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap'>"]
    for title, rows, columns in tables:
        html.append("<div style='min-width:260px;max-width:420px'>")
        html.append(_html_table(rows, columns, title=title))
        html.append("</div>")
    html.append("</div>")
    _display_html("".join(html))


def display_html_table(
    rows: list[dict[str, Any]],
    columns: list[str],
    title: str,
) -> None:
    _display_html(_html_table(rows, columns, title=title))


def _html_table(rows: list[dict[str, Any]], columns: list[str], title: str) -> str:
    html = [
        f"<h3 style='margin:14px 0 8px;color:#f8fafc'>{escape(title)}</h3>",
        "<div style='overflow-x:auto'>",
        f"<table style='{TABLE_STYLE}'>",
        "<thead><tr>",
    ]
    for column in columns:
        html.append(
            f"<th style='{TABLE_HEADER_STYLE}'>"
            f"{escape(column)}"
            "</th>"
        )
    html.append("</tr></thead><tbody>")
    if not rows:
        html.append(
            f"<tr><td colspan='{len(columns)}' style='padding:10px;color:#cbd5e1'>"
            "Sin filas para mostrar"
            "</td></tr>"
        )
    for row in rows:
        html.append("<tr>")
        for column in columns:
            value = row.get(column, "")
            html.append(
                f"<td style='{TABLE_CELL_STYLE}'>"
                f"{_format_cell(value)}"
                "</td>"
            )
        html.append("</tr>")
    html.append("</tbody></table></div>")
    return "".join(html)


def _format_cell(value: Any) -> str:
    if value is None:
        return "<span style='color:#94a3b8'>NULL</span>"
    if isinstance(value, float):
        value = round(value, 6)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = escape(str(value))
    return (
        "<pre style='white-space:pre-wrap;margin:0;color:inherit;"
        f"overflow-wrap:anywhere'>{text}</pre>"
    )


def _format_dt(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


def _trace_pages(trace: list[dict[str, Any]]) -> list[Any]:
    return sorted(
        {
            item.get("page_number")
            for item in trace
            if isinstance(item, dict) and item.get("page_number") is not None
        }
    )


def _trace_blocks(trace: list[dict[str, Any]]) -> list[Any]:
    return [
        item.get("source_block_id")
        for item in trace
        if isinstance(item, dict) and item.get("source_block_id") is not None
    ]


def _display_html(html: str) -> None:
    try:
        from IPython.display import HTML, display
    except ImportError:
        print(html)
        return
    display(HTML(html))
