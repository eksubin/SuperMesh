"""Real mesh backend: runs the Meshtasticator Docker simulator and moves chunked data between its nodes.

Only imported when MOCK_MESH=0. The simulator and its meshtastic client libraries are synchronous and threaded, so
everything here runs in threads and hands results to the asyncio side with `run_coroutine_threadsafe`.

How a transmission travels
  1. app.pipeline prepares the payload (compress / validate) and calls `MeshHost.enqueue`.
  2. One worker thread splits it into 180-byte chunks (lib/blob.py) and hands them to the sending node's radio.
  3. The simulator floods them through the mesh. Every chunk that reaches the destination node counts as ACKed.
  4. Chunks that never arrive are resent (only those) for a few rounds. Delivered = every chunk arrived and the CRC
     of the rebuilt bytes matches.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

os.environ.setdefault("MPLBACKEND", "Agg")  # the simulator builds matplotlib figures; keep them off the GUI

SIM_DIR = Path(os.getenv("MESHTASTICATOR_DIR", Path(__file__).resolve().parents[3] / "Meshtasticator"))
sys.path.insert(0, str(SIM_DIR))  # the simulator's `lib` package

from lib.blob import CHUNK_SIZE, HEADER, encode_chunks  # noqa: E402  (after the path tweak)

NR_NODES = 6
CHUNK_DELAY = float(os.getenv("MESH_CHUNK_DELAY", "1.5"))  # seconds between chunks; the simulated radio drops floods
ROUND_WAIT = float(os.getenv("MESH_ROUND_WAIT", "10"))     # after the last chunk, wait this long for stragglers
MAX_ROUNDS = int(os.getenv("MESH_MAX_ROUNDS", "4"))        # send, then resend only the missing chunks this many times
SETTLE_SECONDS = 15                                        # let the nodes finish booting before accepting traffic
LOG_LIMIT = 1000

# Payload kind byte carried in every chunk header (lib/blob.py defines 0-3).
KIND_TEXT, KIND_IMAGE, KIND_AUDIO, KIND_BINARY = 0, 1, 2, 3
KIND_IMAGE_NEURAL = 4  # the payload is a neural-codec bitstream (app/neural.py), not a picture file
KIND_AUDIO_NEURAL = 5  # the payload is an EnCodec bitstream (app/voice.py), not an audio file
KIND_FOR_TYPE = {"text": KIND_TEXT, "image": KIND_IMAGE, "audio": KIND_AUDIO, "video": KIND_BINARY}
TYPE_FOR_KIND = {v: k for k, v in KIND_FOR_TYPE.items()}

logger = logging.getLogger("chrp.mesh")


@dataclass
class Transfer:
    file_id: str
    from_node: int
    to_node: int
    file_type: str
    data: bytes
    chunks: list[bytes]
    tid: int
    kind: int = KIND_BINARY  # kind byte carried in every chunk header
    received: set[int] = field(default_factory=set)
    sends: int = 0           # chunk transmissions handed to the radio (including resends)
    resends: int = 0
    relays: list[int] = field(default_factory=list)  # nodes other than ends that were heard transmitting a chunk
    done: threading.Event = field(default_factory=threading.Event)
    delivered: bool = False
    started_at: float = 0.0


def sniff_mime(file_type: str, data: bytes) -> str:
    if file_type == "text":
        return "text/plain; charset=utf-8"
    if file_type == "image":
        if data[:4] == b"\x89PNG":
            return "image/png"
        return "image/jpeg"
    if file_type == "audio":
        if data[:4] == b"\x1aE\xdf\xa3":
            return "audio/webm"
        if data[:4] == b"OggS":
            return "audio/ogg"
        if data[:4] == b"RIFF":
            return "audio/wav"
        if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
            return "audio/mpeg"
        return "audio/mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "video/webm"
    return "video/mp4"


class MeshHost:
    def __init__(self) -> None:
        self.status = "off"  # off | booting | ready | error
        self.error: Optional[str] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.sim: Any = None
        self.receivers: list[Any] = []
        self.transfers: dict[str, Transfer] = {}
        self._by_key: dict[tuple[int, int], Transfer] = {}  # (sender node, transfer id)
        self._sendq: "queue.Queue[Transfer]" = queue.Queue()
        self.contents: dict[str, tuple[Path, str]] = {}      # file_id -> (file on disk, mime)
        self.content_dir: Path = Path("./data/uploads/content")
        self.logs: list[dict] = []
        self._log_seq = 0
        self._log_lock = threading.Lock()
        # Wired up by app.pipeline:
        self.on_progress: Callable[[Transfer], None] = lambda t: None
        self.on_delivered: Callable[[Transfer, int, bytes], None] = lambda t, dest, data: None
        self.on_failed: Callable[[Transfer, str], None] = lambda t, reason: None
        self.on_log: Callable[[dict], None] = lambda entry: None

    # ------------------------------------------------------------------ logging

    def log(self, msg: str, level: str = "info") -> None:
        with self._log_lock:
            self._log_seq += 1
            entry = {"seq": self._log_seq, "ts": time.time(), "level": level, "msg": msg}
            self.logs.append(entry)
            del self.logs[:-LOG_LIMIT]
        self.on_log(entry)

    def logs_since(self, seq: int) -> list[dict]:
        return [e for e in self.logs if e["seq"] > seq]

    # ------------------------------------------------------------------ lifecycle

    def start(self, loop: asyncio.AbstractEventLoop, content_dir: Path) -> None:
        self.loop = loop
        self.content_dir = content_dir
        content_dir.mkdir(parents=True, exist_ok=True)
        self.status = "booting"
        threading.Thread(target=self._boot, name="mesh-boot", daemon=True).start()
        threading.Thread(target=self._sender_loop, name="mesh-sender", daemon=True).start()
        threading.Thread(target=self._queue_watchdog, name="mesh-watchdog", daemon=True).start()
        threading.Thread(target=self._supervise, name="mesh-supervisor", daemon=True).start()

    def stop(self) -> None:
        os.system("docker rm -f Meshtastic > /dev/null 2>&1")

    def _container_running(self) -> bool:
        try:
            r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "Meshtastic"],
                               capture_output=True, text=True, timeout=8)
            return r.returncode == 0 and r.stdout.strip() == "true"
        except (OSError, subprocess.TimeoutExpired):
            return True  # can't tell (Docker busy): don't tear down a working mesh on a hiccup

    def _supervise(self) -> None:
        """If the simulator container disappears (Docker restart, another script removing it), start a fresh one."""
        misses = 0
        while True:
            time.sleep(3)
            if self.status != "ready":
                misses = 0
                continue
            misses = 0 if self._container_running() else misses + 1
            if misses >= 2:
                self.log("The simulator container is gone. Restarting the mesh…", "error")
                misses = 0
                self.restart()

    def restart(self) -> None:
        """Replace the simulator with a fresh one. Sends wait for it; transfers resume from scratch on retry."""
        if self.status == "booting":
            return
        self.status = "booting"
        self.error = None
        old, self.sim = self.sim, None
        self.receivers = []
        # Chunks the old destination nodes held are lost, so every unfinished transfer must resend everything.
        for t in self.transfers.values():
            if not t.delivered:
                t.received.clear()
        threading.Thread(target=self._boot, args=(old,), name="mesh-boot", daemon=True).start()

    def _boot(self, old_sim: Any = None) -> None:
        try:
            if old_sim is not None:
                from lib.interface_close import close_interface
                for n in old_sim.nodes:
                    try:
                        close_interface(n.iface)
                    except Exception:
                        pass
                    n.iface = None
            self.log("Starting Docker container 'Meshtastic' with 6 simulated nodes...")
            os.system("docker rm -f Meshtastic > /dev/null 2>&1")
            from lib.interactive import InteractiveSim, HW_ID_OFFSET
            from lib.blob import BlobReceiver
            from pubsub import pub
            import matplotlib
            import lib.gui

            # lib/gui.py forces the Tk backend, which crashes macOS when a figure is built off the main thread.
            matplotlib.use("Agg", force=True)
            lib.gui.move_figure = lambda *a, **k: None

            class SimArgs:
                nrNodes = NR_NODES
                docker = True
                program = str(SIM_DIR)
                from_file = False
                forward = False
                collisions = False
                script = False
                verbose = False

            self._offset = HW_ID_OFFSET
            self.sim = InteractiveSim(SimArgs())
            time.sleep(SETTLE_SECONDS)
            self.receivers = [
                BlobReceiver(
                    n, HW_ID_OFFSET,
                    on_complete=lambda kind, data, sender: None,
                    log=self.log,
                    on_chunk=lambda sender, tid, idx, total, kind, n=n: self._on_chunk(n, sender, tid, idx),
                    on_transfer=lambda sender, tid, kind, data, n=n: self._on_transfer(n, sender, tid, kind, data),
                    on_bad_crc=lambda sender, tid: self._on_bad_crc(sender, tid),
                )
                for n in range(NR_NODES)
            ]
            pub.subscribe(self._on_air, "meshtastic.receive.simulator")
            self.status = "ready"
            self.log("Simulator ready: 6 nodes running in Docker, receivers attached", "ok")
        except BaseException as ex:  # InteractiveSim calls sys.exit on failure
            self.status = "error"
            self.error = repr(ex)
            self.log(f"Simulator failed to start: {ex!r}", "error")

    # ------------------------------------------------------------------ simulator callbacks (library threads)

    def _on_air(self, interface, packet) -> None:
        """Every packet a simulated node puts on the radio. Used for the log and to learn which nodes relayed."""
        try:
            sim = packet["decoded"]["simulator"]
            src = interface.myInfo.my_node_num - self._offset
            dst = packet.get("to", 0) - self._offset
            port = sim.get("portnum")
            data = base64.b64decode(sim.get("data", ""))
            what = "ACK/routing" if port == "ROUTING_APP" else port
            self.log(f"AIR  Node {src} transmits {what} ({len(data)} bytes) addressed to Node {dst}, "
                     f"hop limit {packet.get('hopLimit', '?')}", "tx")
            if port == "PRIVATE_APP" and len(data) >= HEADER.size:
                tid = HEADER.unpack(data[:HEADER.size])[0]
                origin = packet.get("from", 0) - self._offset
                t = self._by_key.get((origin, tid))
                if t and src not in (t.from_node, t.to_node) and src not in t.relays:
                    t.relays.append(src)
        except Exception as ex:
            self.log(f"AIR  (unparsed packet: {ex!r})", "tx")

    def _on_chunk(self, node: int, sender: int, tid: int, index: int) -> None:
        t = self._by_key.get((sender, tid))
        if t is None or t.to_node != node or t.delivered:
            return
        t.received.add(index)
        self.on_progress(t)

    def _on_transfer(self, node: int, sender: int, tid: int, kind: int, data: bytes) -> None:
        t = self._by_key.get((sender, tid))
        if t is not None and t.to_node == node and not t.delivered:
            t.delivered = True
            t.done.set()
        if t is not None:
            self.on_delivered(t, node, data)
        else:
            self._unsolicited(node, sender, kind, data)

    def _unsolicited(self, node: int, sender: int, kind: int, data: bytes) -> None:
        """A transfer we have no record of (e.g. sent by another script): still surface it as incoming."""
        self.log(f"Node {node} received a transfer from Node {sender} that this server did not start", "info")

    def _on_bad_crc(self, sender: int, tid: int) -> None:
        t = self._by_key.get((sender, tid))
        if t is not None:
            t.received.clear()  # the next round resends everything
            self.log(f"Transfer {tid}: CRC mismatch, will resend all {len(t.chunks)} chunks", "error")

    # ------------------------------------------------------------------ sending

    def enqueue(self, t: Transfer) -> None:
        self.transfers[t.file_id] = t
        self._by_key[(t.from_node, t.tid)] = t
        self._sendq.put(t)

    def requeue(self, t: Transfer) -> None:
        """Retry: resend only the chunks that never arrived."""
        t.done.clear()
        t.delivered = False
        self._sendq.put(t)

    def _sender_loop(self) -> None:
        """One worker sends transfers one after another so chunks from different messages don't collide."""
        while True:
            t = self._sendq.get()
            try:
                self._run_transfer(t)
            except Exception as ex:
                logger.exception("transfer %s crashed", t.file_id)
                self.log(f"{t.file_id} failed: {ex!r}", "error")
                self.on_failed(t, "Internal error while sending")

    def _wait_ready(self, seconds: float = 180) -> bool:
        deadline = time.time() + seconds
        while self.status == "booting" and time.time() < deadline:
            time.sleep(0.5)
        return self.status == "ready"

    def _run_transfer(self, t: Transfer) -> None:
        from meshtastic import portnums_pb2

        if not self._wait_ready():
            self.on_failed(t, "The mesh simulator isn’t running. Wait for it to restart, then tap Retry.")
            return
        sim = self.sim
        iface = sim.get_node_iface_by_id(t.from_node)
        total = len(t.chunks)
        t.started_at = time.time()
        self.on_progress(t)
        self.log(f"{t.file_id}: Node {t.from_node} starts sending {t.file_type} to Node {t.to_node} "
                 f"({len(t.data)} bytes in {total} chunks)", "tx")
        for rnd in range(1, MAX_ROUNDS + 1):
            missing = [i for i in range(total) if i not in t.received]
            if not missing:
                break
            if rnd > 1:
                self.log(f"{t.file_id}: round {rnd}, resending {len(missing)} missing chunk(s): "
                         f"{[i + 1 for i in missing][:12]}{'…' if len(missing) > 12 else ''}", "info")
            for i in missing:
                if t.done.is_set():
                    break
                self.log(f"{t.file_id}: handing chunk {i + 1}/{total} to Node {t.from_node}'s radio "
                         f"({len(t.chunks[i])} B incl. header, PRIVATE_APP, wantAck)", "tx")
                try:
                    iface.sendData(t.chunks[i], destinationId=sim.node_id_to_hw_id(t.to_node),
                                   portNum=portnums_pb2.PortNum.PRIVATE_APP, wantAck=True)
                except Exception as ex:
                    # The node's connection dropped: the simulator is probably gone. Check now instead of waiting.
                    self.log(f"{t.file_id}: lost the connection to Node {t.from_node} ({ex})", "error")
                    if not self._container_running():
                        self.log("The simulator container is gone. Restarting the mesh…", "error")
                        self.restart()
                    self.on_failed(t, "Lost the connection to the mesh simulator. It is restarting: tap Retry in a minute.")
                    return
                t.sends += 1
                if rnd > 1:
                    t.resends += 1
                time.sleep(CHUNK_DELAY)
            if t.done.wait(ROUND_WAIT):
                break
        if t.done.is_set():
            self.log(f"{t.file_id}: all {total} chunks acknowledged by Node {t.to_node}", "ok")
            return
        got = len(t.received)
        reason = f"Only {got} of {total} packets acknowledged after {MAX_ROUNDS} attempts"
        self.log(f"{t.file_id}: {reason}", "error")
        self.on_failed(t, reason)

    def _queue_watchdog(self) -> None:
        """Restore each node's client-side TX-queue credit.

        The meshtastic client claims one slot per packet it hands a node and only gets it back when the node reports
        queue status. The simulator forwards packets the node never reports on, so the credit leaks until it hits 0
        and the single callback thread blocks forever ("Waiting for free space in TX Queue"). The counter is only
        client-side flow control, so top it up whenever the node has nothing queued.
        """
        while True:
            time.sleep(0.25)
            if self.status != "ready":
                continue
            for n in self.sim.nodes:
                q = getattr(n.iface, "queueStatus", None)
                if q is not None and q.free < q.maxlen and not n.iface.queue:
                    if q.free <= 2:
                        self.log(f"Node {n.nodeid}: TX queue credit was down to {q.free}/{q.maxlen}, restored")
                    q.free = q.maxlen

    # ------------------------------------------------------------------ content on disk

    def save_content(self, file_id: str, data: bytes, mime: str) -> Path:
        path = self.content_dir / f"{file_id}.bin"
        path.write_bytes(data)
        self.contents[file_id] = (path, mime)
        return path

    def route_for(self, t: Transfer) -> list[int]:
        """Origin, then the nodes heard relaying this transfer (first heard first), then the destination."""
        return [t.from_node, *t.relays, t.to_node]


host = MeshHost()
