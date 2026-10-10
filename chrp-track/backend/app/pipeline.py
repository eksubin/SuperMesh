"""Real mesh pipeline: prepare (compress) -> chunk -> send through the simulator -> ACK-based progress.

Used when MOCK_MESH=0 (see app/mesh.py for the simulator host). The API layer calls `submit_transmission` and
`retry_transmission`; progress reaches the UI through `publish_event` like everything else (CONTRACT.md).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .bus import hub, publish_event, store
from . import neural, voice
from .mesh import CHUNK_DELAY, CHUNK_SIZE, KIND_AUDIO_NEURAL, KIND_FOR_TYPE, KIND_IMAGE_NEURAL, Transfer, encode_chunks, host, HEADER, sniff_mime
from .models import FileType, HopStat, Kind, MeshRoute, MeshTransmissionEvent, utcnow

log = logging.getLogger("chrp.pipeline")

IMAGE_MAX_SIDE = int(os.getenv("IMAGE_MAX_SIDE", "96"))     # images are shrunk to fit this box before sending
IMAGE_QUALITY = int(os.getenv("IMAGE_QUALITY", "40"))       # JPEG quality used for the shrunken copy
MAX_MESH_BYTES = int(os.getenv("MAX_MESH_BYTES", str(12 * 1024)))  # bigger payloads would take minutes of airtime
IMAGE_CODEC = os.getenv("IMAGE_CODEC", "neural")            # default for images: "neural" (CompressAI) or "jpeg"
VOICE_CODEC = os.getenv("VOICE_CODEC", "neural")            # default for audio: "neural" (EnCodec) or "raw" (send the recording as is)
BOOT_WAIT_SECONDS = 180
DEFAULT_FROM_NODE = int(os.getenv("MESH_LOCAL_NODE", "0"))
DEFAULT_TO_NODE = int(os.getenv("MESH_PEER_NODE", "5"))

_loop: Optional[asyncio.AbstractEventLoop] = None


@dataclass
class TransmissionJob:
    """Everything the pipeline needs to start sending one item."""

    file_id: str
    file_name: str
    file_type: FileType
    kind: Kind
    size_bytes: int
    created_at: datetime
    path: Optional[Path] = None   # uploaded file on disk (kind == "file")
    text: Optional[str] = None    # message body (kind == "message")
    from_node: int = DEFAULT_FROM_NODE
    to_node: int = DEFAULT_TO_NODE
    image_codec: Optional[str] = None  # "neural" | "jpeg"; None means the server default
    voice_codec: Optional[str] = None  # "neural" | "raw"; None means the server default
    voice_bitrate: Optional[float] = None  # kbps for the neural voice codec; None means the server default


@dataclass
class Prepared:
    """A payload ready for chunking, plus what the sender's own screen should show for it."""

    payload: bytes        # what travels over the air
    file_type: str
    kind: int             # chunk header kind byte
    content: bytes        # what "Sent content" shows
    content_mime: str
    label: str            # how it was compressed, for the details screen


class PrepareError(Exception):
    """The item can't be sent; the message is shown to the user."""


# --------------------------------------------------------------------------- wiring

def attach(loop: asyncio.AbstractEventLoop, content_dir: Path) -> None:
    """Start the simulator and route its thread-side callbacks onto the asyncio loop."""
    global _loop
    _loop = loop
    host.on_progress = lambda t: _run(_apply_progress(t))
    host.on_delivered = lambda t, dest, data: _run(_apply_delivered(t, dest, data))
    host.on_failed = lambda t, reason: _run(_apply_failed(t, reason))
    host.on_log = lambda entry: _run(hub.broadcast({"type": "log", "entry": entry}))
    host.start(loop, content_dir)
    threading.Thread(target=_warm_codec, name="neural-warmup", daemon=True).start()


