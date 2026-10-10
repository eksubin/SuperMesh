"""The single way transmission updates reach the UI.

Both the mock mesh and the real pipeline call `publish_event`. It stores the
latest state, fills in the queue-wide ETR and pushes it to every open
WebSocket.
"""
from __future__ import annotations

import logging

from .hub import ConnectionHub
from .models import MeshTransmissionEvent, to_json
from .store import TransmissionStore

log = logging.getLogger("chrp.bus")

store = TransmissionStore()
hub = ConnectionHub()


async def publish_event(event: MeshTransmissionEvent, *, allow_reopen: bool = False) -> MeshTransmissionEvent:
    """Record and broadcast a transmission update.

    Final states are sticky: once an item is delivered or failed, late
    in-flight events for it are ignored unless `allow_reopen` is set (retry).
    """
    existing = store.get(event.file_id)
    if existing is not None and existing.is_final and event.is_active and not allow_reopen:
        log.info("ignoring late %s event for finished %s", event.status, event.file_id)
        return existing

    store.put(event)
    total = store.total_queue_etr()
    event.total_queue_etr_seconds = total
    await hub.broadcast({"type": "transmission", "event": to_json(event), "totalQueueEtrSeconds": total})
    return event


def snapshot_message() -> dict:
    return {
        "type": "snapshot",
        "transmissions": [to_json(t) for t in store.all()],
        "totalQueueEtrSeconds": store.total_queue_etr(),
    }
