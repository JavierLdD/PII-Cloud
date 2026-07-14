from __future__ import annotations

from pathlib import Path
from typing import Any

from table_extract.materialization.models import (
    DriveContentClient,
    DriveCredentialsError,
    DriveDependencyError,
    DriveNotFoundError,
    DrivePermissionError,
    DriveTokenError,
    DriveTransientError,
)
from table_extract.operational import OperationalException, sanitize_text


DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class GoogleDriveContentClient(DriveContentClient):
    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def from_oauth(
        cls,
        client_secrets_file: str | Path,
        token_file: str | Path,
    ) -> "GoogleDriveContentClient":
        return cls(build_drive_service(client_secrets_file, token_file))

    def download_binary(
        self,
        file_id: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        try:
            request = self._service.files().get_media(
                fileId=file_id,
                supportsAllDrives=True,
            )
        except Exception as exc:
            raise _drive_error_from_exception(exc, operation="download_binary") from exc
        _download_request(request, output_path, progress_callback)

    def export_file(
        self,
        file_id: str,
        export_mime_type: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        try:
            request = self._service.files().export_media(
                fileId=file_id,
                mimeType=export_mime_type,
            )
        except Exception as exc:
            raise _drive_error_from_exception(exc, operation="export_file") from exc
        _download_request(request, output_path, progress_callback)


def build_drive_service(client_secrets_file: str | Path, token_file: str | Path) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise DriveDependencyError(
            "Missing Google Drive dependencies for Table_Extract materialization."
        ) from exc

    token_path = Path(token_file).expanduser()
    client_secrets_path = Path(client_secrets_file).expanduser()
    credentials = None

    if not client_secrets_path.is_file():
        raise DriveCredentialsError(
            "Missing Google Drive client secrets file for Table_Extract materialization.",
            safe_context={"client_secrets_file": str(client_secrets_path)},
        )

    if token_path.exists():
        try:
            credentials = Credentials.from_authorized_user_file(
                str(token_path),
                [DRIVE_READONLY_SCOPE],
            )
        except Exception as exc:
            raise DriveTokenError(
                "Google Drive token file is invalid; delete it and authorize again.",
                safe_context={"token_file": str(token_path)},
            ) from exc

    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise DriveTokenError(
                "Google Drive token refresh failed; delete it and authorize again.",
                safe_context={"token_file": str(token_path)},
            ) from exc

    if not credentials or not credentials.valid:
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secrets_path),
                [DRIVE_READONLY_SCOPE],
            )
            credentials = flow.run_local_server(port=0)
        except Exception as exc:
            raise DriveCredentialsError(
                "Google Drive OAuth authorization failed.",
                safe_context={"client_secrets_file": str(client_secrets_path)},
            ) from exc

    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    except OSError as exc:
        raise DriveCredentialsError(
            "Could not write Google Drive token file.",
            safe_context={"token_file": str(token_path)},
        ) from exc

    try:
        return build("drive", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:
        raise _drive_error_from_exception(exc, operation="build_drive_service") from exc


def _download_request(request: Any, output_path: Path, progress_callback) -> None:
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:
        raise DriveDependencyError(
            "Missing Google Drive dependencies for Table_Extract materialization."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            try:
                _, done = downloader.next_chunk()
                progress_callback(output_path.stat().st_size)
            except Exception as exc:
                raise _drive_error_from_exception(exc, operation="download") from exc


def _drive_error_from_exception(exc: Exception, *, operation: str) -> OperationalException:
    if isinstance(exc, OperationalException):
        return exc

    status = _http_status(exc)
    reason = _http_reason(exc)
    context = {"operation": operation}
    if status is not None:
        context["status_code"] = status

    if status in {401}:
        return DriveTokenError(
            "Google Drive token is invalid or expired; delete it and authorize again.",
            safe_context=context,
        )
    if status in {403} or "appnotauthorizedtofile" in reason.casefold():
        return DrivePermissionError(
            "Google Drive file is not authorized for this account or app.",
            safe_context=context,
        )
    if status in {404} or "notfound" in reason.casefold():
        return DriveNotFoundError(
            "Google Drive file was not found or is not visible to this account.",
            safe_context=context,
        )
    if status == 429 or (status is not None and status >= 500):
        return DriveTransientError(
            "Google Drive request failed with a transient HTTP status.",
            safe_context=context,
        )
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return DriveTransientError(
            f"Google Drive request failed transiently: {exc.__class__.__name__}.",
            safe_context=context,
        )

    return DrivePermissionError(
        f"Google Drive request failed: {sanitize_text(reason or exc.__class__.__name__)}.",
        safe_context=context,
    )


def _http_status(exc: Exception) -> int | None:
    candidates = (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "resp", None), "status", None),
        getattr(getattr(exc, "resp", None), "status_code", None),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _http_reason(exc: Exception) -> str:
    get_reason = getattr(exc, "_get_reason", None)
    if callable(get_reason):
        try:
            return sanitize_text(get_reason())
        except Exception:
            pass
    reason = getattr(exc, "reason", None)
    if reason is not None:
        return sanitize_text(reason)
    content = getattr(exc, "content", None)
    if content is not None:
        if isinstance(content, bytes):
            return sanitize_text(content.decode("utf-8", errors="replace"))
        return sanitize_text(content)
    return sanitize_text(exc)
