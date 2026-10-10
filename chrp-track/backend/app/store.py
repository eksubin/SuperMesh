"""In-memory record of every transmission (active and history).

Good enough for the hackathon demo. Swap for SQLite/Redis if history must
survive a restart.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .models import MeshTransmissionEvent


class TransmissionStore:
    def __init__(self) -> None:
        self._items: dict[str, MeshTransmissionEvent] = {}

    def get(self, file_id: str) -> Optional[MeshTransmissionEvent]:
        return self._items.get(file_id)

    def put(self, event: MeshTransmissionEvent) -> MeshTransmissionEvent:
        self._items[event.file_id] = event
        return event

    def all(self) -> list[MeshTransmissionEvent]:
        return sorted(self._items.values(), key=lambda t: t.created_at, reverse=True)

    def active(self) -> list[MeshTransmissionEvent]:
        return [t for t in self.all() if t.is_active]

    def history(self) -> list[MeshTransmissionEvent]:
        items: Iterable[MeshTransmissionEvent] = (t for t in self._items.values() if t.is_final)
        return sorted(items, key=lambda t: t.completed_at or t.created_at, reverse=True)

    def contains(self, file_id: str) -> bool:
        return file_id in self._items

    def total_queue_etr(self) -> Optional[float]:
        """Sum of ETRs for outgoing items still in flight.

        Returns None when any of them has no estimate yet, so the UI can say
        "Estimating…" rather than show a number that is too low.
        """
        outgoing = [t for t in self._items.values() if t.is_active and t.direction == "outgoing"]
        if any(t.etr_seconds is None for t in outgoing):
            return None
        return round(sum(t.etr_seconds or 0.0 for t in outgoing), 1)
