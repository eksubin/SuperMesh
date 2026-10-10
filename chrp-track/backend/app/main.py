"""CHRP Track API — the bridge between the iPhone UI and the mesh pipeline.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import mock, pipeline
from .bus import hub, publish_event, snapshot_message, store
from .classify import classify, safe_filename
from .models import MeshTransmissionEvent, TextMessageIn, to_json, utcnow
from .pipeline import TransmissionJob

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("chrp.api")

MOCK_MESH = os.getenv("MOCK_MESH", "1") == "1"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN") or None
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
CHUNK_SIZE = 230
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
NR_NODES = 6
DEFAULT_FROM_NODE = int(os.getenv("MESH_LOCAL_NODE", "0"))
DEFAULT_TO_NODE = int(os.getenv("MESH_PEER_NODE", "5"))

_jobs: dict[str, TransmissionJob] = {}
_tasks: set[asyncio.Task] = set()


def new_id() -> str:
    while True:
        candidate = f"TX-{secrets.randbelow(9000) + 1000}"
        if not store.contains(candidate):
            return candidate


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    background: list[asyncio.Task] = []
    if MOCK_MESH:
        log.info("MOCK_MESH=1: using the built-in mock mesh")
        mock.seed_history()
        background.append(asyncio.create_task(mock.spawn_incoming_forever(new_id)))
    else:
        log.info("MOCK_MESH=0: booting the Meshtasticator simulator (about 30 s)")
        pipeline.attach(asyncio.get_running_loop(), UPLOAD_DIR / "content")
    yield
    if not MOCK_MESH:
        pipeline.detach()
    for task in background + list(_tasks):
        task.cancel()


app = FastAPI(title="CHRP Track API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


# --------------------------------------------------------------------------- helpers

async def _mark_failed(file_id: str, reason: str) -> None:
    record = store.get(file_id)
    if record is None or record.is_final:
        return
    await publish_event(record.model_copy(update={
        "status": "failed", "etr_seconds": None, "completed_at": utcnow(), "failure_reason": reason,
    }))


def _launch(job: TransmissionJob, *, retry: bool = False) -> None:
    async def runner() -> None:
        try:
            if MOCK_MESH:
                await mock.simulate_outgoing(job)
            elif retry:
                await pipeline.retry_transmission(job)
            else:
                await pipeline.submit_transmission(job)
        except asyncio.CancelledError:
            raise
        except NotImplementedError as exc:
            await _mark_failed(job.file_id, str(exc) or "Mesh pipeline not connected yet")
        except Exception:
            log.exception("transmission %s crashed", job.file_id)
            await _mark_failed(job.file_id, "Internal error")

    task = asyncio.create_task(runner())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _resolve_nodes(from_node: Optional[int], to_node: Optional[int]) -> tuple[int, int]:
    src = DEFAULT_FROM_NODE if from_node is None else from_node
    dst = DEFAULT_TO_NODE if to_node is None else to_node
    for n in (src, dst):
        if not 0 <= n < NR_NODES:
            raise HTTPException(422, f"Node must be between 0 and {NR_NODES - 1}.")
    if src == dst:
        raise HTTPException(422, "Pick two different nodes.")
    return src, dst


def mesh_host():
    from .mesh import host  # imported lazily: needs the simulator libraries, which the mock doesn't
    return host


def _get_or_404(file_id: str) -> MeshTransmissionEvent:
    record = store.get(file_id)
    if record is None:
        raise HTTPException(404, "Transmission not found")
    return record


# --------------------------------------------------------------------------- REST

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "mock": MOCK_MESH, "clients": hub.count}


@app.post("/api/transmissions", status_code=202)
async def create_transmission(
    file: UploadFile = File(...),
    from_node: Optional[int] = Form(None),
    to_node: Optional[int] = Form(None),
    file_type_hint: Optional[str] = Form(None, alias="file_type"),
    image_codec: Optional[str] = Form(None),
    voice_codec: Optional[str] = Form(None),
    voice_bitrate: Optional[float] = Form(None),
) -> dict:
    if image_codec not in (None, "neural", "jpeg"):
        raise HTTPException(422, "image_codec must be neural or jpeg.")
    if voice_codec not in (None, "neural", "raw"):
        raise HTTPException(422, "voice_codec must be neural or raw.")
    if voice_bitrate is not None and voice_bitrate not in (1.5, 3.0, 6.0, 12.0, 24.0):
        raise HTTPException(422, "voice_bitrate must be 1.5, 3, 6, 12 or 24 (kbps).")
    src, dst = _resolve_nodes(from_node, to_node)
    file_type = classify(file.filename, file.content_type)
    # A browser voice recording is .webm, which looks like video by extension: let the app say it is audio.
    if file_type_hint in ("text", "audio", "video", "image"):
        file_type = file_type_hint  # type: ignore[assignment]
    if file_type is None:
        raise HTTPException(415, "Unsupported file type. Send a text, audio, video or image file.")

    file_id = new_id()
    name = safe_filename(file.filename)
    dest = UPLOAD_DIR / f"{file_id}_{name}"
    size = 0
    too_big = False
    with dest.open("wb") as out:
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                too_big = True
                break
            out.write(chunk)
    if too_big:
        dest.unlink(missing_ok=True)
        raise HTTPException(413, f"File is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "The file is empty.")

    record = MeshTransmissionEvent(
        file_id=file_id, file_name=name, file_type=file_type, kind="file", direction="outgoing",
        status="queued", size_bytes=size, chunk_size_bytes=CHUNK_SIZE, created_at=utcnow(),
        from_node=None if MOCK_MESH else src, to_node=None if MOCK_MESH else dst,
    )
    record = await publish_event(record)
    job = TransmissionJob(file_id=file_id, file_name=name, file_type=file_type, kind="file",
                          size_bytes=size, created_at=record.created_at, path=dest, from_node=src, to_node=dst,
                          image_codec=image_codec, voice_codec=voice_codec, voice_bitrate=voice_bitrate)
    _jobs[file_id] = job
    _launch(job)
    return to_json(record)


@app.post("/api/messages", status_code=202)
async def create_message(body: TextMessageIn) -> dict:
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Message is empty.")
    src, dst = _resolve_nodes(body.from_node, body.to_node)
    file_id = new_id()
    size = len(text.encode("utf-8"))
    record = MeshTransmissionEvent(
        file_id=file_id, file_name="Text message", file_type="text", kind="message", preview=text[:140],
        direction="outgoing", status="queued", size_bytes=size, chunk_size_bytes=CHUNK_SIZE, created_at=utcnow(),
        from_node=None if MOCK_MESH else src, to_node=None if MOCK_MESH else dst,
    )
    record = await publish_event(record)
    job = TransmissionJob(file_id=file_id, file_name="Text message", file_type="text", kind="message",
                          size_bytes=size, created_at=record.created_at, text=text, from_node=src, to_node=dst)
    _jobs[file_id] = job
    _launch(job)
    return to_json(record)


@app.post("/api/transmissions/{file_id}/retry", status_code=202)
async def retry(file_id: str) -> dict:
    record = _get_or_404(file_id)
    if record.status != "failed" or record.direction != "outgoing":
        raise HTTPException(409, "Only failed outgoing transmissions can be retried.")

    reset = record.model_copy(update={
        "status": "queued", "progress_percentage": 0.0, "packets_acked": 0, "etr_seconds": None,
        "completed_at": None, "failure_reason": None, "route": None, "created_at": utcnow(),
    })
    reset = await publish_event(reset, allow_reopen=True)
    job = _jobs.get(file_id) or TransmissionJob(
        file_id=file_id, file_name=record.file_name, file_type=record.file_type, kind=record.kind,
        size_bytes=record.size_bytes, created_at=reset.created_at, text=record.preview,
        from_node=record.from_node if record.from_node is not None else DEFAULT_FROM_NODE,
        to_node=record.to_node if record.to_node is not None else DEFAULT_TO_NODE,
    )
    _launch(job, retry=True)
    return to_json(reset)


@app.get("/api/transmissions")
async def list_transmissions(scope: Literal["all", "active", "history"] = Query("all")) -> list[dict]:
    items = {"all": store.all, "active": store.active, "history": store.history}[scope]()
    return [to_json(t) for t in items]


@app.get("/api/transmissions/{file_id}")
async def get_transmission(file_id: str) -> dict:
    return to_json(_get_or_404(file_id))


@app.get("/api/transmissions/{file_id}/content")
async def get_content(file_id: str):
    """The payload itself (image, audio, text), for previews and playback. Supports range requests for audio."""
    _get_or_404(file_id)
    entry = None if MOCK_MESH else mesh_host().contents.get(file_id)
    if entry is None or not entry[0].exists():
        raise HTTPException(404, "No content stored for this transmission.")
    path, mime = entry
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


@app.get("/api/mesh")
async def mesh_info() -> dict:
    """Simulator state for the UI: whether the nodes are up and which ones the phone talks to by default."""
    if MOCK_MESH:
        return {"mock": True, "status": "ready", "error": None, "nodes": NR_NODES,
                "localNode": DEFAULT_FROM_NODE, "peerNode": DEFAULT_TO_NODE,
                "codecs": {"default": "jpeg", "neural": "off", "voiceDefault": "raw", "voice": "off", "voiceBitrate": 6}}
    from . import neural, voice
    from .pipeline import IMAGE_CODEC, VOICE_CODEC
    host = mesh_host()
    return {"mock": False, "status": host.status, "error": host.error, "nodes": NR_NODES,
            "localNode": DEFAULT_FROM_NODE, "peerNode": DEFAULT_TO_NODE,
            "codecs": {"default": IMAGE_CODEC, "neural": neural.status()["state"],
                       "voiceDefault": VOICE_CODEC, "voice": voice.status()["state"], "voiceBitrate": voice.DEFAULT_BITRATE}}


@app.post("/api/mesh/restart", status_code=202)
async def mesh_restart() -> dict:
    """Throw the simulator away and start a fresh one (the UI shows the booting notice meanwhile)."""
    if MOCK_MESH:
        raise HTTPException(409, "The mock mesh has nothing to restart.")
    mesh_host().restart()
    return {"status": "booting"}


@app.get("/api/log")
async def mesh_log(since: int = Query(0, ge=0)) -> list[dict]:
    return [] if MOCK_MESH else mesh_host().logs_since(since)


@app.post("/internal/events")
async def ingest_event(event: MeshTransmissionEvent,
                       x_internal_token: Optional[str] = Header(default=None)) -> dict:
    """For a pipeline running as a separate service: POST each update here."""
    if INTERNAL_TOKEN and x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(401, "Bad internal token")
    stored = await publish_event(event)
    return {"ok": True, "status": stored.status}


# --------------------------------------------------------------------------- WebSocket

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        await ws.send_json(snapshot_message())
        while True:
            message = await ws.receive_text()
            if message == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(ws)


# --------------------------------------------------------------------------- built front end

if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
