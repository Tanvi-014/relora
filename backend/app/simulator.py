"""
Webhook simulator — fire realistic fake events from known providers.
"""
import random
import string
import time
from typing import Any, Dict, Optional


def _rand(prefix: str = "") -> str:
    chars = string.ascii_lowercase + string.digits
    return prefix + "".join(random.choices(chars, k=24))


PROVIDER_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "stripe": {
        "payment_intent.succeeded": {
            "id": "evt_{rand}",
            "object": "event",
            "type": "payment_intent.succeeded",
            "created": "{ts}",
            "livemode": False,
            "data": {
                "object": {
                    "id": "pi_{rand}",
                    "object": "payment_intent",
                    "amount": 2999,
                    "currency": "usd",
                    "status": "succeeded",
                    "customer": "cus_{rand}",
                    "description": "Subscription renewal",
                }
            },
            "api_version": "2023-10-16",
        },
        "payment_intent.payment_failed": {
            "id": "evt_{rand}",
            "object": "event",
            "type": "payment_intent.payment_failed",
            "created": "{ts}",
            "livemode": False,
            "data": {
                "object": {
                    "id": "pi_{rand}",
                    "object": "payment_intent",
                    "amount": 9900,
                    "currency": "usd",
                    "status": "requires_payment_method",
                    "last_payment_error": {
                        "code": "card_declined",
                        "message": "Your card was declined.",
                    },
                }
            },
        },
        "customer.subscription.created": {
            "id": "evt_{rand}",
            "object": "event",
            "type": "customer.subscription.created",
            "created": "{ts}",
            "livemode": False,
            "data": {
                "object": {
                    "id": "sub_{rand}",
                    "object": "subscription",
                    "customer": "cus_{rand}",
                    "status": "active",
                    "current_period_start": "{ts}",
                    "current_period_end": "{ts_future}",
                    "plan": {"id": "plan_basic", "amount": 999, "currency": "usd", "interval": "month"},
                }
            },
        },
        "invoice.paid": {
            "id": "evt_{rand}",
            "object": "event",
            "type": "invoice.paid",
            "created": "{ts}",
            "livemode": False,
            "data": {
                "object": {
                    "id": "in_{rand}",
                    "object": "invoice",
                    "amount_paid": 4999,
                    "currency": "usd",
                    "customer": "cus_{rand}",
                    "status": "paid",
                }
            },
        },
    },
    "github": {
        "push": {
            "ref": "refs/heads/main",
            "before": "abc123",
            "after": "def456",
            "repository": {
                "id": 123456,
                "name": "my-repo",
                "full_name": "octocat/my-repo",
                "private": False,
            },
            "pusher": {"name": "octocat", "email": "octocat@github.com"},
            "commits": [
                {
                    "id": "def456",
                    "message": "Fix critical bug",
                    "timestamp": "{ts_iso}",
                    "author": {"name": "Octocat", "email": "octocat@github.com"},
                    "added": ["src/fix.py"],
                    "modified": ["README.md"],
                    "removed": [],
                }
            ],
        },
        "pull_request.opened": {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "id": 987654,
                "title": "Add new feature",
                "state": "open",
                "user": {"login": "contributor"},
                "head": {"ref": "feature-branch"},
                "base": {"ref": "main"},
                "body": "This PR adds an awesome new feature.",
                "created_at": "{ts_iso}",
            },
            "repository": {"name": "my-repo", "full_name": "octocat/my-repo"},
        },
        "issues.opened": {
            "action": "opened",
            "issue": {
                "id": 111222,
                "number": 7,
                "title": "Bug: something is broken",
                "state": "open",
                "user": {"login": "reporter"},
                "body": "Steps to reproduce...",
                "created_at": "{ts_iso}",
            },
            "repository": {"name": "my-repo", "full_name": "octocat/my-repo"},
        },
    },
    "shopify": {
        "orders/create": {
            "id": 4567890123,
            "email": "customer@example.com",
            "created_at": "{ts_iso}",
            "total_price": "99.00",
            "currency": "USD",
            "financial_status": "pending",
            "fulfillment_status": None,
            "line_items": [
                {
                    "id": 111,
                    "title": "T-Shirt",
                    "quantity": 2,
                    "price": "29.99",
                    "sku": "TSHIRT-M-BLK",
                }
            ],
            "shipping_address": {
                "name": "John Doe",
                "address1": "123 Main St",
                "city": "Anytown",
                "country": "US",
                "zip": "12345",
            },
        },
        "orders/paid": {
            "id": 4567890124,
            "email": "customer@example.com",
            "created_at": "{ts_iso}",
            "total_price": "59.00",
            "currency": "USD",
            "financial_status": "paid",
        },
        "customers/create": {
            "id": 789456123,
            "email": "newuser@example.com",
            "first_name": "Jane",
            "last_name": "Smith",
            "created_at": "{ts_iso}",
            "orders_count": 0,
            "total_spent": "0.00",
        },
    },
}


def _fill_template(obj: Any) -> Any:
    ts_now = int(time.time())
    ts_future = ts_now + 30 * 24 * 3600
    import datetime
    ts_iso = datetime.datetime.utcnow().isoformat() + "Z"

    if isinstance(obj, str):
        return (
            obj
            .replace("{rand}", _rand())
            .replace("{ts}", str(ts_now))
            .replace("{ts_future}", str(ts_future))
            .replace("{ts_iso}", ts_iso)
        )
    if isinstance(obj, dict):
        return {k: _fill_template(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fill_template(i) for i in obj]
    return obj


def get_template(provider: str, event_type: str) -> Optional[Dict[str, Any]]:
    provider_templates = PROVIDER_TEMPLATES.get(provider.lower())
    if not provider_templates:
        return None
    return provider_templates.get(event_type)


def build_simulated_payload(
    provider: str,
    event_type: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    template = get_template(provider, event_type)
    if template is None:
        return None
    payload = _fill_template(template)
    if overrides:
        payload.update(overrides)
    return payload


def list_providers() -> Dict[str, list]:
    return {provider: list(events.keys()) for provider, events in PROVIDER_TEMPLATES.items()}
