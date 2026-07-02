import pytest
from fastapi import HTTPException

from app.routers import auth
from app.routers.auth import reject_cross_origin


class _Req:
    """Minimal stand-in for a Starlette Request: only .headers.get is used."""
    def __init__(self, headers):
        self.headers = headers


class TestRejectCrossOrigin:
    def test_allows_request_without_origin(self):
        # Non-browser clients (cookie exporter, curl) send no Origin header.
        reject_cross_origin(_Req({"host": "app:8587"}))

    def test_allows_same_origin(self):
        reject_cross_origin(_Req({"host": "app:8587", "origin": "http://app:8587"}))

    def test_rejects_cross_origin(self):
        with pytest.raises(HTTPException) as exc:
            reject_cross_origin(_Req({"host": "app:8587", "origin": "http://evil.example"}))
        assert exc.value.status_code == 403

    def test_rejects_null_opaque_origin(self):
        # Sandboxed iframes send Origin: null; must not be treated as same-origin.
        with pytest.raises(HTTPException) as exc:
            reject_cross_origin(_Req({"host": "app:8587", "origin": "null"}))
        assert exc.value.status_code == 403

    def test_allows_forwarded_host_behind_proxy(self):
        # Proxy rewrites Host to the internal upstream but sets X-Forwarded-Host to
        # the public host the browser actually used (and put in Origin).
        reject_cross_origin(_Req({
            "host": "channelhoarder:8587",
            "x-forwarded-host": "hoarder.example",
            "origin": "https://hoarder.example",
        }))

    def test_allows_configured_cors_origin(self, monkeypatch):
        monkeypatch.setattr(auth.settings, "CORS_ORIGINS", "https://dash.example")
        reject_cross_origin(_Req({"host": "app:8587", "origin": "https://dash.example"}))
