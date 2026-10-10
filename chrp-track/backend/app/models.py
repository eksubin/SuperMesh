"""Data shapes shared by the API, the mock mesh and the real pipeline.

Mirrors frontend/src/api/types.ts and CONTRACT.md. JSON uses camelCase.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

FileType = Literal["text", "audio", "video", "image"]
Direction = Literal["outgoing", "incoming"]
Status = Literal["queued", "compressing", "sending", "delivered", "failed"]
HopState = Literal["pending", "active", "acked", "failed"]
Kind = Literal["file", "message"]

ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "compressing", "sending"})
FINAL_STATUSES: frozenset[str] = frozenset({"delivered", "failed"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class HopStat(CamelModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    state: HopState = "pending"
    packet_loss_rate: Optional[float] = None
    rssi_dbm: Optional[float] = None


class MeshRoute(CamelModel):
    hops: list[str]
    current_hop_index: int = 0
    rssi_dbm: Optional[float] = None
    packet_loss_rate: Optional[float] = None
    hop_stats: list[HopStat] = Field(default_factory=list)


class MeshTransmissionEvent(CamelModel):
    file_id: str
    file_name: str
    file_type: FileType
    kind: Kind = "file"
    preview: Optional[str] = None
    direction: Direction = "outgoing"
    status: Status = "queued"

    size_bytes: int = 0
    progress_percentage: float = 0.0
    etr_seconds: Optional[float] = None
    total_queue_etr_seconds: Optional[float] = None

    packets_acked: int = 0
    packets_total: Optional[int] = None
    chunk_size_bytes: int = 230
    preset: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None
    failure_reason: Optional[str] = None

    route: Optional[MeshRoute] = None

    # Real-mesh additions: which simulated nodes were involved and whether the payload can be viewed.
    from_node: Optional[int] = None
    to_node: Optional[int] = None
    has_content: bool = False
    content_type: Optional[str] = None  # MIME type served by /api/transmissions/{id}/content

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES


class TextMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    from_node: Optional[int] = None
    to_node: Optional[int] = None


def to_json(event: MeshTransmissionEvent) -> dict:
    """Serialise for the wire: camelCase keys, ISO timestamps."""
    return event.model_dump(mode="json", by_alias=True)
