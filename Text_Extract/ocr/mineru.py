from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
import html
import json
import mimetypes
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

from common.models import OcrWorkMessage


MINERU_BACKEND = "pipeline"
MINERU_LANG = "latin"
DEFAULT_MINERU_TIMEOUT_SECONDS = 6000
DEFAULT_MINERU_API_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MINERU_API_SUBMIT_TIMEOUT_SECONDS = 300.0
MINERU_API_URL_ENV = "MINERU_API_URL"
MINERU_API_POLL_INTERVAL_ENV = "MINERU_API_POLL_INTERVAL_SECONDS"
MINERU_API_SUBMIT_TIMEOUT_ENV = "MINERU_API_SUBMIT_TIMEOUT_SECONDS"
MINERU_API_RESULT_TIMEOUT_ENV = "MINERU_API_RESULT_TIMEOUT_SECONDS"


class MinerUExecutionError(RuntimeError):
    """Raised when the MinerU API fails for a deterministic page/file attempt."""


@dataclass(frozen=True)
class MinerUConfig:
    timeout_seconds: int = DEFAULT_MINERU_TIMEOUT_SECONDS
    keep_artifacts: bool = False
    artifact_root: Path | None = None
    device: str = "api"
    api_url: str | None = None
    poll_interval_seconds: float | None = None
    submit_timeout_seconds: float | None = None


@dataclass(frozen=True)
class MinerUDeviceInfo:
    requested_device: str
    effective_device: str
    cuda_available: bool
    gpu_name: str | None
    cuda_visible_devices: str | None
    mineru_device: str | None


@dataclass(frozen=True)
class MinerUBlock:
    block_type: str
    text: str
    bbox: list[float] | None
    metadata: dict[str, Any] = field(default_factory=dict)


@contextmanager
def mineru_artifact_directory(
    config: MinerUConfig,
    message: OcrWorkMessage,
) -> Iterator[Path]:
    if config.keep_artifacts:
        root = (
            config.artifact_root.expanduser()
            if config.artifact_root is not None
            else Path(tempfile.mkdtemp(prefix="text_extract_ocr_"))
        )
        output_dir = _message_artifact_dir(root, message)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        yield output_dir
        return

    with tempfile.TemporaryDirectory(prefix="text_extract_ocr_") as tmp_dir:
        output_dir = _message_artifact_dir(Path(tmp_dir), message)
        output_dir.mkdir(parents=True, exist_ok=True)
        yield output_dir


def run_mineru_pdf_page(
    pdf_path: Path,
    page_index: int,
    output_dir: Path,
    timeout_seconds: int,
    device: str = "auto",
    config: MinerUConfig | None = None,
) -> None:
    run_mineru_pdf_range(
        pdf_path=pdf_path,
        start_page_index=page_index,
        end_page_index=page_index,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        device=device,
        config=config,
    )


def run_mineru_pdf_range(
    pdf_path: Path,
    start_page_index: int,
    end_page_index: int,
    output_dir: Path,
    timeout_seconds: int,
    device: str = "auto",
    config: MinerUConfig | None = None,
) -> None:
    if start_page_index < 0:
        raise ValueError(f"Invalid start_page_index: {start_page_index}")
    if end_page_index < start_page_index:
        raise ValueError(
            "end_page_index must be greater than or equal to start_page_index"
        )
    _run_mineru_api(
        input_path=pdf_path,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        device=device,
        start_page_id=start_page_index,
        end_page_id=end_page_index,
        config=config,
    )


def run_mineru_image(
    image_path: Path,
    output_dir: Path,
    timeout_seconds: int,
    device: str = "auto",
    config: MinerUConfig | None = None,
) -> None:
    _run_mineru_api(
        input_path=image_path,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        device=device,
        start_page_id=0,
        end_page_id=None,
        config=config,
    )


def parse_mineru_artifacts(
    artifact_dir: Path,
    page_index: int,
    fallback_page_index: int | None = None,
    allow_unpaged_fallback: bool = True,
) -> list[MinerUBlock]:
    for path in _candidate_files(artifact_dir, "*_content_list_v2.json"):
        data = _load_json(path)
        if data is not None:
            blocks = _parse_content_list_v2(data, page_index, path)
            if not blocks and fallback_page_index is not None:
                blocks = _parse_content_list_v2(data, fallback_page_index, path)
            if blocks:
                return blocks

    if allow_unpaged_fallback:
        for path in _candidate_files(artifact_dir, "*_content_list.json"):
            data = _load_json(path)
            if data is not None:
                blocks = _parse_content_list(data, path)
                if blocks:
                    return blocks

        for path in _candidate_files(artifact_dir, "*.md"):
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return [
                    MinerUBlock(
                        block_type="markdown",
                        text=text,
                        bbox=None,
                        metadata={
                            "source": "markdown",
                            "markdown_path": str(path),
                        },
                    )
                ]

    return []


