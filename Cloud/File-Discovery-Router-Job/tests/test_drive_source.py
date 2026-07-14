from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from cloud_file_router_job.drive_source import DriveDiscoveryAdapter  # noqa: E402


class FakeDriveClient:
    def __init__(self, children_by_folder: dict[str, list[dict[str, Any]]]) -> None:
        self.children_by_folder = children_by_folder
        self.calls: list[str] = []

    def list_children(
        self,
        folder_id: str,
        page_token: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        self.calls.append(folder_id)
        return self.children_by_folder.get(folder_id, []), None


def test_drive_discovery_recurses_and_keeps_google_native_mime() -> None:
    client = FakeDriveClient(
        {
            "root": [
                {
                    "id": "folder-1",
                    "name": "Sub",
                    "mimeType": "application/vnd.google-apps.folder",
                },
                {
                    "id": "doc-1",
                    "name": "Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "version": "3",
                },
            ],
            "folder-1": [
                {
                    "id": "sheet-1",
                    "name": "Sheet",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "version": "7",
                }
            ],
        }
    )

    files = list(DriveDiscoveryAdapter(client).iter_files("root"))

    assert [file.relative_path for file in files] == ["Doc", "Sub/Sheet"]
    assert client.calls == ["root", "folder-1"]
    assert files[0].source_uri == "drive://file/doc-1"
    assert files[0].mime_type == "application/vnd.google-apps.document"
    assert files[1].etag == "7"
