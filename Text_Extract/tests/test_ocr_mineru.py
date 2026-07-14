from __future__ import annotations

import io
import json
import sys
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ocr.mineru import (  # noqa: E402
    _HttpResponse,
    _safe_extract_zip,
    html_to_text,
    MinerUExecutionError,
    MinerUConfig,
    parse_mineru_artifacts,
    run_mineru_image,
    run_mineru_pdf_range,
)


def test_parse_mineru_content_list_v2_keeps_bbox_and_table_html(tmp_path):
    content_list_v2 = [
        [
            {
                "type": "paragraph",
                "content": {
                    "paragraph_content": [
                        {"type": "text", "content": "Rut: 12378895-8"}
                    ]
                },
                "bbox": [1, 2, 3, 4],
            },
            {
                "type": "table",
                "content": {
                    "html": "<table><tr><td>Nombre</td><td>Total</td></tr></table>"
                },
                "bbox": [5, 6, 7, 8],
            },
        ]
    ]
    path = tmp_path / "sample_content_list_v2.json"
    path.write_text(json.dumps(content_list_v2), encoding="utf-8")

    blocks = parse_mineru_artifacts(tmp_path, page_index=0)

    assert [block.block_type for block in blocks] == ["paragraph", "table"]
    assert blocks[0].text == "Rut: 12378895-8"
    assert blocks[0].bbox == [1.0, 2.0, 3.0, 4.0]
    assert blocks[1].text == "Nombre Total"
    assert blocks[1].metadata["html"] == (
        "<table><tr><td>Nombre</td><td>Total</td></tr></table>"
    )
    assert blocks[1].metadata["source"] == "content_list_v2"


def test_parse_mineru_falls_back_to_content_list(tmp_path):
    content_list = [
        {
            "type": "table",
            "table_body": "<table><tr><td>A</td><td>B</td></tr></table>",
            "bbox": [9, 8, 7, 6],
        }
    ]
    path = tmp_path / "sample_content_list.json"
    path.write_text(json.dumps(content_list), encoding="utf-8")

    blocks = parse_mineru_artifacts(tmp_path, page_index=0)

    assert len(blocks) == 1
    assert blocks[0].block_type == "table"
    assert blocks[0].text == "A B"
    assert blocks[0].bbox == [9.0, 8.0, 7.0, 6.0]
    assert blocks[0].metadata["source"] == "content_list"


def test_parse_mineru_falls_back_to_markdown(tmp_path):
    (tmp_path / "sample.md").write_text("Texto desde markdown", encoding="utf-8")

    blocks = parse_mineru_artifacts(tmp_path, page_index=0)

    assert len(blocks) == 1
    assert blocks[0].block_type == "markdown"
    assert blocks[0].text == "Texto desde markdown"
    assert blocks[0].bbox is None
    assert blocks[0].metadata["source"] == "markdown"


def test_html_to_text_flattens_table_cells():
    assert html_to_text("<table><tr><td>A</td><td>B</td></tr></table>") == "A B"


def test_pdf_range_uses_mineru_api_task_polling_and_extracts_zip(monkeypatch, tmp_path):
    import ocr.mineru as mineru

    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF")
    output_dir = tmp_path / "out"
    calls: dict[str, list] = {"get": [], "post": [], "download": []}
    statuses = iter(("pending", "processing", "completed"))

    def fake_get(url, timeout_seconds):
        calls["get"].append((url, timeout_seconds))
        if url == "http://mineru.local/health":
            return _HttpResponse(200, b'{"status":"healthy"}')
        return _HttpResponse(
            200,
            json.dumps({"status": next(statuses)}).encode("utf-8"),
        )

    def fake_post(url, fields, file_path, upload_name, timeout_seconds):
        calls["post"].append((url, dict(fields), file_path, upload_name, timeout_seconds))
        return _HttpResponse(
            202,
            json.dumps(
                {
                    "task_id": "task-1",
                    "status_url": "/tasks/task-1",
                    "result_url": "/tasks/task-1/result",
                }
            ).encode("utf-8"),
        )

    def fake_download(url, output_path, timeout_seconds):
        calls["download"].append((url, output_path, timeout_seconds))
        output_path.write_bytes(
            _zip_bytes(
                {
                    "sample_content_list_v2.json": json.dumps(
                        [
                            [
                                {
                                    "type": "paragraph",
                                    "content": {
                                        "paragraph_content": [
                                            {"type": "text", "content": "OCR OK"}
                                        ]
                                    },
                                    "bbox": [1, 2, 3, 4],
                                }
                            ]
                        ]
                    )
                }
            )
        )
        return _HttpResponse(200, b"")

    monkeypatch.setattr(mineru, "_http_get", fake_get)
    monkeypatch.setattr(mineru, "_http_post_multipart", fake_post)
    monkeypatch.setattr(mineru, "_http_download", fake_download)
    monkeypatch.setattr(mineru.time, "sleep", lambda seconds: None)

    run_mineru_pdf_range(
        pdf_path=input_path,
        start_page_index=2,
        end_page_index=4,
        output_dir=output_dir,
        timeout_seconds=30,
        config=MinerUConfig(api_url="http://mineru.local", poll_interval_seconds=0.1),
    )

    assert calls["post"][0][0] == "http://mineru.local/tasks"
    fields = calls["post"][0][1]
    assert fields["backend"] == "pipeline"
    assert fields["lang_list"] == "latin"
    assert fields["parse_method"] == "auto"
    assert fields["return_content_list"] == "true"
    assert fields["response_format_zip"] == "true"
    assert fields["start_page_id"] == "2"
    assert fields["end_page_id"] == "4"
    assert calls["download"][0][0] == "http://mineru.local/tasks/task-1/result"
    assert parse_mineru_artifacts(output_dir, page_index=0)[0].text == "OCR OK"


