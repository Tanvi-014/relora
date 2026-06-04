"""
WebSocket connection manager for real-time dashboard updates.
The worker broadcasts delivery outcomes here; the dashboard listens instead of polling.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Set

from fastapi import WebSocket

logger = logging.getLogger("relora.ws")


class ConnectionManager:
    def __init__(self):
        # project_api_key → set of active WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, project_key: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(project_key, set()).add(websocket)
        logger.debug("WS connected project_key=%s total=%d", project_key, len(self._connections.get(project_key, set())))

    async def disconnect(self, websocket: WebSocket, project_key: str) -> None:
        async with self._lock:
            conns = self._connections.get(project_key, set())
            conns.discard(websocket)
            if not conns:
                self._connections.pop(project_key, None)

    async def broadcast(self, project_key: str, event_type: str, data: dict) -> None:
        conns = self._connections.get(project_key)
        if not conns:
            return
        message = json.dumps({
            "type": event_type,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        dead: Set[WebSocket] = set()
        for ws in list(conns):
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)
        if dead:
            async with self._lock:
                self._connections.get(project_key, set()).difference_update(dead)

    def connection_count(self, project_key: str) -> int:
        return len(self._connections.get(project_key, set()))


# Singleton used by API and worker
ws_manager = ConnectionManager()
