# CHRP Track — UI ↔ Backend Integration Contract (draft v0.2)

This is the working contract between the iPhone-styled front end and the FastAPI
backend. It starts from the teammate's draft `MeshTransmissionEvent` and adds the
fields the History and Details screens need. Everything here is expected to move
during integration; change it in one place (`backend/app/models.py` +
`frontend/src/api/types.ts`) and update this file.

## Principles

1. **One shape for everything.** A transmission is the same object whether it is
   queued, in flight, delivered, failed, outgoing or incoming. The backend sends
   the *whole* object every time it changes (no partial patches), so the UI just
   replaces it by `fileId`.
2. **Honest status.** `status: "delivered"` is only sent once the destination has
   ACKed every chunk. Progress is ACKed chunks / total chunks, never bytes sent.
   The UI caps the ring at 99% until `delivered` arrives.
3. **Unknown is `null`, not a guess.** If ETR, loss or RSSI isn't known yet, send
   `null`. The UI shows "Estimating…" / "—" instead of a made-up number.
4. **Final states are sticky.** Once a transmission is `delivered` or `failed`,
   later in-flight events for it are ignored, except after an explicit retry.

## The transmission object (`MeshTransmissionEvent`)

```ts
type FileType  = 'text' | 'audio' | 'video' | 'image';
type Direction = 'outgoing' | 'incoming';
type Status    = 'queued' | 'compressing' | 'sending' | 'delivered' | 'failed';
type HopState  = 'pending' | 'active' | 'acked' | 'failed';

interface MeshTransmissionEvent {
  fileId: string;                 // e.g. "TX-8921", shown to the user as the task ID
  fileName: string;               // original file name, or "Text message"
  fileType: FileType;             // 'image' added: the judged demo sends an image
  kind: 'file' | 'message';       // 'message' = typed in the text box
  preview: string | null;         // message text (first ~140 chars) for kind 'message'
  direction: Direction;           // outgoing = sent by this phone, incoming = received
  status: Status;

  sizeBytes: number;              // original size before compression
  progressPercentage: number;     // 0.0–100.0, from ACKed chunks
  etrSeconds: number | null;      // this item's estimated time remaining
  totalQueueEtrSeconds: number | null; // filled in by the API layer, see below

  packetsAcked: number;
  packetsTotal: number | null;    // null until chunking is done
  chunkSizeBytes: number;         // ~230
  preset: string | null;          // e.g. "LoRa Fast · 22 kbps"

  createdAt: string;              // ISO 8601, UTC
  completedAt: string | null;     // set when status becomes delivered or failed
  failureReason: string | null;   // e.g. "Link timeout at hop 2"

  route: {
    hops: string[];               // FULL path, origin first, destination last,
                                  // e.g. ["Node 04", "Node 02", "Node 07", "Node 11"]
    currentHopIndex: number;      // index into hopStats of the hop being relayed
    rssiDbm: number | null;       // signal on the current hop
    packetLossRate: number | null;// loss % on the current hop
    hopStats: Array<{             // one entry per hop: hops[i] -> hops[i+1]
      from: string;
      to: string;
      state: HopState;
      packetLossRate: number | null; // % over this link, e.g. 18.5
      rssiDbm: number | null;
    }>;
  } | null;                       // null until a route is known
}
```

`totalQueueEtrSeconds` is computed by the API layer from every outgoing item that
is still in flight. The pipeline can leave it at 0. It is `null` when at least one
in-flight item has no ETR yet.

## REST endpoints (FastAPI, prefix `/api`)

| Method | Path | Body | Returns |
|---|---|---|---|
| `POST` | `/api/transmissions` | `multipart/form-data`, field `file` | `202` + transmission (status `queued`) |
| `POST` | `/api/messages` | `{ "text": "..." }` (1–1000 chars) | `202` + transmission |
| `POST` | `/api/transmissions/{fileId}/retry` | — | `202` + transmission (status `queued`) |
| `GET` | `/api/transmissions?scope=all\|active\|history` | — | transmission[] |
| `GET` | `/api/transmissions/{fileId}` | — | transmission |
| `GET` | `/api/health` | — | `{ "status": "ok", "mock": bool }` |

Errors use FastAPI's `{ "detail": "message" }`. Status codes: `413` file too large,
`415` unsupported type, `404` unknown id, `409` retry on something that isn't a
failed outgoing transmission.

## WebSocket `/ws` (server → UI)

On connect the server sends a snapshot, then one message per change (expected
every 250–500 ms per in-flight item):

```jsonc
{ "type": "snapshot", "transmissions": [ /* MeshTransmissionEvent[] */ ], "totalQueueEtrSeconds": 252 }
{ "type": "transmission", "event": { /* MeshTransmissionEvent */ }, "totalQueueEtrSeconds": 252 }
```

The UI sends the text `ping` every 20 s and gets `{ "type": "pong" }` back. It
reconnects automatically and gets a fresh snapshot.

## Where the mesh pipeline plugs in

Two ways, pick whichever suits the teammate's code:

**A. In-process (same Python app).** Implement the empty functions in
`backend/app/pipeline.py`:

- `submit_transmission(job)`: compress → chunk (~230 B) → start the mesh simulator.
- `retry_transmission(job)`: resend only the chunks that never got ACKed.

For each progress update call `await publish_event(event)` from
`backend/app/bus.py`. Set `MOCK_MESH=0` to switch off the built-in mock.

**B. Separate service.** POST each event as JSON to
`POST /internal/events` (same shape as above). If `INTERNAL_TOKEN` is set, send it
in the `X-Internal-Token` header. Received files are just events with
`direction: "incoming"`.

## Open questions for integration

- Does the simulator know per-hop loss in real time, or only after delivery?
  (The UI only shows per-hop details after completion, so either works.)
- Should a retry resend only missing chunks (preferred) or start over?
- Will received text messages need their own screen later? Today they appear in
  History as received items.
