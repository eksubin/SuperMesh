"""FastAPI backend: boots the Meshtastic simulator and exposes send/receive over HTTP.

Run from the SmartMesh folder:
    source .venv/bin/activate
    uvicorn main:app --app-dir ui --port 8000
then open http://localhost:8000
"""
import base64
import faulthandler
import signal
import os
import queue
import sys
import threading
import time
from contextlib import asynccontextmanager

os.environ.setdefault("MPLBACKEND", "Agg")  # the simulator builds matplotlib objects; keep them off the GUI

faulthandler.register(signal.SIGUSR1, all_threads=True)  # kill -USR1 <pid> dumps every thread's stack to stderr

ROOT = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(os.path.dirname(ROOT), "Meshtasticator")
sys.path.insert(0, SIM_DIR)
os.chdir(SIM_DIR)

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from lib.interactive import InteractiveSim, HW_ID_OFFSET
from lib.blob import (HEADER, encode_chunks, BlobReceiver, KIND_TEXT, KIND_IMAGE, KIND_AUDIO, KIND_BINARY, CHUNK_SIZE)
from meshtastic import portnums_pb2
from pubsub import pub

# lib/gui.py forces the Tk backend, which crashes macOS when a figure is built off the main thread.
# The UI doesn't need the simulator's plots, so switch to the headless backend after the import.
import matplotlib
import lib.gui
matplotlib.use("Agg", force=True)
lib.gui.move_figure = lambda *a, **k: None

NR_NODES = 6
CHUNK_DELAY = 1.5  # seconds between chunks; the simulated radio drops packets if flooded
KINDS = {"text": KIND_TEXT, "image": KIND_IMAGE, "audio": KIND_AUDIO}
KIND_LABEL = {v: k for k, v in KINDS.items()}


class SimArgs:
    nrNodes = NR_NODES
    docker = True
    program = SIM_DIR
    from_file = False
    forward = False
    collisions = False
    script = False
    verbose = False


class State:
    def __init__(self):
        self.sim = None
        self.status = "booting"
        self.error = None
        self.log = []
        self.inbox = []  # received messages, newest last
        self.jobs = {}  # job id -> dict with progress
        self.receivers = []
        self.sendq = queue.Queue()
        self.lock = threading.Lock()
        self.next_id = 0

    def new_id(self):
        with self.lock:
            self.next_id += 1
            return self.next_id


state = State()
LOG_LIMIT = 1000
log_lock = threading.Lock()
log_seq = 0


def log(msg, level="info"):
    """Append an event the UI can show: level is info | tx | rx | ok | error."""
    global log_seq
    with log_lock:
        log_seq += 1
        state.log.append({"seq": log_seq, "ts": time.time(), "level": level, "msg": msg})
        del state.log[:-LOG_LIMIT]


def on_air(interface, packet):
    """Every packet a simulated node puts on the radio (chunks, ACKs, relays)."""
    try:
        sim = packet["decoded"]["simulator"]
        src = interface.myInfo.my_node_num - HW_ID_OFFSET
        dst = packet.get("to", 0) - HW_ID_OFFSET
        port = sim.get("portnum")
        size = len(base64.b64decode(sim.get("data", "")))
        what = "ACK/routing" if port == "ROUTING_APP" else port
        log(f"AIR  Node {src} transmits {what} ({size} bytes) addressed to Node {dst}, hop limit {packet.get('hopLimit', '?')}", "tx")
    except Exception as ex:
        log(f"AIR  (unparsed packet: {ex!r})", "tx")


def sniff_mime(kind, data):
    if kind == KIND_IMAGE:
        return "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    if kind == KIND_AUDIO:
        if data[:4] == b"\x1aE\xdf\xa3":
            return "audio/webm"
        if data[:4] == b"OggS":
            return "audio/ogg"
        return "audio/mp4"
    return "application/octet-stream"


def on_complete_for(node_id):
    def handler(kind, data, sender):
        msg = {"id": state.new_id(), "from": sender, "to": node_id, "kind": KIND_LABEL.get(kind, "binary"),
               "ts": time.time(), "size": len(data)}
        if kind == KIND_TEXT:
            msg["text"] = data.decode("utf-8", "replace")
        else:
            msg["mime"] = sniff_mime(kind, data)
            msg["data"] = base64.b64encode(data).decode()
        state.inbox.append(msg)
        log(f"Node {node_id} delivered a {msg['kind']} message from Node {sender} to its inbox", "ok")
    return handler


def boot_sim():
    try:
        log("Starting Docker container 'Meshtastic' with 6 simulated nodes...")
        os.system("docker rm -f Meshtastic > /dev/null 2>&1")
        state.sim = InteractiveSim(SimArgs())
        time.sleep(15)  # let the nodes settle, as in send_message.py
        state.receivers = [BlobReceiver(n, HW_ID_OFFSET, on_complete_for(n), lambda m, lv="rx": log(m, lv))
                           for n in range(NR_NODES)]
        pub.subscribe(on_air, "meshtastic.receive.simulator")
        state.status = "ready"
        log("Simulator ready: 6 nodes running in Docker, receivers attached", "ok")
    except BaseException as ex:  # InteractiveSim calls sys.exit on failure
        state.status = "error"
        state.error = repr(ex)
        log(f"Simulator failed to start: {ex!r}", "error")


