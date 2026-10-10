"""Tracks open WebSocket connections and fans messages out to all of them."""
from __future__ import annotations

import logging

from fastapi import WebSocket

log = logging.getLogger("chrp.hub")


class ConnectionHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:  # client went away mid-send
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        if dead:
            log.debug("dropped %d stale websocket(s)", len(dead))
