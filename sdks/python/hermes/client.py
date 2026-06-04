"""Synchronous Hermes SDK client — zero external dependencies."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class HermesError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class HermesClient:
    """Synchronous client for the Hermes Webhook Delivery Middleware.

    Zero external dependencies — uses the Python standard library only.

    Example::

        from hermes import HermesClient
        client = HermesClient("http://localhost:8000", api_key="hk_...")
        result = client.send(destination_url="https://myapp.com/hook",
                             payload={"event": "order.created"})
        print(result["id"])
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    # ── Internal helpers ───────────────────────────────────────────────────

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Hermes-API-Key"] = self.api_key
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
        req = urllib.request.Request(url, data=data, headers=self._headers(extra_headers), method=method)
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
            raise HermesError(exc.code, detail) from exc
        except Exception as exc:
            raise HermesError(0, str(exc)) from exc

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
        """Ingest a single webhook event through Hermes.

        Returns a dict with at least ``{"id": "<uuid>", "status": "pending"}``.
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
        return self._request("POST", "/api/v1/ingest", payload=payload, params=params, extra_headers=headers)

    def fan_out(
        self,
        destination_urls: List[str],
        payload: Dict[str, Any],
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Send the same payload to multiple destinations (fan-out).

        Each destination gets an independent webhook record. Returns a list of
        ingest responses, one per destination.
        """
        return [self.send(url, payload, **kwargs) for url in destination_urls]

    # ── Webhooks ────────────────────────────────────────────────────────────

    def get_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Fetch status and delivery attempts for a webhook."""
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
        """Force immediate re-delivery of a failed webhook."""
        return self._request("POST", f"/api/v1/webhooks/{webhook_id}/replay")

    # ── DLQ ─────────────────────────────────────────────────────────────────

    def list_dlq(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Return webhooks in the Dead Letter Queue."""
        return self._request("GET", "/api/v1/dlq", params={"limit": str(limit), "offset": str(offset)})

    def replay_all_dlq(self) -> Dict[str, Any]:
        """Re-queue every webhook currently in the DLQ."""
        return self._request("POST", "/api/v1/dlq/replay-all")

    def dlq_health(self) -> Dict[str, Any]:
        """Return the DLQ health score and intelligence summary."""
        return self._request("GET", "/api/v1/dlq/health")

    # ── Stats & audit ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return delivery statistics for your tenant."""
        return self._request("GET", "/api/v1/stats")

    def get_audit_log(
        self,
        resource_type: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Return the audit log for your tenant."""
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if resource_type:
            params["resource_type"] = resource_type
        if action:
            params["action"] = action
        return self._request("GET", "/api/v1/audit-log", params=params)

    # ── Destinations ─────────────────────────────────────────────────────────

    def list_destinations(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/v1/destinations")

    def create_destination(self, name: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        return self._request("POST", "/api/v1/destinations", payload={"name": name, "url": url, **kwargs})

    def delete_destination(self, destination_id: str) -> None:
        self._request("DELETE", f"/api/v1/destinations/{destination_id}")