def _warm_codec() -> None:
    """Load the neural models while the simulator boots so the first picture or recording isn't slow."""
    if IMAGE_CODEC == "neural":
        host.log("Loading the neural image codec…")
        if neural.warm_up():
            host.log(f"Neural image codec ready on {neural.status()['device']}", "ok")
        else:
            host.log(f"Neural image codec unavailable, using JPEG instead ({neural.status()['error']})", "error")
    if VOICE_CODEC == "neural":
        host.log("Loading the EnCodec voice codec…")
        if voice.warm_up():
            host.log("EnCodec voice codec ready on cpu", "ok")
        else:
            host.log(f"EnCodec voice codec unavailable, sending recordings as they are ({voice.status()['error']})", "error")


def detach() -> None:
    host.stop()


def _run(coro) -> None:
    """Schedule a coroutine from a simulator thread."""
    if _loop is not None:
        asyncio.run_coroutine_threadsafe(coro, _loop)


async def _update(file_id: str, **fields) -> Optional[MeshTransmissionEvent]:
    record = store.get(file_id)
    if record is None:
        return None
    return await publish_event(record.model_copy(update=fields))


def _new_rx_id() -> str:
    while True:
        candidate = f"RX-{secrets.randbelow(9000) + 1000}"
        if not store.contains(candidate):
            return candidate


def _fmt_bytes(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


# --------------------------------------------------------------------------- preparing the payload

def _shrink_image(raw: bytes) -> bytes:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover
        raise PrepareError("Image support isn't installed on the server (pip install Pillow).") from exc
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, (255, 255, 255))
            flat.paste(img, mask=img.split()[-1])
            img = flat
        else:
            img = img.convert("RGB")
        img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=IMAGE_QUALITY, optimize=True)
        return out.getvalue()
    except Exception as exc:
        raise PrepareError("Couldn’t read that image. Try a JPEG or PNG.") from exc


def _prepare_image(raw: bytes, codec: str) -> Prepared:
    if codec == "neural":
        if neural.available():
            try:
                bits, source_png = neural.compress(raw)
                return Prepared(bits, "image", KIND_IMAGE_NEURAL, source_png, "image/png",
                                f"Neural codec · {_fmt_bytes(len(bits))} sent")
            except Exception as exc:
                host.log(f"Neural codec failed ({exc!r}), using JPEG for this picture", "error")
        else:
            host.log("Neural codec isn’t available, using JPEG for this picture", "error")
    jpeg = _shrink_image(raw)
    return Prepared(jpeg, "image", KIND_FOR_TYPE["image"], jpeg, "image/jpeg",
                    f"JPEG {IMAGE_MAX_SIDE}px · {_fmt_bytes(len(jpeg))} sent")


def _prepare_audio(raw: bytes, codec: str, bitrate: float) -> Prepared:
    if codec == "neural":
        if voice.available():
            try:
                bits, source_wav = voice.compress(raw, bitrate)
                return Prepared(bits, "audio", KIND_AUDIO_NEURAL, source_wav, "audio/wav",
                                f"EnCodec {bitrate:g} kbps · {_fmt_bytes(len(bits))} sent")
            except ValueError as exc:
                host.log(f"Voice codec couldn’t read this recording ({exc}); sending it as is", "error")
            except Exception as exc:
                host.log(f"Voice codec failed ({exc!r}); sending the recording as is", "error")
        else:
            host.log("Voice codec isn’t available; sending the recording as is", "error")
    return Prepared(raw, "audio", KIND_FOR_TYPE["audio"], raw, sniff_mime("audio", raw),
                    f"Original recording · {_fmt_bytes(len(raw))} sent")


