"""Google Drive v3 connector: mock API, strict input, response bounds, read scopes."""
import httpx
import pytest

from im19_mcp.drive import DriveClient, MAX_RESPONSE_BYTES, _literal, validate_file_id
from im19_mcp.youtube import ProviderError


def test_drive_query_escaping():
    assert _literal("Bob's \\ Docs") == "Bob\\'s \\\\ Docs"
    for unsafe in ("", "a\nb", "x" * 161):
        with pytest.raises(ValueError):
            _literal(unsafe)


@pytest.mark.parametrize("file_id", ["", "../etc/passwd", "https://bad.example", "a b", "A" * 257])
def test_invalid_resource_ids_blocked(file_id):
    with pytest.raises(ValueError):
        validate_file_id(file_id)


@pytest.mark.asyncio
async def test_search_folder_and_metadata_use_authorized_static_google_origin():
    requests = []

    def respond(request: httpx.Request):
        requests.append(request)
        assert request.url.host == "www.googleapis.com"
        assert request.url.path.startswith("/drive/v3/files")
        assert request.headers["Authorization"] == "Bearer TEST-MOCK-TOKEN"
        if request.url.path.endswith("file_ABCDE"):
            return httpx.Response(200, json={
                "id": "file_ABCDE", "name": "alpha.txt", "mimeType": "text/plain", "size": "10"
            })
        return httpx.Response(200, json={"files": [], "nextPageToken": "page-two"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api = DriveClient("TEST-MOCK-TOKEN", client)
        result = await api.search_files("Bob's", page_size=4, page_token="cursor")
        assert result["nextPageToken"] == "page-two"
        assert requests[-1].url.params["pageToken"] == "cursor"
        assert requests[-1].url.params["q"] == "name contains 'Bob\\'s' and trashed = false"
        await api.list_folder("folder_ABCDE")
        assert requests[-1].url.params["q"] == "'folder_ABCDE' in parents and trashed = false"
        await api.metadata("file_ABCDE")
        assert requests[-1].url.params["fields"].startswith("id,name")


@pytest.mark.asyncio
async def test_text_read_is_bounded_and_untrusted():
    def respond(request):
        if request.url.params.get("fields"):
            return httpx.Response(200, json={
                "id": "file_ABCDE", "name": "document.txt", "mimeType": "text/plain",
                "size": "7",
            })
        assert request.url.params["alt"] == "media"
        return httpx.Response(200, content=b"example")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await DriveClient("token", client).read_text("file_ABCDE")
        assert result["text"] == "example"
        assert result["content_untrusted"] is True


@pytest.mark.asyncio
async def test_google_document_export_text_only():
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.params.get("fields"):
            return httpx.Response(200, json={
                "id": "doc_ABCDE", "name": "Doc", "mimeType": "application/vnd.google-apps.document",
            })
        return httpx.Response(200, content=b"exported")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        output = await DriveClient("token", client).read_text("doc_ABCDE")
        assert output["text"] == "exported"
        assert requests[-1].url.path.endswith("/export")
        assert requests[-1].url.params["mimeType"] == "text/plain"


@pytest.mark.asyncio
async def test_binaries_pdf_refused_before_download():
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"mimeType": "application/pdf", "size": "99"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ProviderError, match="UNSUPPORTED_MIME_TYPE"):
            await DriveClient("token", client).read_text("file_ABCDE")
        assert len(requests) == 1


@pytest.mark.asyncio
async def test_large_provider_response_rejected():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"X" * (MAX_RESPONSE_BYTES + 1))
    )) as client:
        with pytest.raises(ProviderError, match="RESPONSE_SIZE_LIMIT_EXCEEDED"):
            await DriveClient("token", client).metadata("file_ABCDE")


@pytest.mark.asyncio
async def test_rate_limit_and_missing_file_normalized():
    for status, error in [(429, "RATE_LIMITED"), (404, "NOT_FOUND"),
                          (403, "SCOPE_OR_FILE_ACCESS_DENIED")]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(status)
        )) as client:
            with pytest.raises(ProviderError, match=error):
                await DriveClient("token", client).metadata("file_ABCDE")


@pytest.mark.asyncio
async def test_no_arbitrary_url_or_excess_page_size():
    api = DriveClient("token")
    with pytest.raises(ValueError):
        await api._fetch("http://127.0.0.1:8000/secrets", {})
    with pytest.raises(ValueError):
        await api.search_files("term", page_size=101)
    with pytest.raises(ValueError):
        await api.list_folder("folder_ABCDE", page_size=0)
