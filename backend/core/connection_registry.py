"""
ConnectionRegistry — tracks active WebSocket connections per user_id.
Used by reminder system to push notifications to connected clients.
"""
import logging
from typing import Dict
from fastapi import WebSocket

log = logging.getLogger("zephyro.ws")


class ConnectionRegistry:
    def __init__(self):
        self._connections: Dict[str, WebSocket] = {}

    def count(self) -> int:
        return len(self._connections)

    async def register(self, user_id: str, ws: WebSocket) -> None:
        self._connections[user_id] = ws
        log.debug(f"[registry] Registered WS for user {user_id}")

    async def unregister(self, user_id: str) -> None:
        self._connections.pop(user_id, None)
        log.debug(f"[registry] Unregistered WS for user {user_id}")

    async def push(self, user_id: str, payload: dict) -> bool:
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception as exc:
            log.warning(f"[registry] Failed to push to {user_id}: {exc}")
            self._connections.pop(user_id, None)
            return False

    async def broadcast(self, payload: dict) -> int:
        sent = 0
        for user_id in list(self._connections.keys()):
            if await self.push(user_id, payload):
                sent += 1
        return sent

    def get_connected_users(self) -> list[str]:
        return list(self._connections.keys())


# Global singleton
registry = ConnectionRegistry()