def test_image_api_request_uses_full_document_range(monkeypatch, tmp_path):
    import ocr.mineru as mineru

    input_path = tmp_path / "scan.png"
    input_path.write_bytes(b"PNG")
    calls: dict[str, list] = {"post": []}

    monkeypatch.setattr(mineru, "_http_get", _fake_health_or_completed)
    def fake_download(url, output_path, timeout_seconds):
        output_path.write_bytes(_zip_bytes({"sample.md": "texto"}))
        return _HttpResponse(200, b"")

    monkeypatch.setattr(mineru, "_http_download", fake_download)

    def fake_post(url, fields, file_path, upload_name, timeout_seconds):
        calls["post"].append(dict(fields))
        return _task_response()

    monkeypatch.setattr(mineru, "_http_post_multipart", fake_post)

    run_mineru_image(
        image_path=input_path,
        output_dir=tmp_path / "out",
        timeout_seconds=30,
        config=MinerUConfig(api_url="http://mineru.local"),
    )

    assert calls["post"][0]["start_page_id"] == "0"
    assert calls["post"][0]["end_page_id"] == "99999"


def test_missing_mineru_api_url_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("MINERU_API_URL", raising=False)
    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF")

    with pytest.raises(MinerUExecutionError, match="MINERU_API_URL is not configured"):
        run_mineru_pdf_range(
            pdf_path=input_path,
            start_page_index=0,
            end_page_index=0,
            output_dir=tmp_path / "out",
            timeout_seconds=30,
        )


def test_health_failure_reports_api_unavailable(monkeypatch, tmp_path):
    import ocr.mineru as mineru

    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF")
    monkeypatch.setattr(mineru, "_http_get", lambda url, timeout_seconds: _HttpResponse(503, b"down"))

    with pytest.raises(MinerUExecutionError, match="MinerU API unavailable"):
        run_mineru_pdf_range(
            pdf_path=input_path,
            start_page_index=0,
            end_page_index=0,
            output_dir=tmp_path / "out",
            timeout_seconds=30,
            config=MinerUConfig(api_url="http://mineru.local"),
        )


def test_failed_task_payload_is_reported(monkeypatch, tmp_path):
    import ocr.mineru as mineru

    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF")

    def fake_get(url, timeout_seconds):
        if url.endswith("/health"):
            return _HttpResponse(200, b'{"status":"healthy"}')
        return _HttpResponse(200, b'{"status":"failed","error":"boom"}')

    monkeypatch.setattr(mineru, "_http_get", fake_get)
    monkeypatch.setattr(mineru, "_http_post_multipart", lambda *args, **kwargs: _task_response())

    with pytest.raises(MinerUExecutionError, match="boom"):
        run_mineru_pdf_range(
            pdf_path=input_path,
            start_page_index=0,
            end_page_index=0,
            output_dir=tmp_path / "out",
            timeout_seconds=30,
            config=MinerUConfig(api_url="http://mineru.local"),
        )


def test_invalid_zip_is_reported(monkeypatch, tmp_path):
    import ocr.mineru as mineru

    input_path = tmp_path / "doc.pdf"
    input_path.write_bytes(b"%PDF")
    monkeypatch.setattr(mineru, "_http_get", _fake_health_or_completed)
    monkeypatch.setattr(mineru, "_http_post_multipart", lambda *args, **kwargs: _task_response())

    def fake_download(url, output_path, timeout_seconds):
        output_path.write_bytes(b"not zip")
        return _HttpResponse(200, b"")

    monkeypatch.setattr(mineru, "_http_download", fake_download)

    with pytest.raises(MinerUExecutionError, match="not a valid ZIP"):
        run_mineru_pdf_range(
            pdf_path=input_path,
            start_page_index=0,
            end_page_index=0,
            output_dir=tmp_path / "out",
            timeout_seconds=30,
            config=MinerUConfig(api_url="http://mineru.local"),
        )


def test_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "unsafe.zip"
    zip_path.write_bytes(_zip_bytes({"../evil.txt": "nope"}))

    with pytest.raises(MinerUExecutionError, match="unsafe MinerU ZIP entry"):
        _safe_extract_zip(zip_path, tmp_path / "out")


def _fake_health_or_completed(url, timeout_seconds):
    if url.endswith("/health"):
        return _HttpResponse(200, b'{"status":"healthy"}')
    return _HttpResponse(200, b'{"status":"completed"}')


def _task_response():
    return _HttpResponse(
        202,
        json.dumps(
            {
                "task_id": "task-1",
                "status_url": "http://mineru.local/tasks/task-1",
                "result_url": "http://mineru.local/tasks/task-1/result",
            }
        ).encode("utf-8"),
    )


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zip_file:
        for name, content in entries.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()
