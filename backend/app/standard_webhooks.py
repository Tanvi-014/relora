"""
Standard Webhooks signing spec (standardwebhooks.com).
Produces svix-id / svix-timestamp / svix-signature headers on every outbound delivery.
"""
import base64
import hashlib
import hmac
import time
from typing import Dict


def sign_outbound_webhook(
    webhook_id: str,
    payload: str,
    secret: str,
    timestamp: int | None = None,
) -> Dict[str, str]:
    """
    Sign an outbound webhook payload.

    secret must be a bare base64 string or whsec_<base64> prefixed string.
    Returns headers dict ready to merge into delivery_headers.
    """
    if timestamp is None:
        timestamp = int(time.time())

    if secret.startswith("whsec_"):
        raw_secret = base64.b64decode(secret[6:])
    else:
        try:
            raw_secret = base64.b64decode(secret)
        except Exception:
            raw_secret = secret.encode()

    to_sign = f"{webhook_id}.{timestamp}.{payload}".encode()
    sig = hmac.new(raw_secret, to_sign, hashlib.sha256).digest()
    b64_sig = base64.b64encode(sig).decode()

    return {
        "webhook-id": webhook_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": f"v1,{b64_sig}",
        # Svix-compatible aliases
        "svix-id": webhook_id,
        "svix-timestamp": str(timestamp),
        "svix-signature": f"v1,{b64_sig}",
    }
