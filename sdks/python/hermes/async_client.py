"""Async Hermes SDK client — requires httpx (pip install hermes-middleware-sdk[async])."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError as exc:
    raise ImportError(
        "AsyncHermesClient requires httpx. Install it with:\n"
        "    pip install hermes-middleware-sdk[async]"
    ) from exc

from hermes.client import HermesError


class AsyncHermesClient:
    """Async client for the Hermes Webhook Delivery Middleware.

    Requires ``httpx`` (``pip install hermes-middleware-sdk[async]``).

    Example::

        from hermes.async_client import AsyncHermesClient

        async with AsyncHermesClient("http://localhost:8000", api_key="hk_...") as client:
            result = await client.send("https://myapp.com/hook", {"event": "order.created"})
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
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "AsyncHermesClient":
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
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["X-Hermes-API-Key"] = self.api_key
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
            raise RuntimeError("Use AsyncHermesClient as an async context manager")
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
            raise HermesError(exc.response.status_code, detail) from exc
        except Exception as exc:
            raise HermesError(0, str(exc)) from exc

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
    ) -> Dict[str, Any]:
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
        return await self._request("POST", "/api/v1/ingest", payload=payload, params=params, extra_headers=headers)

    async def fan_out(
        self,
        destination_urls: List[str],
        payload: Dict[str, Any],
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        import asyncio
        return list(await asyncio.gather(*[self.send(u, payload, **kwargs) for u in destination_urls]))

    # ── Webhooks ────────────────────────────────────────────────────────────

    async def get_webhook(self, webhook_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/v1/webhooks/{webhook_id}")

    async def list_webhooks(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        params: Dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        if status:
            params["status"] = status
        return await self._request("GET", "/api/v1/webhooks", params=params)

    async def replay_webhook(self, webhook_id: str) -> Dict[str, Any]:
        return await self._request("POST", f"/api/v1/webhooks/{webhook_id}/replay")

    # ── DLQ ─────────────────────────────────────────────────────────────────

    async def list_dlq(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/dlq", params={"limit": str(limit), "offset": str(offset)})

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

    async def create_destination(self, name: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/destinations", payload={"name": name, "url": url, **kwargs})

    async def delete_destination(self, destination_id: str) -> None:
        await self._request("DELETE", f"/api/v1/destinations/{destination_id}")
