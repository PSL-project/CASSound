"""Run real CPU model/pack checks with a synthetic control; no field-accuracy claim."""

import argparse
import json
import tempfile
from pathlib import Path
import numpy as np
from cassound.runtime.models import install
from cassound.engine.neural import run


def main():
    p = argparse.ArgumentParser()
    p.add_argument("packs", type=Path)
    p.add_argument("--output", type=Path, default=Path("docs/model-evidence.json"))
    args = p.parse_args()
    x = (
        0.1 * np.sin(2 * np.pi * 440 * np.arange(48000) / 16000)
        + np.random.default_rng(3).normal(0, 0.01, 48000)
    ).astype(np.float32)
    evidence = {}
    with tempfile.TemporaryDirectory() as root:
        for pack in sorted(args.packs.glob("*.casp")):
            install(pack, root)
        denoised, meta = run(x, 16000, "gtcrn", root)
        assert denoised.shape == x.shape and np.all(np.isfinite(denoised))
        evidence["denoise"] = meta
        bits = "0101010101010101"
        marked, meta = run(x, 16000, "audioseal_generator", root, bits)
        evidence["watermark_insert"] = meta
        _, detected = run(marked, 16000, "audioseal_detector", root)
        assert detected["candidate"] and detected["message_bits"] == bits
        evidence["watermark_roundtrip"] = detected
        _, control = run(x, 16000, "audioseal_detector", root)
        evidence["unmarked_synthetic_control"] = control
        _, meta = run(x, 16000, "deepfake", root)
        assert 0 <= meta["mean_fake_score"] <= 1
        evidence["deepfake_execution"] = meta
    evidence["limits"] = (
        "Synthetic execution evidence, not listening quality, unseen-source deepfake accuracy or population false-positive calibration."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2))
    print("Real CPU model checks passed")


if __name__ == "__main__":
    main()
