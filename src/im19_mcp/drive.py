"""Scoped Google Drive v3 connector with bounded responses and fixed Google API origin.

File listing requires drive.metadata.readonly. Reading textual file content
requires separately consented restricted drive.readonly scope, enforced by the
MCP orchestration layer and by Google.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from im19_mcp.youtube import ProviderError
from im19_mcp.outbound import ResponseTooLarge, json_object, request_bytes, request_json

DRIVE_URL = "https://www.googleapis.com/drive/v3/files"
METADATA_FIELDS = "id,name,mimeType,size,modifiedTime,parents,webViewLink,description"
LIST_FIELDS = f"nextPageToken,incompleteSearch,files({METADATA_FIELDS})"
MAX_RESPONSE_BYTES = 128 * 1024
_ID = re.compile(r"^[A-Za-z0-9_-]{4,256}$")
_TEXT_MIMES = frozenset({
    "text/plain", "text/markdown", "text/csv", "application/json", "application/xml"
})
_GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


def validate_file_id(file_id: str) -> str:
    if not isinstance(file_id, str) or not _ID.fullmatch(file_id):
        raise ValueError("drive_file_id_invalid")
    return file_id


def _literal(value: str) -> str:
    """Escape quotes and backslashes in Drive q string literals."""
    if not isinstance(value, str) or not 1 <= len(value) <= 160:
        raise ValueError("drive_query_invalid")
    if any(ord(char) < 32 for char in value):
        raise ValueError("drive_query_control_character")
    return value.replace("\\", "\\\\").replace("'", "\\'")


def validate_folder_name(name: str) -> str:
    """Validate a root-folder name before consuming a one-time approval."""
    if (not isinstance(name, str) or not 1 <= len(name) <= 128 or
            name.strip() != name or name in {".", ".."} or
            any(ord(ch) < 32 or ord(ch) == 127 or ch in ("/", "\\\\")
                for ch in name)):
        raise ValueError("drive_folder_name_invalid")
    return name


class DriveClient:
    """Read operations and an approval-gated, narrowly scoped folder creation primitive."""

    def __init__(self, access_token: str, client: httpx.AsyncClient | None = None):
        if not access_token:
            raise ValueError("access_token_required")
        self._token = access_token
        self._client = client

    async def _fetch(self, url: str, params: dict[str, str], *, limit: int = MAX_RESPONSE_BYTES) -> bytes:
        if not (
            url == DRIVE_URL or
            url.startswith(DRIVE_URL + "/") and
            (url.endswith("/export") or url.count("/") == DRIVE_URL.count("/") + 1)
        ):
            raise ValueError("drive_url_not_allowlisted")
        try:
            return await request_bytes("GET", url, client=self._client, params=params,
                                       max_bytes=limit, timeout=12,
                                       headers={"Authorization": f"Bearer {self._token}", "Accept": "*/*"})
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = (
                "AUTH_REQUIRED" if status == 401 else
                "SCOPE_OR_FILE_ACCESS_DENIED" if status == 403 else
                "NOT_FOUND" if status == 404 else
                "RATE_LIMITED" if status == 429 else
                "PROVIDER_ERROR"
            )
            raise ProviderError(reason) from exc
        except ResponseTooLarge as exc:
            raise ProviderError("RESPONSE_SIZE_LIMIT_EXCEEDED") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("PROVIDER_UNAVAILABLE") from exc

    async def _json(self, url: str, params: dict[str, str]) -> dict:
        payload = await self._fetch(url, params)
        try:
            value = json_object(payload)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError("PROVIDER_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise ProviderError("PROVIDER_RESPONSE_INVALID")
        return value

    async def search_files(self, term: str, *, page_size: int = 25,
                           page_token: str = "") -> dict:
        """Search names in user's accessible Drive corpus, excluding trash."""
        if not 1 <= page_size <= 100:
            raise ValueError("drive_page_size_invalid")
        if not isinstance(page_token, str) or len(page_token) > 4096:
            raise ValueError("drive_page_token_invalid")
        params = {
            "q": f"name contains '{_literal(term)}' and trashed = false",
            "pageSize": str(page_size),
            "fields": LIST_FIELDS,
            "spaces": "drive",
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._json(DRIVE_URL, params)

    async def list_folder(self, folder_id: str, *, page_size: int = 25,
                          page_token: str = "") -> dict:
        """List direct children of the specified folder; no recursive traversal."""
        validate_file_id(folder_id)
        if not 1 <= page_size <= 100:
            raise ValueError("drive_page_size_invalid")
        if not isinstance(page_token, str) or len(page_token) > 4096:
            raise ValueError("drive_page_token_invalid")
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "pageSize": str(page_size),
            "fields": LIST_FIELDS,
            "spaces": "drive",
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._json(DRIVE_URL, params)

    async def metadata(self, file_id: str) -> dict:
        """Return only a small documented field projection for one file."""
        validate_file_id(file_id)
        return await self._json(
            DRIVE_URL + "/" + quote(file_id, safe=""),
            {"fields": METADATA_FIELDS, "supportsAllDrives": "true"},
        )

    async def create_folder(self, name: str) -> dict:
        """Create one folder in My Drive root; caller MUST have a consumed approval.

        The Drive API is not generally idempotent. Never retry this POST
        automatically after an ambiguous outcome. Verify using GET metadata.
        Requires separately consented `drive.file` scope.
        """
        validate_folder_name(name)
        try:
            result = await request_json(
                "POST", DRIVE_URL, client=self._client, timeout=12,
                max_bytes=MAX_RESPONSE_BYTES,
                params={"fields": "id,name,mimeType"},
                json={"name": name, "mimeType": "application/vnd.google-apps.folder"},
                headers={"Authorization": f"Bearer {self._token}",
                         "Accept": "application/json"},
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            reason = ("AUTH_REQUIRED" if status == 401 else
                      "SCOPE_OR_FILE_ACCESS_DENIED" if status == 403 else
                      "RATE_LIMITED" if status == 429 else "PROVIDER_ERROR")
            raise ProviderError(reason) from exc
        except ResponseTooLarge as exc:
            raise ProviderError("RESPONSE_SIZE_LIMIT_EXCEEDED") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("WRITE_OUTCOME_UNKNOWN") from exc
        folder_id = result.get("id")
        if (not isinstance(folder_id, str) or not _ID.fullmatch(folder_id) or
                result.get("name") != name or
                result.get("mimeType") != "application/vnd.google-apps.folder"):
            raise ProviderError("WRITE_OUTCOME_UNKNOWN")
        # An acknowledged POST is not sufficient proof of durable creation.
        # A later GET establishes observable postconditions.
        observed = await self.metadata(folder_id)
        if (observed.get("id") != folder_id or observed.get("name") != name or
                observed.get("mimeType") != "application/vnd.google-apps.folder"):
            raise ProviderError("WRITE_VERIFICATION_FAILED")
        return {"id": folder_id, "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "verified": True}

    async def read_text(self, file_id: str) -> dict:
        """Opt-in content access: text files or Google Docs exported as plain text.

        Requires drive.readonly, a Google restricted scope, and caller's explicit
        request. PDF/binary/Google Sheets are deliberately unsupported here.
        """
        metadata = await self.metadata(file_id)
        mime = metadata.get("mimeType")
        if mime not in _TEXT_MIMES and mime != _GOOGLE_DOC_MIME:
            raise ProviderError("UNSUPPORTED_MIME_TYPE")
        size = metadata.get("size")
        # Google Drive serializes int64 `size` values as JSON strings.
        if isinstance(size, str):
            if not size.isdecimal() or len(size) > 20:
                raise ProviderError("PROVIDER_RESPONSE_INVALID")
            size = int(size)
        if isinstance(size, int) and size > MAX_RESPONSE_BYTES:
            raise ProviderError("RESPONSE_SIZE_LIMIT_EXCEEDED")
        url = DRIVE_URL + "/" + quote(file_id, safe="")
        if mime == _GOOGLE_DOC_MIME:
            url += "/export"
            params = {"mimeType": "text/plain"}
        else:
            params = {"alt": "media", "supportsAllDrives": "true"}
        data = await self._fetch(url, params)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProviderError("UNSUPPORTED_TEXT_ENCODING") from exc
        return {"id": file_id, "name": metadata.get("name"),
                "mimeType": mime, "text": text, "content_untrusted": True}

