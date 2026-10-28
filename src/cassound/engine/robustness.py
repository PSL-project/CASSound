"""Seeded channel experiments, including optional installed FFmpeg codec round trips."""

import time
import shutil
import subprocess
import tempfile
from pathlib import Path
import numpy as np
from scipy import signal
from scipy.io import wavfile
from . import messages
from ..domain.errors import CASSoundError


def sweep(envelope, profile="fsk", symbol_frames=128, seed=0):
    source = messages.synthesize(envelope, profile, symbol_frames)
    random = np.random.default_rng(seed)
    rms = np.sqrt(np.mean(source**2))
    transforms = [
        ("control", lambda x: x.copy()),
        ("gain_-12dB", lambda x: x * 0.2511886432),
        ("noise_20dB", lambda x: x + random.normal(0, rms / 10, len(x))),
        ("clip_0.1FS", lambda x: np.clip(x, -0.1, 0.1)),
        ("crop_10frames", lambda x: x[10:]),
        ("resample_roundtrip", lambda x: signal.resample_poly(signal.resample_poly(x, 2, 3), 3, 2)[: len(x)]),
        ("stretch_1.02", lambda x: signal.resample(x, round(len(x) * 1.02))),
    ]
    rows = []
    expected = messages.frame(envelope)

    def measure(name, y, started):
        recovered = False
        outcome = "damaged_or_absent"
        ber = None
        corrections = None
        try:
            bits, _ = messages.raw_symbols(y, profile, symbol_frames)
            if len(bits) >= len(expected):
                ber = float(np.mean(bits[: len(expected)] != expected))
            data, trace = messages.demodulate(y, profile, symbol_frames, trace=True)
            recovered = data == envelope
            corrections = trace["corrected_bit_groups"]
            outcome = "recovered" if recovered else "different_frame"
        except CASSoundError as exc:
            outcome = exc.code
        mse = float(np.mean((source - y) ** 2)) if len(source) == len(y) else None
        rows.append(
            {
                "channel": name,
                "recovered": recovered,
                "outcome": outcome,
                "ber": ber,
                "corrected_bit_groups": corrections,
                "mse": mse,
                "snr_db": float(10 * np.log10(np.mean(source**2) / mse)) if mse and mse > 0 else None,
                "seconds": time.monotonic() - started,
            }
        )

    for name, transform in transforms:
        start = time.monotonic()
        measure(name, transform(source), start)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        with tempfile.TemporaryDirectory(prefix="cassound-channel-") as directory:
            directory = Path(directory)
            original = directory / "source.wav"
            wavfile.write(original, 48000, source.astype(np.float32))
            for codec, extension, options in [
                ("flac", "flac", []),
                ("libmp3lame", "mp3", ["-b:a", "192k"]),
                ("aac", "m4a", ["-b:a", "192k"]),
                ("libopus", "ogg", ["-b:a", "128k"]),
            ]:
                start = time.monotonic()
                encoded = directory / ("encoded." + extension)
                decoded = directory / "decoded.wav"
                try:
                    subprocess.run(
                        [
                            ffmpeg,
                            "-v",
                            "error",
                            "-y",
                            "-i",
                            str(original),
                            "-c:a",
                            codec,
                            *options,
                            str(encoded),
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=30,
                    )
                    subprocess.run(
                        [ffmpeg, "-v", "error", "-y", "-i", str(encoded), "-c:a", "pcm_f32le", str(decoded)],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=30,
                    )
                    _, y = wavfile.read(decoded)
                    measure(codec, y, start)
                except (subprocess.SubprocessError, OSError):
                    rows.append(
                        {"channel": codec, "outcome": "backend_failed", "recovered": False, "ber": None}
                    )
    else:
        rows.append({"channel": "codecs", "outcome": "ffmpeg_missing", "recovered": False, "ber": None})
    control = random.normal(0, rms, len(source))
    false_positive = False
    try:
        false_positive = messages.demodulate(control, profile, symbol_frames) == envelope
    except CASSoundError:
        pass
    return {
        "profile": profile,
        "symbol_frames": symbol_frames,
        "seed": seed,
        "rows": rows,
        "unmarked_control_recovered": false_positive,
        "limits": "Single known packet and one seeded noise control; no population false-positive estimate. BER is unset when synchronization is absent.",
    }
