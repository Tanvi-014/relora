"""Unit tests for Standard Webhooks signing."""
import base64
import hashlib
import hmac
import pytest

from app.standard_webhooks import sign_outbound_webhook


SECRET_RAW = b"super_secret_key_32bytes_abcdefgh"
SECRET_B64 = base64.b64encode(SECRET_RAW).decode()
SECRET_WHSEC = "whsec_" + SECRET_B64


def test_returns_all_required_headers():
    headers = sign_outbound_webhook("wh_test_id", '{"event":"test"}', SECRET_WHSEC)
    for key in ("webhook-id", "webhook-timestamp", "webhook-signature", "svix-id", "svix-timestamp", "svix-signature"):
        assert key in headers


def test_signature_format():
    headers = sign_outbound_webhook("wh_test_id", '{"event":"test"}', SECRET_WHSEC, timestamp=1700000000)
    sig = headers["webhook-signature"]
    assert sig.startswith("v1,")
    b64_part = sig[3:]
    # must be valid base64
    base64.b64decode(b64_part)


def test_signature_verifiable():
    wh_id = "wh_abc123"
    payload = '{"event":"payment.succeeded","amount":999}'
    ts = 1700000000
    headers = sign_outbound_webhook(wh_id, payload, SECRET_WHSEC, timestamp=ts)

    # Manually verify
    to_sign = f"{wh_id}.{ts}.{payload}".encode()
    expected_sig = hmac.new(SECRET_RAW, to_sign, hashlib.sha256).digest()
    expected_b64 = base64.b64encode(expected_sig).decode()
    assert headers["webhook-signature"] == f"v1,{expected_b64}"


def test_svix_and_webhook_headers_match():
    headers = sign_outbound_webhook("wh_x", '{}', SECRET_WHSEC, timestamp=1700000000)
    assert headers["svix-id"] == headers["webhook-id"]
    assert headers["svix-timestamp"] == headers["webhook-timestamp"]
    assert headers["svix-signature"] == headers["webhook-signature"]


def test_bare_base64_secret():
    headers = sign_outbound_webhook("wh_y", '{"x":1}', SECRET_B64)
    assert "webhook-signature" in headers
    assert headers["webhook-signature"].startswith("v1,")


def test_different_payloads_produce_different_signatures():
    ts = 1700000000
    h1 = sign_outbound_webhook("id", '{"a":1}', SECRET_WHSEC, timestamp=ts)
    h2 = sign_outbound_webhook("id", '{"a":2}', SECRET_WHSEC, timestamp=ts)
    assert h1["webhook-signature"] != h2["webhook-signature"]
