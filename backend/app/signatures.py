import hmac
import time
from hashlib import sha1, sha256
from typing import Optional

from fastapi import HTTPException, Request, status

from app.config import settings


def _compare_digest(expected: str, supplied: Optional[str]) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


def _get_secret(provider: str) -> str:
    if provider == "stripe":
        return settings.STRIPE_WEBHOOK_SECRET
    if provider == "github":
        return settings.GITHUB_WEBHOOK_SECRET
    if provider in {"hermes", "generic"}:
        return settings.HERMES_WEBHOOK_SECRET
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported signature provider. Use stripe, github, or hermes.",
    )


def verify_webhook_signature(provider: Optional[str], request: Request, raw_body: bytes) -> None:
    if not provider:
        return

    provider = provider.lower()
    secret = _get_secret(provider)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{provider} signature verification is configured but no secret is set",
        )

    if provider == "stripe":
        _verify_stripe(request, raw_body, secret)
        return

    if provider == "github":
        _verify_github(request, raw_body, secret)
        return

    _verify_hermes(request, raw_body, secret)


def _verify_stripe(request: Request, raw_body: bytes, secret: str) -> None:
    signature_header = request.headers.get("Stripe-Signature", "")
    parts = {}
    for item in signature_header.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parts.setdefault(key, []).append(value)

    timestamp_values = parts.get("t", [])
    signatures = parts.get("v1", [])
    if not timestamp_values or not signatures:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Stripe signature header")

    timestamp = int(timestamp_values[0])
    if abs(time.time() - timestamp) > settings.SIGNATURE_TOLERANCE_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stripe signature timestamp is outside tolerance")

    signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()
    if not any(_compare_digest(expected, supplied) for supplied in signatures):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Stripe signature verification failed")


def _verify_github(request: Request, raw_body: bytes, secret: str) -> None:
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, sha256).hexdigest()
    if not _compare_digest(expected, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="GitHub signature verification failed")


def _verify_hermes(request: Request, raw_body: bytes, secret: str) -> None:
    algorithm = request.headers.get("X-Hermes-Signature-Algorithm", "sha256").lower()
    digest = sha1 if algorithm == "sha1" else sha256
    expected = hmac.new(secret.encode("utf-8"), raw_body, digest).hexdigest()
    if not _compare_digest(expected, request.headers.get("X-Hermes-Signature")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Hermes signature verification failed")