def queue_watchdog():
    """Restore each node's client-side TX-queue credit.

    The meshtastic client claims one slot per packet it hands a node and only gets it back when the node reports
    queue status. The simulator forwards packets the node never reports on, so the credit leaks until it hits 0 and
    the single callback thread blocks forever in _sendToRadio ("Waiting for free space in TX Queue"). The counter
    is only client-side flow control, so top it up whenever the node has nothing queued.
    """
    while True:
        time.sleep(0.25)
        for n in state.sim.nodes if state.sim else []:
            q = getattr(n.iface, "queueStatus", None)
            if q is not None and q.free < q.maxlen and not n.iface.queue:
                if q.free <= 2:
                    log(f"Node {n.nodeid}: TX queue credit was down to {q.free}/{q.maxlen}, restored", "info")
                q.free = q.maxlen


def sender_loop():
    """One worker sends jobs one after another so chunks from different messages don't collide."""
    while True:
        job = state.sendq.get()
        sim = state.sim
        try:
            iface = sim.get_node_iface_by_id(job["from"])
            job["status"] = "sending"
            log(f"Job {job['id']}: Node {job['from']} starts sending {job['kind']} to Node {job['to']}", "tx")
            for i, payload in enumerate(job["chunks"], 1):
                tid, idx, total, kind, crc = HEADER.unpack(payload[:HEADER.size])
                log(f"Job {job['id']}: handing chunk {i}/{len(job['chunks'])} to Node {job['from']}'s radio "
                    f"(transfer {tid}, header {HEADER.size} B + {len(payload) - HEADER.size} B data, PRIVATE_APP, wantAck)", "tx")
                iface.sendData(payload, destinationId=sim.node_id_to_hw_id(job["to"]),
                               portNum=portnums_pb2.PortNum.PRIVATE_APP, wantAck=True)
                job["sent"] = i
                if i < len(job["chunks"]):
                    time.sleep(CHUNK_DELAY)
            job["status"] = "sent"
            log(f"Job {job['id']}: all {len(job['chunks'])} chunks handed to the radio; waiting for the mesh to deliver", "ok")
        except Exception as ex:
            job["status"] = "failed"
            job["error"] = repr(ex)
            log(f"Job {job['id']} failed: {ex!r}", "error")
        job.pop("chunks", None)


@asynccontextmanager
async def lifespan(app):
    threading.Thread(target=boot_sim, daemon=True).start()
    threading.Thread(target=sender_loop, daemon=True).start()
    threading.Thread(target=queue_watchdog, daemon=True).start()
    yield
    os.system("docker rm -f Meshtastic > /dev/null 2>&1")


app = FastAPI(lifespan=lifespan)


class SendRequest(BaseModel):
    from_node: int
    to_node: int
    kind: str  # text | image | audio
    text: str | None = None
    data: str | None = None  # base64 for image/audio


@app.get("/")
def index():
    return FileResponse(os.path.join(ROOT, "static", "index.html"))


@app.get("/api/debug")
def debug():
    """Free TX-queue slots the client believes each node has (0 = the sender blocks waiting)."""
    out = []
    for n in state.sim.nodes:
        q = n.iface.queueStatus
        out.append({"node": n.nodeid, "free": None if q is None else q.free, "queued": len(n.iface.queue)})
    return out


@app.get("/api/log")
def get_log(since: int = 0):
    return [e for e in state.log if e["seq"] > since]


@app.get("/api/state")
def get_state():
    progress = {}
    for r in state.receivers:
        for (sender, tid), (got, total) in r.progress().items():
            progress[f"{sender}->{r.node_id}:{tid}"] = {"from": sender, "to": r.node_id, "got": got, "total": total}
    return {
        "status": state.status, "error": state.error, "nodes": NR_NODES,
        "inbox": state.inbox,
        "jobs": [{k: v for k, v in j.items() if k != "chunks"} for j in state.jobs.values()],
        "incoming": list(progress.values()),
    }


@app.post("/api/send")
def send(req: SendRequest):
    if state.status != "ready":
        raise HTTPException(409, f"simulator is {state.status}")
    if req.kind not in KINDS:
        raise HTTPException(400, "kind must be text, image or audio")
    for n in (req.from_node, req.to_node):
        if not 0 <= n < NR_NODES:
            raise HTTPException(400, f"node must be 0..{NR_NODES - 1}")
    if req.from_node == req.to_node:
        raise HTTPException(400, "from and to must differ")
    if req.kind == "text":
        if not req.text:
            raise HTTPException(400, "text is empty")
        data = req.text.encode("utf-8")
    else:
        if not req.data:
            raise HTTPException(400, "data is empty")
        data = base64.b64decode(req.data)
    chunks = encode_chunks(data, KINDS[req.kind])
    job = {"id": state.new_id(), "from": req.from_node, "to": req.to_node, "kind": req.kind,
           "size": len(data), "total": len(chunks), "sent": 0, "status": "queued", "chunks": chunks}
    state.jobs[job["id"]] = job
    log(f"Job {job['id']}: request Node {req.from_node} -> Node {req.to_node}, {req.kind}, {len(data)} bytes "
        f"=> {len(chunks)} chunk(s) of up to {CHUNK_SIZE} B, {CHUNK_DELAY}s apart", "info")
    state.sendq.put(job)
    return {"job": job["id"], "chunks": len(chunks), "bytes": len(data)}
