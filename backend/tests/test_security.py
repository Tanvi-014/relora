from unittest.mock import Mock
import asyncio

import pytest
from fastapi import HTTPException

from app.config import settings
from app.security import require_api_key, validate_destination_url


def test_validate_destination_url_accepts_public_url(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", False)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    assert validate_destination_url("https://example.com/webhook") == "https://example.com/webhook"


def test_validate_destination_url_rejects_invalid_scheme():
    with pytest.raises(HTTPException) as exc:
        validate_destination_url("ftp://example.com/webhook")

    assert exc.value.status_code == 400


def test_validate_destination_url_rejects_private_ip_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", False)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    with pytest.raises(HTTPException) as exc:
        validate_destination_url("http://127.0.0.1:3000/webhook")

    assert exc.value.status_code == 400


def test_validate_destination_url_allows_private_host_for_local_demo(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", True)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    assert validate_destination_url("http://localhost:3000/webhook") == "http://localhost:3000/webhook"


def test_validate_destination_url_enforces_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", True)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "hooks.example.com")

    with pytest.raises(HTTPException) as exc:
        validate_destination_url("https://other.example.com/webhook")

    assert exc.value.status_code == 400


def test_destination_host_allowlist_parses_csv(monkeypatch):
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", " hooks.example.com, api.example.com ")

    assert settings.destination_host_allowlist == ["hooks.example.com", "api.example.com"]


def test_require_api_key_noops_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "")
    request = Mock()
    request.headers = {}

    assert asyncio.run(require_api_key(request)) is None


def test_require_api_key_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "secret")
    request = Mock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_api_key(request))

    assert exc.value.status_code == 401


def test_require_api_key_accepts_matching_key(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "secret")
    request = Mock()
    request.headers = {"X-Hermes-API-Key": "secret"}

    assert asyncio.run(require_api_key(request)) is None
