"""Credentialed browser requests are limited to configured frontend origins."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api import cors


def test_configured_origin_can_send_connection_cookies(monkeypatch):
    monkeypatch.setattr(cors, "load_settings", lambda: None)
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://127.0.0.1:5173")
    app = FastAPI()
    cors.configure_cors(app)
    with TestClient(app) as browser:
        response = browser.options("/api/integrations/trello/connection", headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-credentials"] == "true"
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        denied = browser.options("/api/integrations/trello/connection", headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "DELETE",
        })
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers


def test_wildcard_origins_are_rejected(monkeypatch):
    monkeypatch.setattr(cors, "load_settings", lambda: None)
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError, match="explicit origins"):
        cors.configure_cors(FastAPI())
