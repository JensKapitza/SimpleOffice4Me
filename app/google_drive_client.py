"""Small Google Drive v3 client with strict HTTPS and response limits."""

from __future__ import annotations

import json
import secrets
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DRIVE_CHANGES_URL = "https://www.googleapis.com/drive/v3/changes"
DRIVE_START_TOKEN_URL = "https://www.googleapis.com/drive/v3/changes/startPageToken"
FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."
MAX_JSON_BYTES = 16 * 1024 * 1024


def _allowed_google_host(hostname: str) -> bool:
    host = str(hostname or "").casefold()
    return host in {"www.googleapis.com", "content.googleapis.com"} or host.endswith(".googleusercontent.com")


def _validate_google_url(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    if (
        parsed.scheme != "https"
        or not _allowed_google_host(parsed.hostname or "")
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise ValueError("Google Drive URL is not allowed")
    return parsed.geturl()


class _GoogleDriveRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_google_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_GOOGLE_DRIVE_OPENER = build_opener(_GoogleDriveRedirectHandler())


def _read_bounded(response, max_bytes: int) -> bytes:
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            length = int(declared)
        except (TypeError, ValueError) as exc:
            raise ValueError("Google Drive returned invalid Content-Length") from exc
        if length < 0 or length > max_bytes:
            raise ValueError("Google Drive response is too large")
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("Google Drive response is too large")
    return raw


class DriveClient:
    def __init__(self, access_token: str):
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("Google access token is required")
        self.access_token = token

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        max_bytes: int = MAX_JSON_BYTES,
    ) -> bytes:
        safe_url = _validate_google_url(url)
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = content_type
        request = Request(safe_url, data=body, headers=headers, method=method)
        with _GOOGLE_DRIVE_OPENER.open(request, timeout=45) as response:
            _validate_google_url(response.geturl())
            return _read_bounded(response, max_bytes)

    def _json(self, method: str, url: str, *, body: bytes | None = None, content_type: str = "application/json") -> dict:
        raw = self._request(method, url, body=body, content_type=content_type)
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(payload, dict):
            raise ValueError("Google Drive returned invalid JSON")
        return payload

    @staticmethod
    def _fields() -> str:
        return "id,name,mimeType,md5Checksum,modifiedTime,version,size,webViewLink,parents,trashed,appProperties"

    def get_start_page_token(self) -> str:
        payload = self._json("GET", f"{DRIVE_START_TOKEN_URL}?{urlencode({'supportsAllDrives': 'false'})}")
        token = str(payload.get("startPageToken", "")).strip()
        if not token:
            raise RuntimeError("Google Drive did not return a start page token")
        return token

    def list_changes(self, page_token: str) -> tuple[list[dict], str]:
        token = str(page_token or "").strip()
        if not token:
            raise ValueError("Drive page token is required")
        changes: list[dict] = []
        next_token = token
        while next_token:
            params = {
                "pageToken": next_token,
                "pageSize": "1000",
                "includeRemoved": "true",
                "restrictToMyDrive": "true",
                "fields": f"nextPageToken,newStartPageToken,changes(fileId,removed,file({self._fields()}))",
            }
            payload = self._json("GET", f"{DRIVE_CHANGES_URL}?{urlencode(params)}")
            batch = payload.get("changes", [])
            if isinstance(batch, list):
                changes.extend(item for item in batch if isinstance(item, dict))
            following = str(payload.get("nextPageToken", "")).strip()
            if following:
                next_token = following
                continue
            return changes, str(payload.get("newStartPageToken", token)).strip() or token
        return changes, token

    def list_children(self, parent_id: str) -> list[dict]:
        parent = str(parent_id or "").strip().replace("'", "")
        if not parent:
            raise ValueError("Drive parent ID is required")
        results: list[dict] = []
        page_token = ""
        while True:
            params = {
                "q": f"'{parent}' in parents and trashed = false",
                "pageSize": "1000",
                "spaces": "drive",
                "fields": f"nextPageToken,files({self._fields()})",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json("GET", f"{DRIVE_FILES_URL}?{urlencode(params)}")
            items = payload.get("files", [])
            if isinstance(items, list):
                results.extend(item for item in items if isinstance(item, dict))
            page_token = str(payload.get("nextPageToken", "")).strip()
            if not page_token:
                return results

    def find_managed_root(self) -> dict | None:
        params = {
            "q": "appProperties has { key='simpleoffice4me_root' and value='1' } and trashed = false",
            "pageSize": "10",
            "spaces": "drive",
            "fields": f"files({self._fields()})",
        }
        payload = self._json("GET", f"{DRIVE_FILES_URL}?{urlencode(params)}")
        files = payload.get("files", [])
        if not isinstance(files, list):
            return None
        for item in files:
            if isinstance(item, dict) and item.get("mimeType") == FOLDER_MIME:
                return item
        return None

    def create_folder(self, name: str, parent_id: str | None = None, *, root_marker: bool = False) -> dict:
        metadata: dict[str, object] = {"name": str(name), "mimeType": FOLDER_MIME}
        if parent_id:
            metadata["parents"] = [str(parent_id)]
        if root_marker:
            metadata["appProperties"] = {"simpleoffice4me_root": "1"}
        params = urlencode({"fields": self._fields()})
        return self._json("POST", f"{DRIVE_FILES_URL}?{params}", body=json.dumps(metadata).encode("utf-8"))

    def get_file(self, file_id: str) -> dict:
        encoded = quote(str(file_id or "").strip(), safe="")
        if not encoded:
            raise ValueError("Drive file ID is required")
        params = urlencode({"fields": self._fields()})
        return self._json("GET", f"{DRIVE_FILES_URL}/{encoded}?{params}")

    @staticmethod
    def _multipart(metadata: dict, content: bytes, mime_type: str) -> tuple[bytes, str]:
        boundary = f"simpleoffice-{secrets.token_hex(12)}"
        head = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata, ensure_ascii=False)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("ascii")
        return head + bytes(content) + tail, f"multipart/related; boundary={boundary}"

    def create_file(self, parent_id: str, name: str, content: bytes, mime_type: str) -> dict:
        metadata = {
            "name": str(name),
            "parents": [str(parent_id)],
            "appProperties": {"simpleoffice4me_managed": "1"},
        }
        body, content_type = self._multipart(metadata, content, mime_type)
        params = urlencode({"uploadType": "multipart", "fields": self._fields()})
        return self._json("POST", f"{DRIVE_UPLOAD_URL}?{params}", body=body, content_type=content_type)

    def update_file(self, file_id: str, name: str, content: bytes, mime_type: str) -> dict:
        encoded = quote(str(file_id or "").strip(), safe="")
        if not encoded:
            raise ValueError("Drive file ID is required")
        metadata = {"name": str(name)}
        body, content_type = self._multipart(metadata, content, mime_type)
        params = urlencode({"uploadType": "multipart", "fields": self._fields()})
        return self._json("PATCH", f"{DRIVE_UPLOAD_URL}/{encoded}?{params}", body=body, content_type=content_type)

    def download_file(self, file_id: str, max_bytes: int) -> bytes:
        encoded = quote(str(file_id or "").strip(), safe="")
        if not encoded:
            raise ValueError("Drive file ID is required")
        params = urlencode({"alt": "media"})
        return self._request("GET", f"{DRIVE_FILES_URL}/{encoded}?{params}", max_bytes=max_bytes)
