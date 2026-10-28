"""A real one-hour PCM input; sparse silence keeps fixture creation inexpensive."""

import argparse
import json
from pathlib import Path
import struct
import tempfile
import time
from cassound import Session, Region, AnalysisSpec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/long-file.json")
    parser.add_argument("--science", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        rate, channels, seconds = 48000, 2, 3600
        size = rate * channels * 2 * seconds
        path = root / "one-hour.wav"
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + size,
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            rate,
            rate * channels * 2,
            channels * 2,
            16,
            b"data",
            size,
        )
        with path.open("wb") as f:
            f.write(header)
            f.seek(size - 1, 1)
            f.write(b"\0")
        with Session(root / "session") as s:
            start = time.monotonic()
            job = s.import_media(path)
            asset = s.asset(job.wait(180).asset_id)
            assert asset.frames == rate * seconds
            elapsed = time.monotonic() - start
            import_metrics = dict(job.metrics)
            query = s.waveform(Region(asset.original_revision_id, 0, asset.frames, (0, 1)), pixels=1600)
            tick = time.monotonic()
            overview = query.wait(20)
            overview_seconds = time.monotonic() - tick
            assert len(overview["time"]) <= 3200
            assert all(v == [0.0, 0.0] for v in overview["maximum"])
            scientific = {}
            if args.science:
                for task in ("time.metrics", "spectral.welch_psd"):
                    stamp = time.monotonic()
                    job = s.analyze(
                        Region(asset.original_revision_id, 0, asset.frames, (0, 1)), AnalysisSpec(task)
                    )
                    result = job.wait(180)
                    scientific[task] = {
                        "seconds": time.monotonic() - stamp,
                        "worker": job.metrics,
                        "result_id": result.result_id,
                    }
            data = {
                "fixture": "one-hour sparse silence WAV; not a representative detector benchmark",
                "rate": rate,
                "channels": channels,
                "frames": asset.frames,
                "source_bytes": size + 44,
                "import_seconds": elapsed,
                "worker_metrics": import_metrics,
                "overview_seconds": overview_seconds,
                "overview_points": len(overview["time"]),
                "science": scientific,
            }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data))


if __name__ == "__main__":
    main()
