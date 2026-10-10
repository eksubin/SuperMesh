"""Neural image codec for the mesh (CompressAI bmshj2018_factorized), adapted from image_compress.py.

The sender turns a picture into a tiny entropy-coded bitstream; the destination node decodes it back into pixels.
Heavy imports (torch, compressai) happen lazily so the API still starts without them; `available()` says whether the
codec can be used and the pipeline falls back to JPEG when it can't.

Wire format (all big-endian):
    magic "N1" | quality B | original width H | original height H | latent height H | latent width H
    | number of string groups B | per group: number of strings B | per string: length H + bytes
"""
from __future__ import annotations

import io
import os
import struct
import threading
from typing import Any, Optional

from PIL import Image

MAGIC = b"N1"
HEADER = struct.Struct("!2sBHHHH")
QUALITY = int(os.getenv("NEURAL_QUALITY", "1"))      # 1 = smallest bitstream; the pretrained weights come with CompressAI
MAX_SIDE = int(os.getenv("NEURAL_MAX_SIDE", "256"))  # larger pictures are shrunk first (keeps the bitstream and compute small)
STRIDE = 16                                          # the encoder downsamples 16x, so pad up to a multiple of this

_lock = threading.Lock()
_models: dict[int, Any] = {}
_state = "idle"  # idle | loading | ready | unavailable
_error: Optional[str] = None
_device = "cpu"


def status() -> dict:
    return {"state": _state, "error": _error, "device": _device}


def _load(quality: int):
    """Load (and cache) the pretrained model. Downloads the weights on first use if they aren't cached."""
    global _state, _error, _device
    with _lock:
        if quality in _models:
            return _models[quality]
        _state = "loading"
        try:
            import torch
            from compressai.zoo import bmshj2018_factorized

            _device = "mps" if torch.backends.mps.is_available() else "cpu"
            model = bmshj2018_factorized(quality=quality, pretrained=True).to(_device).eval()
        except Exception as exc:  # missing torch/compressai, no weights and no network, ...
            _state, _error = "unavailable", f"{type(exc).__name__}: {exc}"
            raise
        _models[quality] = model
        _state, _error = "ready", None
        return model


def warm_up() -> bool:
    """Load the default model now so the first picture isn't slow. Returns whether the codec is usable."""
    try:
        _load(QUALITY)
        return True
    except Exception:
        return False


def available() -> bool:
    return _state == "ready" or (_state == "idle" and warm_up())


def _prepare(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    return img


def compress(raw: bytes, quality: int = QUALITY) -> tuple[bytes, bytes]:
    """Encode a picture. Returns (bitstream to send, PNG of the picture exactly as it was fed to the encoder)."""
    import numpy as np
    import torch
    import torch.nn.functional as F

    model = _load(quality)
    img = _prepare(Image.open(io.BytesIO(raw)))
    width, height = img.size
    x = torch.from_numpy(np.asarray(img, dtype=np.float32) / 255.0).permute(2, 0, 1).unsqueeze(0)
    pad_w, pad_h = -width % STRIDE, -height % STRIDE
    if pad_w or pad_h:
        x = F.pad(x, (0, pad_w, 0, pad_h), mode="replicate")
    with _lock, torch.no_grad():
        out = model.compress(x.to(_device))

    latent_h, latent_w = out["shape"]
    groups = out["strings"]
    parts = [HEADER.pack(MAGIC, quality, width, height, latent_h, latent_w), struct.pack("!B", len(groups))]
    for group in groups:
        parts.append(struct.pack("!B", len(group)))
        for s in group:
            if len(s) > 0xFFFF:
                raise ValueError("picture is too detailed for the mesh codec")
            parts.append(struct.pack("!H", len(s)))
            parts.append(s)
    png = io.BytesIO()
    img.save(png, format="PNG")
    return b"".join(parts), png.getvalue()


def decompress(blob: bytes) -> bytes:
    """Decode a bitstream from `compress` into PNG bytes."""
    import torch

    magic, quality, width, height, latent_h, latent_w = HEADER.unpack_from(blob, 0)
    if magic != MAGIC:
        raise ValueError("not a neural image bitstream")
    pos = HEADER.size
    (n_groups,) = struct.unpack_from("!B", blob, pos)
    pos += 1
    groups: list[list[bytes]] = []
    for _ in range(n_groups):
        (n_strings,) = struct.unpack_from("!B", blob, pos)
        pos += 1
        group = []
        for _ in range(n_strings):
            (length,) = struct.unpack_from("!H", blob, pos)
            pos += 2
            group.append(bytes(blob[pos:pos + length]))
            pos += length
        groups.append(group)

    model = _load(quality)
    with _lock, torch.no_grad():
        x_hat = model.decompress(groups, [latent_h, latent_w])["x_hat"]
    x_hat = x_hat.squeeze(0).clamp(0, 1).cpu()[:, :height, :width]
    pixels = (x_hat.permute(1, 2, 0).numpy() * 255.0 + 0.5).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(pixels, "RGB").save(out, format="PNG")
    return out.getvalue()