def prepare(job: TransmissionJob) -> Prepared:
    """Compress the item for the mesh. Runs in a worker thread."""
    if job.kind == "message":
        data = (job.text or "").encode("utf-8")
        if not data:
            raise PrepareError("There is nothing to send.")
        prepared = Prepared(data, "text", KIND_FOR_TYPE["text"], data, sniff_mime("text", data),
                            f"Simulated mesh · {_fmt_bytes(len(data))} sent")
    else:
        if job.path is None or not job.path.exists():
            raise PrepareError("The uploaded file is no longer on the server.")
        raw = job.path.read_bytes()
        if not raw:
            raise PrepareError("There is nothing to send.")
        if job.file_type == "image":
            prepared = _prepare_image(raw, job.image_codec or IMAGE_CODEC)
        elif job.file_type == "audio":
            prepared = _prepare_audio(raw, job.voice_codec or VOICE_CODEC, job.voice_bitrate or voice.DEFAULT_BITRATE)
        else:
            prepared = Prepared(raw, job.file_type, KIND_FOR_TYPE[job.file_type], raw,
                                sniff_mime(job.file_type, raw), f"Simulated mesh · {_fmt_bytes(len(raw))} sent")
    if len(prepared.payload) > MAX_MESH_BYTES:
        raise PrepareError(
            f"Too large for the mesh: {_fmt_bytes(len(prepared.payload))} after compression, the limit is "
            f"{_fmt_bytes(MAX_MESH_BYTES)}. Try a smaller file or a shorter clip."
        )
    return prepared


# --------------------------------------------------------------------------- API entry points

async def _fail(file_id: str, reason: str) -> None:
    await _update(file_id, status="failed", etr_seconds=None, completed_at=utcnow(), failure_reason=reason)


async def _wait_ready(file_id: str) -> bool:
    waited = 0.0
    while host.status == "booting" and waited < BOOT_WAIT_SECONDS:
        await asyncio.sleep(0.5)
        waited += 0.5
    if host.status == "ready":
        return True
    if host.status == "error":
        await _fail(file_id, f"The mesh simulator failed to start: {host.error}")
    else:
        await _fail(file_id, "The mesh simulator isn’t running.")
    return False


async def submit_transmission(job: TransmissionJob) -> None:
    """Compress -> chunk -> hand to the worker thread, which streams ACK-based progress until delivered/failed."""
    await _update(job.file_id, status="compressing", from_node=job.from_node, to_node=job.to_node)
    try:
        prep = await asyncio.to_thread(prepare, job)
    except PrepareError as exc:
        await _fail(job.file_id, str(exc))
        return
    if not await _wait_ready(job.file_id):
        return

    chunks = encode_chunks(prep.payload, prep.kind)
    tid = HEADER.unpack(chunks[0][:HEADER.size])[0]
    transfer = Transfer(file_id=job.file_id, from_node=job.from_node, to_node=job.to_node, file_type=prep.file_type,
                        data=prep.payload, chunks=chunks, tid=tid, kind=prep.kind)
    host.save_content(job.file_id, prep.content, prep.content_mime)
    host.log(f"{job.file_id}: Node {job.from_node} -> Node {job.to_node}, {prep.file_type}, "
             f"{_fmt_bytes(job.size_bytes)} original, {prep.label} => {len(chunks)} chunk(s) of up to {CHUNK_SIZE} B")
    await _update(
        job.file_id, status="queued", packets_total=len(chunks), packets_acked=0, progress_percentage=0.0,
        chunk_size_bytes=CHUNK_SIZE, etr_seconds=round(len(chunks) * CHUNK_DELAY, 1),
        preset=prep.label, has_content=True, content_type=prep.content_mime,
    )
    host.enqueue(transfer)


async def retry_transmission(job: TransmissionJob) -> None:
    """Resend only the chunks the destination never acknowledged."""
    transfer = host.transfers.get(job.file_id)
    if transfer is None:
        await submit_transmission(job)
        return
    if not await _wait_ready(job.file_id):
        return
    missing = len(transfer.chunks) - len(transfer.received)
    host.log(f"{job.file_id}: retry, {missing} of {len(transfer.chunks)} chunk(s) still missing")
    await _update(job.file_id, status="queued", etr_seconds=round(missing * CHUNK_DELAY, 1))
    host.requeue(transfer)


# --------------------------------------------------------------------------- events from the simulator threads

