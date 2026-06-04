"""
Server-Sent Events (SSE) hub for streaming real-time delivery logs.
Provides an alternative to WebSockets for clients that prefer SSE.
"""
import asyncio
import json
import logging
from typing import Dict, Set
from uuid import UUID

from fastapi import Request

logger = logging.getLogger("hermes.sse_hub")


class SSEHub:
    """Manages SSE connections and broadcasts events to connected clients."""
    
    def __init__(self):
        # Map of project_id to set of queues
        self._connections: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self, project_id: str) -> asyncio.Queue:
        """Register a new SSE connection for a project."""
        async with self._lock:
            if project_id not in self._connections:
                self._connections[project_id] = set()
            queue = asyncio.Queue()
            self._connections[project_id].add(queue)
            logger.info(f"SSE client connected for project {project_id}, total connections: {len(self._connections[project_id])}")
            return queue
    
    async def disconnect(self, project_id: str, queue: asyncio.Queue):
        """Remove an SSE connection for a project."""
        async with self._lock:
            if project_id in self._connections:
                self._connections[project_id].discard(queue)
                if not self._connections[project_id]:
                    del self._connections[project_id]
                logger.info(f"SSE client disconnected for project {project_id}, remaining connections: {len(self._connections.get(project_id, set()))}")
    
    async def broadcast(self, project_id: str, event_type: str, data: dict):
        """Broadcast an event to all SSE clients for a project."""
        async with self._lock:
            if project_id not in self._connections:
                return
            
            message = {
                "event": event_type,
                "data": data,
                "timestamp": asyncio.get_running_loop().time(),
            }
            
            # Remove disconnected queues
            dead_queues = set()
            for queue in self._connections[project_id]:
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    logger.warning(f"SSE queue full for project {project_id}, dropping message")
                    dead_queues.add(queue)
                except Exception as e:
                    logger.error(f"Error broadcasting to SSE queue: {e}")
                    dead_queues.add(queue)
            
            # Clean up dead queues
            for queue in dead_queues:
                self._connections[project_id].discard(queue)
            
            if dead_queues:
                logger.info(f"Removed {len(dead_queues)} dead SSE connections for project {project_id}")
    
    async def broadcast_webhook_update(self, project_id: str, webhook_data: dict):
        """Broadcast a webhook update event."""
        await self.broadcast(project_id, "webhook.updated", webhook_data)
    
    async def broadcast_delivery_attempt(self, project_id: str, attempt_data: dict):
        """Broadcast a delivery attempt event."""
        await self.broadcast(project_id, "delivery.attempt", attempt_data)
    
    async def broadcast_incident_update(self, project_id: str, incident_data: dict):
        """Broadcast an incident update event."""
        await self.broadcast(project_id, "incident.updated", incident_data)


# Global SSE hub instance
sse_hub = SSEHub()
