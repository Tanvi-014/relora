"""Unit tests for webhook simulator."""
import pytest
from app.simulator import build_simulated_payload, list_providers, get_template


def test_list_providers_returns_all():
    providers = list_providers()
    assert "stripe" in providers
    assert "github" in providers
    assert "shopify" in providers


def test_list_providers_stripe_events():
    providers = list_providers()
    stripe_events = providers["stripe"]
    assert "payment_intent.succeeded" in stripe_events
    assert "payment_intent.payment_failed" in stripe_events
    assert "customer.subscription.created" in stripe_events
    assert "invoice.paid" in stripe_events


def test_get_template_existing():
    template = get_template("stripe", "payment_intent.succeeded")
    assert template is not None
    assert "type" in template


def test_get_template_missing_provider():
    assert get_template("unknown_provider", "any.event") is None


def test_get_template_missing_event():
    assert get_template("stripe", "unknown.event") is None


def test_build_payload_fills_placeholders():
    payload = build_simulated_payload("stripe", "payment_intent.succeeded")
    assert payload is not None
    # {rand} placeholders should be replaced
    payload_str = str(payload)
    assert "{rand}" not in payload_str
    assert "{ts}" not in payload_str


def test_build_payload_github_push():
    payload = build_simulated_payload("github", "push")
    assert payload is not None
    assert "commits" in payload
    assert isinstance(payload["commits"], list)


def test_build_payload_shopify_order():
    payload = build_simulated_payload("shopify", "orders/create")
    assert payload is not None
    assert "line_items" in payload
    assert "total_price" in payload


def test_build_payload_with_overrides():
    overrides = {"custom_field": "custom_value"}
    payload = build_simulated_payload("stripe", "payment_intent.succeeded", overrides)
    assert payload["custom_field"] == "custom_value"


def test_build_payload_unknown_returns_none():
    assert build_simulated_payload("stripe", "unknown.event.xyz") is None
    assert build_simulated_payload("unknown_co", "any.event") is None
