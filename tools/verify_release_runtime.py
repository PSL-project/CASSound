"""Exercise a built CLI outside the checkout, with no source/Python dependency fallback."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("executable", type=Path)
    p.add_argument("--models", type=Path)
    a = p.parse_args()
    executable = a.executable.resolve()
    packs = a.models.resolve() if a.models else None
    with tempfile.TemporaryDirectory(prefix="cassound-packaged-") as directory:
        root = Path(directory)
        env = os.environ.copy()
        for key in ["PYTHONPATH", "VIRTUAL_ENV", "CASSOUND_VST3_HOST"]:
            env.pop(key, None)

        def call(arguments):
            completed = subprocess.run(
                [str(executable), *map(str, arguments)],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if completed.returncode:
                raise RuntimeError(
                    "Packaged command failed: "
                    + str(arguments[:2])
                    + " "
                    + completed.stderr[-1000:]
                    + " "
                    + completed.stdout[-1000:]
                )
            return completed.stdout

        key = root / "private.key"
        key.write_bytes(bytes(range(32)))
        payload = root / "payload.bin"
        payload.write_bytes("سلام packaged workflow".encode())
        wave = root / "carrier.wav"
        recovered = root / "recovered.bin"
        call(["message-encode", payload, wave, "--carrier", "qpsk", "--key-file", key])
        call(["message-decode", wave, recovered, "--carrier", "qpsk", "--key-file", key])
        assert recovered.read_bytes() == payload.read_bytes()
        work = root / "session"
        call(
            [
                "--workdir",
                work,
                "analyze",
                wave,
                "--task",
                "spectral.welch_psd",
                "--export",
                root / "spectrum.svg",
            ]
        )
        for extension in ["png", "pdf", "html", "parquet"]:
            call(
                [
                    "--workdir",
                    work,
                    "analyze",
                    wave,
                    "--task",
                    "spectral.welch_psd",
                    "--export",
                    root / ("spectrum." + extension),
                ]
            )
        if packs:
            for pack in sorted(packs.glob("*.casp")):
                call(["model-install", pack, "--workdir", work])
            for name in ["gtcrn", "deepfake", "audioseal_generator", "audioseal_detector"]:
                destination = root / (
                    name + (".wav" if name in ["gtcrn", "audioseal_generator"] else ".json")
                )
                call(
                    [
                        "model-run",
                        name,
                        wave,
                        "--output",
                        destination,
                        "--bits",
                        "0101010101010101",
                        "--workdir",
                        work,
                    ]
                )
                assert destination.is_file()
        print("Packaged encryption/recovery, reports and requested CPU models passed")


if __name__ == "__main__":
    main()
