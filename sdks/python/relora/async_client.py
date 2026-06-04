"""Async Relora SDK client — requires httpx (pip install relora-sdk[async])."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError as exc:
    raise ImportError(
        "AsyncReloraClient requires httpx. Install it with:\n"
        "    pip install relora-sdk[async]"
    ) from exc

from relora.client import ReloraError


class AsyncReloraClient:
    """Async client for the Relora.

    Requires ``httpx`` (``pip install relora-sdk[async]``).

    Example::

        from relora import AsyncReloraClient

        async with AsyncReloraClient("http://localhost:8000", api_key="hk_...") as client:
            result = await client.send(
                "https://myapp.com/hook",
                {"event": "order.created"},
                idempotency_key="order-123",
            )
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
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AsyncReloraClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._base_headers(),
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    def _base_headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Relora-API-Key"] = self.api_key
        if self.project_id:
            h["X-Project-Id"] = self.project_id
        return h

    async def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        params: Optional[Dict[str, str]] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        if self._client is None:
            raise RuntimeError("Use AsyncReloraClient as an async context manager")
        try:
            resp = await self._client.request(
                method,
                path,
                json=payload,
                params=params,
                headers=extra_headers or {},
            )
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except Exception:
                detail = exc.response.text
            raise ReloraError(exc.response.status_code, detail) from exc
        except Exception as exc:
            raise ReloraError(0, str(exc)) from exc

    # ── Ingest ──────────────────────────────────────────────────────────────

    async def send(
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
        """Ingest a single webhook event through Relora."""
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
        return await self._request(
            "POST", "/api/v1/ingest", payload=payload, params=params, extra_headers=headers
        )

    async def fan_out(
        self,
        destination_urls: List[str],
        payload: Dict[str, Any],
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Send the same payload to all destinations concurrently.

        All destinations are attempted regardless of individual failures.
        Each result dict either contains the normal ingest response (success)
        or ``{"url": ..., "id": None, "error": "..."}`` (failure).
        """
        results = await asyncio.gather(
            *[self.send(url, payload, **kwargs) for url in destination_urls],
            return_exceptions=True,
        )
        out: List[Dict[str, Any]] = []
        for url, result in zip(destination_urls, results):
            if isinstance(result, Exception):
                out.append({"url": url, "id": None, "error": str(result)})
            else:
                out.append(result)
        return out

    # ── Webhooks ────────────────────────────────────────────────────────────

    async def get_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Fetch a webhook record with its full delivery attempt history."""
        return await self._request("GET", f"/api/v1/webhooks/{webhook_id}")

    async def list_webhooks(
        self, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status:
            params["status"] = status
        return await self._request("GET", "/api/v1/webhooks", params=params)

    async def replay_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """Force immediate re-delivery of a webhook."""
        return await self._request("POST", f"/api/v1/webhooks/{webhook_id}/replay")

    # ── DLQ ─────────────────────────────────────────────────────────────────

    async def list_dlq(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        return await self._request(
            "GET", "/api/v1/dlq", params={"limit": str(limit), "offset": str(offset)}
        )

    async def replay_all_dlq(self) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/dlq/replay-all")

    async def dlq_health(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/dlq/health")

    # ── Stats & audit ────────────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/stats")

    async def get_audit_log(
        self,
        resource_type: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if resource_type:
            params["resource_type"] = resource_type
        if action:
            params["action"] = action
        return await self._request("GET", "/api/v1/audit-log", params=params)

    # ── Destinations ─────────────────────────────────────────────────────────

    async def list_destinations(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/v1/destinations")

    async def get_destination(self, destination_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/v1/destinations/{destination_id}")

    async def create_destination(self, name: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/destinations", payload={"name": name, "url": url, **kwargs}
        )

    async def update_destination(self, destination_id: str, **kwargs: Any) -> Dict[str, Any]:
        """Update a destination (PUT — supply all fields you want persisted)."""
        return await self._request(
            "PUT", f"/api/v1/destinations/{destination_id}", payload=kwargs
        )

    async def delete_destination(self, destination_id: str) -> None:
        await self._request("DELETE", f"/api/v1/destinations/{destination_id}")

    # ── Event types ──────────────────────────────────────────────────────────

    async def list_event_types(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/v1/event-types")

    async def create_event_type(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        return await self._request(
            "POST", "/api/v1/event-types", payload={"name": name, **kwargs}
        )

    async def delete_event_type(self, event_type_id: str) -> None:
        await self._request("DELETE", f"/api/v1/event-types/{event_type_id}")

    # ── Alerts ───────────────────────────────────────────────────────────────

    async def list_alerts(self) -> List[Dict[str, Any]]:
        return await self._request("GET", "/api/v1/alerts")

    async def create_alert(
        self,
        name: str,
        channel_type: str,
        config: Dict[str, Any],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/alerts",
            payload={"name": name, "channel_type": channel_type, "config": config, **kwargs},
        )

    async def delete_alert(self, alert_id: str) -> None:
        await self._request("DELETE", f"/api/v1/alerts/{alert_id}")
