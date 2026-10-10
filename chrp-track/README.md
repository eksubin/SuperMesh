# CHRP Track

iPhone-styled front end and FastAPI bridge for the CHRP off-grid mesh
(Hackathon Track 1). Users send files and text messages, watch ACK-based
progress while they're in flight, and review the route each finished
transmission took.

```
frontend/   React + TypeScript (Vite). Runs in Safari, installs to the Home Screen.
backend/    FastAPI. REST for uploads/messages/retry, WebSocket for live updates.
            Mock mesh included so the UI works before the real pipeline exists.
CONTRACT.md Event shape + API the UI and the mesh pipeline agree on (draft).
```

## Run it

**Docker (one command)**

```bash
docker compose up --build
```

Open `http://<your-computer-ip>:8080` on the iPhone (same Wi-Fi), then
Share → Add to Home Screen for a full-screen app.

**Local dev (hot reload)**

```bash
# terminal 1
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# terminal 2
cd frontend
npm install
npm run dev          # http://localhost:5173, or http://<ip>:5173 on the phone
```

Vite forwards `/api` and `/ws` to `localhost:8000`.

## Run it on the real mesh simulator (macOS, no Node needed)

The backend can drive the Meshtasticator Docker simulator in `../Meshtasticator` instead of the mock. Docker Desktop
must be running. Build the UI once (this uses a throwaway Node container, nothing is installed on your machine), then
start the backend, which also serves the built UI:

```bash
# once, and again after changing anything in frontend/
docker run --rm -v "$PWD/frontend":/app -w /app node:20-slim sh -c "npm ci && npm run build"

# from the SmartMesh folder
source .venv/bin/activate
pip install -r chrp-track/backend/requirements.txt
cd chrp-track/backend
MOCK_MESH=0 python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000. The simulator takes about 30 seconds to boot; the Main screen says so.

What the phone UI does on the real mesh:

- **Nodes:** pick which node this phone is and which it sends to. Swap them to read what arrived. Sent items show for the
  sender, received items for the receiver.
- **Text, images, voice:** text box, file picker, and a voice recorder (Opus at 6 kbit/s, up to 8 s). Images are shrunk to
  a small JPEG before sending (`IMAGE_MAX_SIDE`, default 96 px). Anything over `MAX_MESH_BYTES` (12 KB) fails with a reason.
- **Honest progress:** a packet counts as ACKed when it reaches the destination node. Missing packets are resent for up to
  4 rounds; "Retry" resends only what never arrived. Delivered means every packet arrived and the CRC matches.
- **Details:** shows the image, voice player or text that was sent or received, and the route taken (the nodes heard relaying).
- **Log tab:** live event log of chunks, radio transmissions, relays, ACKs and the rebuild.

The microphone needs `localhost` or HTTPS, so voice recording does not work from a phone over plain `http://<ip>`.

## Screens

| Screen | What it shows |
|---|---|
| **Main** | Round "Tap to send a file" dial (type detected automatically), transparent message box, and "Sending now" with a progress ring per item plus total time left and queue count. |
| **History** | Delivered and failed transmissions, filter by Sent / Received / Failed. Received items have outline icons. Failed sends show **Retry**. Tap a row for details. |
| **Transmission details** | Opened from History only. Task ID, delivered/failed ring, packets ACKed, preset, chunk size, the route taken and per-hop ACK and loss. |

Honesty rules built into the UI:

- In-flight rings cap at 99%. Only `status: "delivered"` shows Delivered.
- A missing ETR shows "Estimating…", never a guess.
- If the WebSocket drops, a banner says so until it reconnects.

## Connecting the real mesh pipeline

The mock (`backend/app/mock.py`) runs while `MOCK_MESH=1`. To plug in the real
compression → chunking → mesh simulator:

1. Implement the two empty functions in **`backend/app/pipeline.py`**:
   `submit_transmission(job)` and `retry_transmission(job)`.
2. Report every update with `await publish_event(event)` from `app/bus.py`
   (every 250–500 ms), or POST the same JSON to `/internal/events` if the
   pipeline runs as its own service.
3. Set `MOCK_MESH=0`.

Until then, with `MOCK_MESH=0` every send fails honestly with
"Mesh pipeline not connected yet".

The front-end fetch points are in `frontend/src/api/client.ts` (REST) and
`frontend/src/api/socket.ts` (live updates). Types live in
`frontend/src/api/types.ts` and `backend/app/models.py`. Change both together
with `CONTRACT.md`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MOCK_MESH` | `1` | Use the built-in mock mesh. `0` runs the Meshtasticator simulator |
| `MESH_LOCAL_NODE` / `MESH_PEER_NODE` | `0` / `5` | Default sender and destination node |
| `MESH_CHUNK_DELAY` | `1.5` | Seconds between packets (lower floods the simulated radios) |
| `IMAGE_MAX_SIDE` / `IMAGE_QUALITY` | `96` / `40` | Image shrink box (px) and JPEG quality |
| `MAX_MESH_BYTES` | 12 KB | Largest payload accepted after compression |
| `UPLOAD_DIR` | `./data/uploads` | Where uploaded files are saved for the pipeline |
| `MAX_UPLOAD_BYTES` | 10 MB | Upload size limit |
| `INTERNAL_TOKEN` | unset | If set, required as `X-Internal-Token` on `/internal/events` |
| `CORS_ORIGINS` | `*` | Comma-separated origins, if the UI is served elsewhere |
| `LOCAL_NODE` | `Node 04` | Mock only: this phone's node name |
| `MOCK_FAIL_RATE` | `0.08` | Mock only: chance a send fails. File names containing `fail` always fail. |
| `VITE_API_BASE` / `VITE_WS_URL` | same origin | Front end: point at a backend on another host |

## Known gaps

- History is kept in memory and resets when the backend restarts.
- Light theme only, matching the design palette.
- Received text messages appear in History. There's no conversation view yet.
