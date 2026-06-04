"""Synchronous Relora SDK client — zero external dependencies."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class ReloraError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class ReloraClient:
    """Synchronous client for the Relora.

    Zero external dependencies — uses the Python standard library only.

    Example::

        from relora import ReloraClient
        client = ReloraClient("http://localhost:8000", api_key="hk_...")
        result = client.send(
            destination_url="https://myapp.com/hook",
            payload={"event": "order.created"},
            idempotency_key="order-123",
        )
        print(result["id"])
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
        project_id: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.project_id = project_id

    # ── Internal helpers ───────────────────────────────────────────────────

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Relora-API-Key"] = self.api_key
        if self.project_id:
            h["X-Project-Id"] = self.project_id
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(extra_headers), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            raise ReloraError(exc.code, detail) from exc
        except Exception as exc:
            raise ReloraError(0, str(exc)) from exc

    # ── Ingest ──────────────────────────────────────────────────────────────

    def send(
        self,
        destination_url: str,
        payload: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        filter_expression: Optional[str] = None,
        transform: Optional[Dict[str, Any]] = None,
        ordering_key: Optional[str] = None,
        signature_provider: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Ingest a single webhook event through Relora.

        Returns the ingest response, which includes at least
        ``{"webhook_id": "<uuid>", "status": "pending"}``.
        """
        params: Dict[str, str] = {"url": destination_url}
        if filter_expression:
            params["filter"] = filter_expression
        if transform:
            params["transform"] = json.dumps(transform)
        if ordering_key:
            params["ordering_key"] = ordering_key
        if signature_provider:
            params["signature_provider"] = signature_provider
        headers: Dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        return self._request(
            "POST", "/api/v1/ingest", payload=payload, params=params, extra_headers=headers
        )

    def fan_out(
        self,
        destination_urls: List[str],
        payload: Dict[str, Any],
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Send the same payload to multiple destinations.

        Attempts every destination regardless of individual failures and returns
        one result dict per URL. On failure the result contains an ``"error"``
        key and ``"id": None``; on success it is the normal ingest response.

        Note: requests are made sequentially. Use ``AsyncReloraClient.fan_out``
        for concurrent delivery.
        """
        results: List[Dict[str, Any]] = []
        for url in destination_urls:
            try:
                results.append(self.send(url, payload, **kwargs))
            except ReloraError as exc:
                results.append({"url": url, "id": None, "error": str(exc)})
        return results

    # ── Webhooks ────────────────────────────────────────────────────────────

    def get_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Fetch a webhook record with its full delivery attempt history."""
        return self._request("GET", f"/api/v1/webhooks/{webhook_id}")

    def list_webhooks(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List webhooks with optional status filter."""
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status:
            params["status"] = status
        return self._request("GET", "/api/v1/webhooks", params=params)

    def replay_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Force immediate re-delivery of a webhook."""
        return self._request("POST", f"/api/v1/webhooks/{webhook_id}/replay")

    # ── DLQ ─────────────────────────────────────────────────────────────────

    def list_dlq(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Return webhooks in the Dead Letter Queue."""
        return self._request(
            "GET", "/api/v1/dlq", params={"limit": str(limit), "offset": str(offset)}
        )

    def replay_all_dlq(self) -> Dict[str, Any]:
        """Re-queue every webhook currently in the DLQ."""
        return self._request("POST", "/api/v1/dlq/replay-all")

    def dlq_health(self) -> Dict[str, Any]:
        """Return the DLQ health score and intelligence summary."""
        return self._request("GET", "/api/v1/dlq/health")

    # ── Stats & audit ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return delivery statistics for your project."""
        return self._request("GET", "/api/v1/stats")

    def get_audit_log(
        self,
        resource_type: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Return the tamper-evident audit log for your project."""
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if resource_type:
            params["resource_type"] = resource_type
        if action:
            params["action"] = action
        return self._request("GET", "/api/v1/audit-log", params=params)

    # ── Destinations ─────────────────────────────────────────────────────────

    def list_destinations(self) -> List[Dict[str, Any]]:
        """List all registered delivery destinations."""
        return self._request("GET", "/api/v1/destinations")

    def get_destination(self, destination_id: str) -> Dict[str, Any]:
        """Fetch a single destination by ID."""
        return self._request("GET", f"/api/v1/destinations/{destination_id}")

    def create_destination(self, name: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        """Create a new delivery destination.

        Common optional kwargs: ``description``, ``max_retries``,
        ``filter_expression``, ``transform_type``, ``custom_headers``,
        ``webhook_secret``, ``slo_target_pct``.
        """
        return self._request(
            "POST", "/api/v1/destinations", payload={"name": name, "url": url, **kwargs}
        )

    def update_destination(self, destination_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Update a destination (PUT — supply all fields you want persisted)."""
        return self._request(
            "PUT", f"/api/v1/destinations/{destination_id}", payload=kwargs
        )

    def delete_destination(self, destination_id: str) -> None:
        """Delete a destination. In-flight webhooks are unaffected."""
        self._request("DELETE", f"/api/v1/destinations/{destination_id}")

    # ── Event types ──────────────────────────────────────────────────────────

    def list_event_types(self) -> List[Dict[str, Any]]:
        """List all event types registered in the catalog."""
        return self._request("GET", "/api/v1/event-types")

    def create_event_type(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        """Create an event type.

        Common optional kwargs: ``description``, ``schema`` (JSON Schema dict),
        ``example_payload``, ``version``.
        """
        return self._request(
            "POST", "/api/v1/event-types", payload={"name": name, **kwargs}
        )

    def delete_event_type(self, event_type_id: str) -> None:
        """Delete an event type from the catalog."""
        self._request("DELETE", f"/api/v1/event-types/{event_type_id}")

    # ── Alerts ───────────────────────────────────────────────────────────────

    def list_alerts(self) -> List[Dict[str, Any]]:
        """List all configured alert channels."""
        return self._request("GET", "/api/v1/alerts")

    def create_alert(
        self,
        name: str,
        channel_type: str,
        config: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Create an alert channel (``channel_type``: ``"slack"`` or ``"email"``)."""
        return self._request(
            "POST",
            "/api/v1/alerts",
            payload={"name": name, "channel_type": channel_type, "config": config, **kwargs},
        )

    def delete_alert(self, alert_id: str) -> None:
        """Delete an alert channel."""
        self._request("DELETE", f"/api/v1/alerts/{alert_id}")