def resolve_mineru_device_info(
    device: str,
    require_cuda: bool = False,
) -> MinerUDeviceInfo:
    if require_cuda:
        raise MinerUExecutionError(
            "Local CUDA device selection is not supported by the MinerU API worker"
        )

    return MinerUDeviceInfo(
        requested_device="api",
        effective_device="api",
        cuda_available=False,
        gpu_name=None,
        cuda_visible_devices=None,
        mineru_device=None,
    )


def html_to_text(markup: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(markup)
    parser.close()
    return " ".join(parser.parts).strip()


@dataclass(frozen=True)
class _HttpResponse:
    status_code: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _TaskSubmission:
    task_id: str
    status_url: str
    result_url: str


def _run_mineru_api(
    input_path: Path,
    output_dir: Path,
    timeout_seconds: int,
    device: str,
    start_page_id: int,
    end_page_id: int | None,
    config: MinerUConfig | None,
) -> None:
    effective_config = _effective_config(config, timeout_seconds, device)
    api_url = _resolve_api_url(effective_config)
    result_timeout = _resolve_result_timeout(effective_config)
    submit_timeout = _resolve_submit_timeout(effective_config)
    poll_interval = _resolve_poll_interval(effective_config)

    _ensure_api_healthy(api_url, submit_timeout)
    submission = _submit_task(
        api_url=api_url,
        input_path=input_path,
        start_page_id=start_page_id,
        end_page_id=end_page_id,
        timeout_seconds=submit_timeout,
    )
    _wait_for_task_result(
        submission=submission,
        timeout_seconds=result_timeout,
        poll_interval_seconds=poll_interval,
    )
    _download_and_extract_result(
        submission=submission,
        output_dir=output_dir,
        timeout_seconds=result_timeout,
    )


def _effective_config(
    config: MinerUConfig | None,
    timeout_seconds: int,
    device: str,
) -> MinerUConfig:
    if config is not None:
        return config
    return MinerUConfig(timeout_seconds=timeout_seconds, device=device or "api")


def _resolve_api_url(config: MinerUConfig) -> str:
    api_url = config.api_url or os.environ.get(MINERU_API_URL_ENV)
    if api_url is None or api_url.strip() == "":
        raise MinerUExecutionError(
            f"MinerU API unavailable: {MINERU_API_URL_ENV} is not configured"
        )
    return api_url.strip().rstrip("/")


def _resolve_result_timeout(config: MinerUConfig) -> float:
    return _float_env(
        MINERU_API_RESULT_TIMEOUT_ENV,
        float(config.timeout_seconds),
        minimum=1.0,
    )


def _resolve_submit_timeout(config: MinerUConfig) -> float:
    if config.submit_timeout_seconds is not None:
        return max(1.0, float(config.submit_timeout_seconds))
    return _float_env(
        MINERU_API_SUBMIT_TIMEOUT_ENV,
        DEFAULT_MINERU_API_SUBMIT_TIMEOUT_SECONDS,
        minimum=1.0,
    )


def _resolve_poll_interval(config: MinerUConfig) -> float:
    if config.poll_interval_seconds is not None:
        return max(0.1, float(config.poll_interval_seconds))
    return _float_env(
        MINERU_API_POLL_INTERVAL_ENV,
        DEFAULT_MINERU_API_POLL_INTERVAL_SECONDS,
        minimum=0.1,
    )


def _float_env(name: str, default: float, minimum: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        resolved = float(value)
    except ValueError:
        return default
    return resolved if resolved >= minimum else default


def _ensure_api_healthy(api_url: str, timeout_seconds: float) -> None:
    health_url = f"{api_url}/health"
    try:
        response = _http_get(health_url, timeout_seconds)
    except MinerUExecutionError as exc:
        raise MinerUExecutionError(f"MinerU API unavailable: {exc}") from exc

    if response.status_code != 200:
        raise MinerUExecutionError(
            "MinerU API unavailable: "
            f"GET /health returned {response.status_code} {_response_detail(response)}"
        )

    payload = _decode_json_response(response, "MinerU API health")
    status = payload.get("status")
    if status is not None and status != "healthy":
        raise MinerUExecutionError(
            "MinerU API unavailable: "
            f"GET /health returned {json.dumps(payload, ensure_ascii=False)}"
        )


def _submit_task(
    api_url: str,
    input_path: Path,
    start_page_id: int,
    end_page_id: int | None,
    timeout_seconds: float,
) -> _TaskSubmission:
    fields = _build_parse_form_fields(start_page_id, end_page_id)
    response = _http_post_multipart(
        url=f"{api_url}/tasks",
        fields=fields,
        file_path=input_path,
        upload_name=input_path.name,
        timeout_seconds=timeout_seconds,
    )
    if response.status_code not in {200, 202}:
        raise MinerUExecutionError(
            "MinerU API task submission failed: "
            f"{response.status_code} {_response_detail(response)}"
        )

    payload = _decode_json_response(response, "MinerU API task submission")
    task_id = payload.get("task_id")
    status_url = payload.get("status_url")
    result_url = payload.get("result_url")
    if not isinstance(task_id, str) or not isinstance(status_url, str) or not isinstance(
        result_url,
        str,
    ):
        raise MinerUExecutionError(
            "MinerU API task submission returned an invalid payload: "
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
    return _TaskSubmission(
        task_id=task_id,
        status_url=_absolute_url(api_url, status_url),
        result_url=_absolute_url(api_url, result_url),
    )


def _wait_for_task_result(
    submission: _TaskSubmission,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    status_timeout_seconds = min(60.0, max(5.0, poll_interval_seconds * 2.0))
    while time.monotonic() < deadline:
        response = _http_get(
            submission.status_url,
            timeout_seconds=status_timeout_seconds,
        )
        if response.status_code != 200:
            raise MinerUExecutionError(
                "MinerU API task status failed: "
                f"{response.status_code} {_response_detail(response)}"
            )

        payload = _decode_json_response(response, "MinerU API task status")
        status = payload.get("status")
        if status == "completed":
            return
        if status in {"pending", "processing"}:
            time.sleep(poll_interval_seconds)
            continue

        raise MinerUExecutionError(
            f"MinerU task {submission.task_id} failed: "
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

    raise MinerUExecutionError(
        "MinerU API task timed out: "
        f"task_id={submission.task_id} timeout_seconds={timeout_seconds}"
    )


def _download_and_extract_result(
    submission: _TaskSubmission,
    output_dir: Path,
    timeout_seconds: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "_mineru_result.zip"
    try:
        response = _http_download(
            submission.result_url,
            zip_path,
            timeout_seconds=timeout_seconds,
        )
        if response.status_code != 200:
            raise MinerUExecutionError(
                "MinerU API result download failed: "
                f"{response.status_code} {_response_detail(response)}"
            )
        _safe_extract_zip(zip_path, output_dir)
    finally:
        zip_path.unlink(missing_ok=True)


def _build_parse_form_fields(
    start_page_id: int,
    end_page_id: int | None,
) -> list[tuple[str, str]]:
    return [
        ("lang_list", MINERU_LANG),
        ("backend", MINERU_BACKEND),
        ("parse_method", "auto"),
        ("formula_enable", "true"),
        ("table_enable", "true"),
        ("image_analysis", "true"),
        ("return_md", "true"),
        ("return_middle_json", "true"),
        ("return_model_output", "false"),
        ("return_content_list", "true"),
        ("return_images", "true"),
        ("response_format_zip", "true"),
        ("return_original_file", "false"),
        ("client_side_output_generation", "false"),
        ("start_page_id", str(start_page_id)),
        ("end_page_id", str(99999 if end_page_id is None else end_page_id)),
    ]


def _http_get(url: str, timeout_seconds: float) -> _HttpResponse:
    try:
        import httpx  # type: ignore[import-not-found]
    except Exception:
        return _urllib_get(url, timeout_seconds)

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise MinerUExecutionError(str(exc)) from exc
    return _HttpResponse(
        status_code=response.status_code,
        body=response.content,
        headers={key.lower(): value for key, value in response.headers.items()},
    )


def _http_post_multipart(
    url: str,
    fields: list[tuple[str, str]],
    file_path: Path,
    upload_name: str,
    timeout_seconds: float,
) -> _HttpResponse:
    mime_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    try:
        import httpx  # type: ignore[import-not-found]
    except Exception:
        return _urllib_post_multipart(
            url,
            fields,
            file_path,
            upload_name,
            mime_type,
            timeout_seconds,
        )

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            with file_path.open("rb") as handle:
                response = client.post(
                    url,
                    data=fields,
                    files={"files": (upload_name, handle, mime_type)},
                )
    except httpx.HTTPError as exc:
        raise MinerUExecutionError(str(exc)) from exc
    except OSError as exc:
        raise MinerUExecutionError(f"Could not read MinerU input file: {exc}") from exc
    return _HttpResponse(
        status_code=response.status_code,
        body=response.content,
        headers={key.lower(): value for key, value in response.headers.items()},
    )


def _http_download(
    url: str,
    output_path: Path,
    timeout_seconds: float,
) -> _HttpResponse:
    try:
        import httpx  # type: ignore[import-not-found]
    except Exception:
        return _urllib_download(url, output_path, timeout_seconds)

    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return _HttpResponse(
                        status_code=response.status_code,
                        body=response.read(),
                        headers={
                            key.lower(): value for key, value in response.headers.items()
                        },
                    )
                with output_path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                return _HttpResponse(
                    status_code=response.status_code,
                    body=b"",
                    headers={
                        key.lower(): value for key, value in response.headers.items()
                    },
                )
    except httpx.HTTPError as exc:
        raise MinerUExecutionError(str(exc)) from exc
    except OSError as exc:
        raise MinerUExecutionError(f"Could not write MinerU result ZIP: {exc}") from exc


def _urllib_get(url: str, timeout_seconds: float) -> _HttpResponse:
    request = urllib.request.Request(url, method="GET")
    return _urllib_request(request, timeout_seconds)


def _urllib_post_multipart(
    url: str,
    fields: list[tuple[str, str]],
    file_path: Path,
    upload_name: str,
    mime_type: str,
    timeout_seconds: float,
) -> _HttpResponse:
    content_type, body = _encode_multipart_body(
        fields=fields,
        file_path=file_path,
        upload_name=upload_name,
        mime_type=mime_type,
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    return _urllib_request(request, timeout_seconds)


def _urllib_download(
    url: str,
    output_path: Path,
    timeout_seconds: float,
) -> _HttpResponse:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = int(response.getcode())
            headers = {key.lower(): value for key, value in response.headers.items()}
            if status_code != 200:
                return _HttpResponse(
                    status_code=status_code,
                    body=response.read(),
                    headers=headers,
                )
            with output_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            return _HttpResponse(status_code=status_code, body=b"", headers=headers)
    except urllib.error.HTTPError as exc:
        return _HttpResponse(status_code=exc.code, body=exc.read(), headers={})
    except urllib.error.URLError as exc:
        raise MinerUExecutionError(str(exc.reason)) from exc
    except OSError as exc:
        raise MinerUExecutionError(str(exc)) from exc


def _urllib_request(
    request: urllib.request.Request,
    timeout_seconds: float,
) -> _HttpResponse:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return _HttpResponse(
                status_code=int(response.getcode()),
                body=response.read(),
                headers={key.lower(): value for key, value in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        return _HttpResponse(status_code=exc.code, body=exc.read(), headers={})
    except urllib.error.URLError as exc:
        raise MinerUExecutionError(str(exc.reason)) from exc
    except OSError as exc:
        raise MinerUExecutionError(str(exc)) from exc


def _encode_multipart_body(
    fields: list[tuple[str, str]],
    file_path: Path,
    upload_name: str,
    mime_type: str,
) -> tuple[str, bytes]:
    boundary = f"----mineru-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    try:
        file_bytes = file_path.read_bytes()
    except OSError as exc:
        raise MinerUExecutionError(f"Could not read MinerU input file: {exc}") from exc

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="files"; filename="{upload_name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", bytes(body)


def _decode_json_response(response: _HttpResponse, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinerUExecutionError(
            f"{label} returned invalid JSON: {_response_detail(response)}"
        ) from exc
    if not isinstance(payload, dict):
        raise MinerUExecutionError(
            f"{label} returned an invalid JSON payload: {payload!r}"
        )
    return payload


def _response_detail(response: _HttpResponse) -> str:
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return response.body.decode("utf-8", errors="replace").strip()

    if isinstance(payload, dict):
        for key in ("detail", "error", "message"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return json.dumps(payload, ensure_ascii=False)


def _absolute_url(api_url: str, value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{api_url}/{value.lstrip('/')}"


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> None:
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_file:
            output_root = output_dir.resolve()
            for member in zip_file.infolist():
                member_path = PurePosixPath(member.filename)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise MinerUExecutionError(
                        f"Refusing to extract unsafe MinerU ZIP entry: {member.filename}"
                    )
                target_path = (output_root / Path(*member_path.parts)).resolve()
                if target_path != output_root and output_root not in target_path.parents:
                    raise MinerUExecutionError(
                        f"Refusing to extract unsafe MinerU ZIP entry: {member.filename}"
                    )
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(member, "r") as source, target_path.open(
                    "wb"
                ) as target:
                    shutil.copyfileobj(source, target)
    except zipfile.BadZipFile as exc:
        raise MinerUExecutionError("MinerU API result is not a valid ZIP") from exc


def _message_artifact_dir(root: Path, message: OcrWorkMessage) -> Path:
    file_id = _safe_identifier(message.file_id)
    if message.is_pdf_batch:
        return root / file_id / "batch"
    if message.is_pdf_page:
        return root / file_id / f"page_{message.page_number:04d}"
    return root / file_id / "image"


def _safe_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return sanitized or "ocr_input"


def _candidate_files(artifact_dir: Path, pattern: str) -> list[Path]:
    return sorted(
        path
        for path in artifact_dir.rglob(pattern)
        if ".ipynb_checkpoints" not in path.parts
    )


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _parse_content_list_v2(
    data: Any,
    page_index: int,
    source_path: Path,
) -> list[MinerUBlock]:
    if not isinstance(data, list):
        return []

    page_blocks = _select_v2_page_blocks(data, page_index)
    parsed: list[MinerUBlock] = []

    for block in page_blocks:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type", "unknown"))
        content = block.get("content", {})
        text, metadata = _text_from_v2_content(block_type, content)
        text = text.strip()
        if not text:
            continue

        metadata.update(
            {
                "source": "content_list_v2",
                "artifact_path": str(source_path),
            }
        )
        parsed.append(
            MinerUBlock(
                block_type=block_type,
                text=text,
                bbox=_normalize_bbox(block.get("bbox")),
                metadata=metadata,
            )
        )

    return parsed


def _select_v2_page_blocks(data: list[Any], page_index: int) -> list[Any]:
    if data and all(isinstance(item, list) for item in data):
        if len(data) == 1:
            return data[0]
        if 0 <= page_index < len(data):
            return data[page_index]
        return []

    return data


def _text_from_v2_content(
    block_type: str,
    content: Any,
) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if not isinstance(content, dict):
        return _coerce_text(content), metadata

    if block_type == "table":
        table_html = str(content.get("html", "")).strip()
        if table_html:
            metadata["html"] = table_html
            return html_to_text(table_html), metadata

    if "title_content" in content:
        return _join_content_items(content.get("title_content")), metadata
    if "paragraph_content" in content:
        return _join_content_items(content.get("paragraph_content")), metadata
    if "image_caption" in content:
        return _join_content_items(content.get("image_caption")), metadata

    return _join_known_text_values(content), metadata


def _parse_content_list(data: Any, source_path: Path) -> list[MinerUBlock]:
    if not isinstance(data, list):
        return []

    parsed: list[MinerUBlock] = []
    for block in data:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type", "unknown"))
        metadata: dict[str, Any] = {
            "source": "content_list",
            "artifact_path": str(source_path),
        }
        if block_type == "table":
            table_html = str(block.get("table_body", "")).strip()
            if table_html:
                metadata["html"] = table_html
                text = html_to_text(table_html)
            else:
                text = ""
        else:
            text = _coerce_text(block.get("text", "")).strip()

        if not text:
            continue

        parsed.append(
            MinerUBlock(
                block_type=block_type,
                text=text,
                bbox=_normalize_bbox(block.get("bbox")),
                metadata=metadata,
            )
        )

    return parsed


def _join_content_items(items: Any) -> str:
    if not isinstance(items, list):
        return _coerce_text(items)

    parts: list[str] = []
    for item in items:
        if isinstance(item, dict):
            parts.append(_coerce_text(item.get("content", "")))
        else:
            parts.append(_coerce_text(item))

    return " ".join(part.strip() for part in parts if part.strip())


def _join_known_text_values(content: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "text",
        "content",
        "paragraph_content",
        "title_content",
        "table_caption",
        "table_footnote",
        "image_caption",
        "image_footnote",
    ):
        if key in content:
            parts.append(_coerce_text(content[key]))

    return " ".join(part.strip() for part in parts if part.strip())


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _join_content_items(value)
    if isinstance(value, dict):
        return _join_known_text_values(value)
    return str(value)


def _normalize_bbox(value: Any) -> list[float] | None:
    if value is None:
        return None
    try:
        bbox = [float(number) for number in value]
    except (TypeError, ValueError):
        return None
    if len(bbox) != 4:
        return None
    return bbox


class _HTMLTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = html.unescape(data).strip()
        if text:
            self.parts.append(text)
