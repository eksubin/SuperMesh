"""A stand-in mesh so the UI runs before the real pipeline exists.

Enabled with MOCK_MESH=1 (default). It fakes compression, ACK-based progress
over 2–4 hops with per-hop loss, occasional link failures, a seeded history
and the odd incoming transmission. Nothing here is used when MOCK_MESH=0.

Tip for demos: a file whose name contains "fail" always fails mid-route.
"""
from __future__ import annotations

import asyncio
import math
import os
import random
from datetime import timedelta
from typing import Optional

from .bus import publish_event, store
from .models import HopStat, MeshRoute, MeshTransmissionEvent, utcnow
from .pipeline import TransmissionJob

LOCAL_NODE = os.getenv("LOCAL_NODE", "Node 04")
RELAYS = ["Node 01", "Node 02", "Node 03", "Node 05", "Node 06", "Node 07"]
REMOTES = ["Node 09", "Node 11", "Node 12"]
CHUNK = 230
PRESET = "LoRa Fast · 22 kbps"
TICK = 0.4            # seconds between updates (contract: 250–500 ms)
MAX_PACKETS = 120     # keeps demo transfers under a minute or so
FAIL_RATE = float(os.getenv("MOCK_FAIL_RATE", "0.08"))


def _compressed_size(file_type: str, size: int) -> int:
    if file_type == "text":
        return max(40, int(size * 0.45))
    if file_type == "audio":
        return max(1200, int(size * 0.08))
    if file_type == "image":
        return max(1500, int(size * 0.06))
    return max(3000, int(size * 0.05))  # video: keyframes / thumbnail


def _route(outgoing: bool) -> tuple[list[str], list[float], list[int]]:
    hop_count = random.randint(2, 4)
    relays = random.sample(RELAYS, hop_count - 1)
    remote = random.choice(REMOTES)
    hops = [LOCAL_NODE, *relays, remote] if outgoing else [remote, *relays, LOCAL_NODE]
    losses = [round(random.uniform(0, 20), 1) for _ in range(hop_count)]
    rssis = [random.randint(-112, -72) for _ in range(hop_count)]
    return hops, losses, rssis


def _hop_stats(hops: list[str], losses: list[float], rssis: list[int], states: list[str]) -> list[HopStat]:
    return [
        HopStat(
            from_node=hops[i],
            to_node=hops[i + 1],
            state=states[i],  # type: ignore[arg-type]
            packet_loss_rate=losses[i] if states[i] in ("acked", "failed") else None,
            rssi_dbm=rssis[i] if states[i] != "pending" else None,
        )
        for i in range(len(hops) - 1)
    ]


