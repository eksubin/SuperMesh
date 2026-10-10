"""Neural voice codec for the mesh (Meta EnCodec 24 kHz), adapted from voice_compress.py.

The sender turns a recording into an EnCodec bitstream (the same `.ecdc` format the standalone script writes); the
destination decodes it back into a mono WAV. Heavy imports (torch, encodec, soundfile) happen lazily so the API
still starts without them; `available()` says whether the codec can be used, and the pipeline sends the original
recording instead when it can't.
"""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
import threading
from typing import Any, Optional

BITRATES = (1.5, 3.0, 6.0, 12.0, 24.0)               # kbps, the values EnCodec's 24 kHz model supports
DEFAULT_BITRATE = float(os.getenv("VOICE_BITRATE", "6"))

_lock = threading.Lock()
_model: Any = None
_state = "idle"  # idle | loading | ready | unavailable
_error: Optional[str] = None


def status() -> dict:
    return {"state": _state, "error": _error}


def _load():
    """Load (and cache) the pretrained model on the CPU, as voice_compress.py does. Downloads weights on first use."""
    global _model, _state, _error
    with _lock:
        if _model is not None:
            return _model
        _state = "loading"
        try:
            from encodec import EncodecModel

            _model = EncodecModel.encodec_model_24khz().cpu().eval()
        except Exception as exc:  # missing packages, no weights and no network, ...
            _state, _error = "unavailable", f"{type(exc).__name__}: {exc}"
            raise
        _state, _error = "ready", None
        return _model


def warm_up() -> bool:
    """Load the model now so the first recording isn't slow. Returns whether the codec is usable."""
    try:
        _load()
        return True
    except Exception:
        return False


def available() -> bool:
    return _state == "ready" or (_state == "idle" and warm_up())


def _read_audio(raw: bytes):
    """Decode a recording into (samples [frames, channels] float32, sample rate)."""
    import soundfile as sf

    try:
        return sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    except Exception as first:
        # libsndfile can't open m4a/mp4/caf containers; macOS ships a converter that can.
        if os.path.exists("/usr/bin/afconvert"):
            with tempfile.TemporaryDirectory() as tmp:
                src, dst = os.path.join(tmp, "in"), os.path.join(tmp, "out.wav")
                with open(src, "wb") as fh:
                    fh.write(raw)
                done = subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", "LEI16", src, dst],
                                      capture_output=True, timeout=30)
                if done.returncode == 0:
                    return sf.read(dst, dtype="float32", always_2d=True)
        raise ValueError("Unsupported audio format. Send a WAV, FLAC, OGG or MP3 recording.") from first


def compress(raw: bytes, bitrate: float = DEFAULT_BITRATE) -> tuple[bytes, bytes]:
    """Encode a recording. Returns (bitstream to send, 24 kHz mono WAV of the recording as the encoder heard it)."""
    import soundfile as sf
    import torch
    from encodec.compress import compress_to_file
    from encodec.utils import convert_audio

    if bitrate not in BITRATES:
        raise ValueError(f"bitrate must be one of {', '.join(str(b) for b in BITRATES)} kbps")
    samples, rate = _read_audio(raw)
    if samples.shape[0] == 0:
        raise ValueError("The recording is empty.")
    waveform = torch.from_numpy(samples.T.copy())
    if not torch.isfinite(waveform).all():
        raise ValueError("The recording contains invalid audio samples.")

    model = _load()
    with _lock, torch.no_grad():
        model.set_target_bandwidth(bitrate)
        waveform = convert_audio(waveform, rate, model.sample_rate, model.channels)
        stream = io.BytesIO()
        compress_to_file(model, waveform, stream, use_lm=False)

    source = io.BytesIO()
    sf.write(source, waveform.cpu().numpy().T, model.sample_rate, subtype="PCM_16", format="WAV")
    return stream.getvalue(), source.getvalue()


def decompress(blob: bytes) -> bytes:
    """Decode a bitstream from `compress` into WAV bytes."""
    import soundfile as sf
    import torch
    from encodec.compress import decompress_from_file

    with _lock, torch.no_grad():
        recovered, rate = decompress_from_file(io.BytesIO(blob), device="cpu")
    if not torch.isfinite(recovered).all():
        raise ValueError("Decoded audio contains invalid samples.")
    peak = recovered.abs().max().item()
    if peak > 1:  # same clipping guard as voice_compress.py
        recovered = recovered / peak
    out = io.BytesIO()
    sf.write(out, recovered.cpu().numpy().T, rate, subtype="PCM_16", format="WAV")
    return out.getvalue()
