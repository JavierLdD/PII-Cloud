from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from cloud_file_router_job.models import DiscoveredFile


DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"


class DriveMetadataClient(Protocol):
    def list_children(
        self,
        folder_id: str,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        ...


class GoogleDriveMetadataClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def list_children(
        self,
        folder_id: str,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        request = (
            self._service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                spaces="drive",
                fields=(
                    "nextPageToken, "
                    "files(id, name, mimeType, size, md5Checksum, "
                    "modifiedTime, version, webViewLink, parents)"
                ),
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
        )
        response = request.execute()
        return list(response.get("files", [])), response.get("nextPageToken")


class DriveDiscoveryAdapter:
    def __init__(self, client: DriveMetadataClient) -> None:
        self._client = client

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "DriveDiscoveryAdapter":
        service = build_drive_service_from_environment(env)
        return cls(GoogleDriveMetadataClient(service))

    def iter_files(self, folder_id: str) -> Iterable[DiscoveredFile]:
        yield from self._iter_folder(folder_id, parent_relative_path="")

    def _iter_folder(
        self,
        folder_id: str,
        parent_relative_path: str,
    ) -> Iterable[DiscoveredFile]:
        page_token: str | None = None
        while True:
            children, page_token = self._client.list_children(folder_id, page_token)
            for child in sorted(children, key=lambda item: str(item.get("name", ""))):
                child_id = str(child["id"])
                child_name = str(child["name"])
                relative_path = (
                    f"{parent_relative_path}/{child_name}"
                    if parent_relative_path
                    else child_name
                )
                mime_type = child.get("mimeType")
                if mime_type == DRIVE_FOLDER_MIME_TYPE:
                    yield from self._iter_folder(child_id, relative_path)
                    continue

                yield build_drive_discovered_file(child, relative_path, folder_id)

            if not page_token:
                break


def build_drive_discovered_file(
    drive_file: dict[str, Any],
    relative_path: str,
    parent_folder_id: str,
) -> DiscoveredFile:
    file_id = str(drive_file["id"])
    file_name = str(drive_file["name"])
    mime_type = drive_file.get("mimeType")
    size_value = drive_file.get("size")
    version_value = drive_file.get("version")
    modified_time = drive_file.get("modifiedTime")
    metadata_json = {
        "parent_folder_id": parent_folder_id,
        "modified_time": modified_time,
        "version": str(version_value) if version_value is not None else None,
        "web_view_link": drive_file.get("webViewLink"),
        "parents": drive_file.get("parents"),
    }

    return DiscoveredFile(
        source_type="drive",
        source_uri=f"drive://file/{file_id}",
        external_id=file_id,
        file_name=file_name,
        relative_path=relative_path,
        extension=Path(file_name).suffix.lower(),
        mime_type=str(mime_type) if mime_type else None,
        size_bytes=int(size_value) if size_value is not None else None,
        checksum_sha256=None,
        content_hash=drive_file.get("md5Checksum"),
        etag=str(version_value) if version_value is not None else modified_time,
        metadata_json={key: value for key, value in metadata_json.items() if value},
    )


def build_drive_service_from_environment(env: Mapping[str, str]) -> Any:
    client_secrets_file = _text_env(env, "GOOGLE_CLIENT_SECRETS_FILE")
    token_file = _text_env(env, "GOOGLE_TOKEN_FILE")
    if client_secrets_file and token_file:
        return build_drive_service_from_oauth(client_secrets_file, token_file)
    return build_drive_service_from_adc()


def build_drive_service_from_adc() -> Any:
    try:
        import google.auth
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Missing Google Drive dependencies.") from exc

    credentials, _ = google.auth.default(scopes=[DRIVE_READONLY_SCOPE])
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def build_drive_service_from_oauth(
    client_secrets_file: str | Path,
    token_file: str | Path,
) -> Any:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError("Missing Google Drive OAuth dependencies.") from exc

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


def _text_env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    text = value.strip()
    return text or None