async def simulate_outgoing(job: TransmissionJob) -> None:
    record = store.get(job.file_id)
    if record is None:
        return

    record = record.model_copy(update={"status": "compressing"})
    await publish_event(record)
    await asyncio.sleep(random.uniform(0.8, 1.6))

    total = min(MAX_PACKETS, max(1, math.ceil(_compressed_size(job.file_type, job.size_bytes) / CHUNK)))
    hops, losses, rssis = _route(outgoing=True)
    hop_count = len(hops) - 1
    success_p = math.prod(1 - l / 100 for l in losses)

    will_fail = "fail" in job.file_name.lower() or random.random() < FAIL_RATE
    fail_at: Optional[int] = random.randint(max(1, total * 3 // 10), max(1, total * 7 // 10)) if will_fail else None
    fail_hop = random.randrange(hop_count)

    acked = 0
    while acked < total:
        await asyncio.sleep(TICK)
        if random.random() < success_p:
            acked += 1

        if fail_at is not None and acked >= fail_at:
            states = ["acked"] * fail_hop + ["failed"] + ["pending"] * (hop_count - fail_hop - 1)
            await publish_event(record.model_copy(update={
                "status": "failed",
                "progress_percentage": round(acked / total * 100, 1),
                "packets_acked": acked,
                "packets_total": total,
                "etr_seconds": None,
                "completed_at": utcnow(),
                "failure_reason": f"Link timeout at hop {fail_hop + 1}",
                "route": MeshRoute(hops=hops, current_hop_index=fail_hop, rssi_dbm=rssis[fail_hop],
                                   packet_loss_rate=losses[fail_hop],
                                   hop_stats=_hop_stats(hops, losses, rssis, states)),
            }))
            return

        current = acked % hop_count
        await publish_event(record.model_copy(update={
            "status": "sending",
            "progress_percentage": round(acked / total * 100, 1),
            "packets_acked": acked,
            "packets_total": total,
            "chunk_size_bytes": CHUNK,
            "preset": PRESET,
            "etr_seconds": round((total - acked) * TICK / success_p, 1),
            "route": MeshRoute(hops=hops, current_hop_index=current, rssi_dbm=rssis[current],
                               packet_loss_rate=losses[current],
                               hop_stats=_hop_stats(hops, losses, rssis, ["active"] * hop_count)),
        }))

    await publish_event(record.model_copy(update={
        "status": "delivered",
        "progress_percentage": 100.0,
        "packets_acked": total,
        "packets_total": total,
        "chunk_size_bytes": CHUNK,
        "preset": PRESET,
        "etr_seconds": 0.0,
        "completed_at": utcnow(),
        "route": MeshRoute(hops=hops, current_hop_index=hop_count - 1, rssi_dbm=rssis[-1],
                           packet_loss_rate=losses[-1],
                           hop_stats=_hop_stats(hops, losses, rssis, ["acked"] * hop_count)),
    }))


def _finished(file_id: str, name: str, file_type: str, size: int, *, outgoing: bool, age: timedelta,
              failed: bool = False, kind: str = "file", preview: Optional[str] = None) -> MeshTransmissionEvent:
    hops, losses, rssis = _route(outgoing)
    hop_count = len(hops) - 1
    total = min(MAX_PACKETS, max(1, math.ceil(_compressed_size(file_type, size) / CHUNK)))
    if failed:
        fail_hop = random.randrange(hop_count)
        states = ["acked"] * fail_hop + ["failed"] + ["pending"] * (hop_count - fail_hop - 1)
        acked = max(1, total // 2)
    else:
        fail_hop, states, acked = hop_count - 1, ["acked"] * hop_count, total
    finished_at = utcnow() - age
    return MeshTransmissionEvent(
        file_id=file_id, file_name=name, file_type=file_type, kind=kind, preview=preview,  # type: ignore[arg-type]
        direction="outgoing" if outgoing else "incoming",
        status="failed" if failed else "delivered",
        size_bytes=size,
        progress_percentage=round(acked / total * 100, 1),
        etr_seconds=None if failed else 0.0,
        packets_acked=acked, packets_total=total, chunk_size_bytes=CHUNK, preset=PRESET,
        created_at=finished_at - timedelta(seconds=random.randint(20, 90)),
        completed_at=finished_at,
        failure_reason=f"Link timeout at hop {fail_hop + 1}" if failed else None,
        route=MeshRoute(hops=hops, current_hop_index=fail_hop, rssi_dbm=rssis[fail_hop],
                        packet_loss_rate=losses[fail_hop], hop_stats=_hop_stats(hops, losses, rssis, states)),
    )


def seed_history() -> None:
    """Pre-fill History so the demo doesn't start empty."""
    seeds = [
        ("TX-8921", "sitrep_alpha.txt", "text", 420, True, timedelta(hours=1), False),
        ("TX-8904", "ridge_check_02.opus", "audio", 9_830, False, timedelta(hours=3), False),
        ("TX-8890", "supply_list.txt", "text", 1_126, True, timedelta(hours=5), False),
        ("TX-8817", "perimeter_brief.opus", "audio", 12_288, False, timedelta(hours=21), False),
        ("TX-8806", "camp_overview.h265", "video", 31_744, True, timedelta(hours=23), False),
        ("TX-8773", "weather_note.txt", "text", 640, False, timedelta(days=1, hours=20), False),
        ("TX-8768", "visual_recon.h265", "video", 38_912, True, timedelta(days=2, hours=4), True),
        ("TX-8731", "field_notes_03.txt", "text", 2_458, True, timedelta(days=2, hours=17), False),
    ]
    for file_id, name, file_type, size, outgoing, age, failed in seeds:
        store.put(_finished(file_id, name, file_type, size, outgoing=outgoing, age=age, failed=failed))


_INCOMING = [
    ("file", "ridge_check_03.opus", "audio", 8_900, None),
    ("message", "Text message", "text", 44, "Water resupply at 1600, north trailhead."),
    ("file", "trail_marker.jpg", "image", 46_000, None),
    ("message", "Text message", "text", 29, "Copy that. Holding at camp 2."),
]


async def spawn_incoming_forever(new_id) -> None:
    """Every minute or so, a transmission arrives from another node."""
    while True:
        await asyncio.sleep(random.uniform(45, 90))
        kind, name, file_type, size, preview = random.choice(_INCOMING)
        event = _finished(new_id(), name, file_type, size, outgoing=False, age=timedelta(0),
                          kind=kind, preview=preview)
        await publish_event(event)
