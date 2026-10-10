"""Compress a recording with EnCodec and recover it as a mono WAV file."""

import argparse
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input WAV recording")
    parser.add_argument("--bitrate", type=float, default=6,
                        choices=[1.5, 3, 6, 12, 24], help="Target kbps (default: 6)")
    parser.add_argument("--force", action="store_true", help="Replace existing outputs")
    args = parser.parse_args()
    source = args.input.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Recording not found: {source}")
    compressed_path = source.with_name(source.stem + "_compressed.ecdc")
    recovered_path = source.with_name(source.stem + "_recovered.wav")
    for path in (compressed_path, recovered_path):
        if path == source:
            parser.error("Output would replace the input recording")
        if path.exists() and not args.force:
            parser.error(f"Output already exists: {path}. Use --force to replace it.")

    try:
        import soundfile as sf
        import torch
        from encodec import EncodecModel
        from encodec.compress import compress_to_file, decompress_from_file
        from encodec.utils import convert_audio
    except ImportError as exc:
        parser.exit(1, f"Missing dependency: {exc}\n"
                    "Install: python -m pip install torch torchaudio encodec soundfile\n")

    samples, sample_rate = sf.read(source, dtype="float32", always_2d=True)
    if samples.shape[0] == 0:
        parser.error("The recording is empty")
    waveform = torch.from_numpy(samples.T.copy())
    if not torch.isfinite(waveform).all():
        parser.error("The recording contains invalid audio samples")

    print("Device: CPU")
    print("Loading pretrained EnCodec 24 kHz mono model (first run downloads weights)...")
    model = EncodecModel.encodec_model_24khz().cpu().eval()
    model.set_target_bandwidth(args.bitrate)
    waveform = convert_audio(waveform, sample_rate, model.sample_rate, model.channels)
    duration = waveform.shape[-1] / model.sample_rate

    start = time.perf_counter()
    with torch.no_grad(), compressed_path.open("wb") as stream:
        compress_to_file(model, waveform, stream, use_lm=False)
    print(f"Compression finished in {time.perf_counter() - start:.2f} seconds")

    # Decode the saved file to verify the actual on-disk round trip.
    start = time.perf_counter()
    with torch.no_grad(), compressed_path.open("rb") as stream:
        recovered, output_rate = decompress_from_file(stream, device="cpu")
    if recovered.shape != waveform.shape:
        raise RuntimeError("Recovered audio dimensions do not match the converted input")
    if not torch.isfinite(recovered).all():
        raise RuntimeError("Decoded audio contains invalid samples")
    peak = recovered.abs().max().item()
    if peak > 1:
        recovered = recovered / peak
        print("Recovered audio volume reduced slightly to prevent clipping")
    sf.write(recovered_path, recovered.cpu().numpy().T, output_rate, subtype="PCM_16")
    print(f"Recovery finished in {time.perf_counter() - start:.2f} seconds")

    original_size = source.stat().st_size
    compressed_size = compressed_path.stat().st_size
    print(f"Duration: {duration:.2f} seconds")
    print(f"Original file: {original_size:,} bytes")
    print(f"Compressed file: {compressed_size:,} bytes")
    print(f"Size change: {100 * (1 - compressed_size / original_size):.1f}% smaller")
    print(f"Actual file bitrate: {compressed_size * 8 / duration / 1000:.2f} kbps")
    print(f"Compressed: {compressed_path}")
    print(f"Recovered:  {recovered_path}")
    print("Lossy compression: listen to the recovered WAV to compare quality.")


if __name__ == "__main__":
    main()