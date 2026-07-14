from __future__ import annotations

from pathlib import Path
from typing import Any

from materialization.models import DriveContentClient


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

    @classmethod
    def from_adc(cls) -> "GoogleDriveContentClient":
        return cls(build_drive_service_from_adc())

    def download_binary(
        self,
        file_id: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        request = self._service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        _download_request(request, output_path, progress_callback)

    def export_file(
        self,
        file_id: str,
        export_mime_type: str,
        output_path: Path,
        progress_callback,
    ) -> None:
        request = self._service.files().export_media(
            fileId=file_id,
            mimeType=export_mime_type,
        )
        _download_request(request, output_path, progress_callback)


def build_drive_service(client_secrets_file: str | Path, token_file: str | Path) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Missing Google Drive dependencies: install Text_Extract requirements."
        ) from exc

    token_path = Path(token_file).expanduser()
    client_secrets_path = Path(client_secrets_file).expanduser()
    credentials = None

    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            [DRIVE_READONLY_SCOPE],
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path),
            [DRIVE_READONLY_SCOPE],
        )
        credentials = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def build_drive_service_from_adc() -> Any:
    try:
        import google.auth
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Missing Google Drive dependencies: install Text_Extract requirements."
        ) from exc

    credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _download_request(request: Any, output_path: Path, progress_callback) -> None:
    try:
        from googleapiclient.http import MediaIoBaseDownload
    except ImportError as exc:
        raise RuntimeError(
            "Missing Google Drive dependencies: install Text_Extract requirements."
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
            progress_callback(output_path.stat().st_size)