def _build_route(t: Transfer, outcome: str) -> MeshRoute:
    hops = [f"Node {n}" for n in host.route_for(t)]
    # Share of chunk transmissions that were resends: the only loss figure we can measure honestly.
    loss = round(100 * t.resends / t.sends, 1) if t.sends else None
    stats = []
    for i in range(len(hops) - 1):
        last = i == len(hops) - 2
        state = "acked" if outcome == "delivered" or not last else "failed"
        stats.append(HopStat(from_node=hops[i], to_node=hops[i + 1], state=state,
                             packet_loss_rate=loss if i == 0 else None, rssi_dbm=None))
    return MeshRoute(hops=hops, current_hop_index=len(stats) - 1, packet_loss_rate=loss, hop_stats=stats)


async def _apply_progress(t: Transfer) -> None:
    total = len(t.chunks)
    acked = len(t.received)
    remaining = total - acked
    await _update(
        t.file_id, status="sending", packets_acked=acked, packets_total=total,
        progress_percentage=round(100 * acked / total, 1) if total else 0.0,
        etr_seconds=round(remaining * CHUNK_DELAY, 1) if remaining else 0.0,
    )


async def _apply_delivered(t: Transfer, dest: int, data: bytes) -> None:
    total = len(t.chunks)

    # What the destination shows: for a neural bitstream, the picture it decodes from those bytes.
    rx_id = _new_rx_id()
    rx_label = f"Simulated mesh · {_fmt_bytes(len(data))} received"
    if t.kind in (KIND_IMAGE_NEURAL, KIND_AUDIO_NEURAL):
        is_image = t.kind == KIND_IMAGE_NEURAL
        decode = neural.decompress if is_image else voice.decompress
        try:
            decoded = await asyncio.to_thread(decode, data)
        except Exception as exc:
            host.log(f"{t.file_id}: Node {dest} couldn’t decode the neural bitstream ({exc!r})", "error")
            await _apply_failed(t, f"The {'image' if is_image else 'voice message'} arrived but couldn’t be decoded")
            return
        mime = "image/png" if is_image else "audio/wav"
        host.save_content(rx_id, decoded, mime)
        rx_label = f"{'Neural codec' if is_image else 'EnCodec'} · decoded from {_fmt_bytes(len(data))} received"
        host.log(f"{rx_id}: Node {dest} decoded {_fmt_bytes(len(data))} of bitstream into "
                 f"{'a' if is_image else 'an'} {_fmt_bytes(len(decoded))} {'PNG' if is_image else 'WAV'}", "ok")
    else:
        path, mime = host.contents[t.file_id]
        host.contents[rx_id] = (path, mime)

    route = _build_route(t, "delivered")
    await _update(t.file_id, status="delivered", packets_acked=total, packets_total=total, progress_percentage=100.0,
                  etr_seconds=None, completed_at=utcnow(), failure_reason=None, route=route)

    # The same bytes now sit at the destination: record them as an incoming item for that node.
    is_text = t.file_type == "text"
    names = {"text": "Text message", "image": "Received image", "audio": "Voice message", "video": "Received video"}
    await publish_event(MeshTransmissionEvent(
        file_id=rx_id, file_name=names[t.file_type], file_type=t.file_type,  # type: ignore[arg-type]
        kind="message" if is_text else "file",
        preview=data.decode("utf-8", "replace")[:140] if is_text else None,
        direction="incoming", status="delivered", size_bytes=len(data), progress_percentage=100.0,
        packets_acked=total, packets_total=total, chunk_size_bytes=CHUNK_SIZE,
        preset=rx_label, created_at=utcnow(), completed_at=utcnow(),
        route=route, from_node=t.from_node, to_node=dest, has_content=True, content_type=mime,
    ))
    host.log(f"{rx_id}: Node {dest} delivered the {t.file_type} from Node {t.from_node} to its inbox", "ok")


async def _apply_failed(t: Transfer, reason: str) -> None:
    await _update(t.file_id, status="failed", etr_seconds=None, completed_at=utcnow(), failure_reason=reason,
                  route=_build_route(t, "failed"))
