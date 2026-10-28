"""Explicit digest-pinned downloads; no upstream Python execution."""

import hashlib
import json
import urllib.request
import zipfile
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

CATALOG = [
    {
        "id": "gtcrn",
        "repo": "andrelgomes/gtcrn-stream",
        "revision": "af53b933a7c79c34d488b384c9a8744f1b734395",
        "file": "gtcrn_simple.onnx",
        "sha256": "b4718df6228e7bdf1a8a435cf98f838636eb2fd331acabf86ba87c5192ebcb87",
        "size": 535190,
        "license": "MIT",
    },
    {
        "id": "audioseal_generator",
        "repo": "synath/audioseal-onnx",
        "revision": "aacb1186bedc8118960e5ce8a7849861bf9a4c6c",
        "file": "audioseal_generator_16bits.onnx",
        "sha256": "3b3211e0fb4cf513c9da0f4e45d523cf434426902ea9a78a16c68b33d224cf32",
        "size": 58953817,
        "license": "MIT",
    },
    {
        "id": "audioseal_detector",
        "repo": "synath/audioseal-onnx",
        "revision": "aacb1186bedc8118960e5ce8a7849861bf9a4c6c",
        "file": "audioseal_detector_16bits.onnx",
        "sha256": "3ff3991c4bf4426c5aed8b122610833cb4cd7e02535d6edeae6380199f809ca2",
        "size": 34759744,
        "license": "MIT",
    },
    {
        "id": "deepfake",
        "repo": "pranjal-pravesh/wav2vec2-large-xlsr-deepfake-audio-classification",
        "revision": "4b1c4a294ab68ab444dac038fec8e8da2cc1982f",
        "file": "model_int8.onnx",
        "sha256": "7af799e8443c3d030bb3ef644ffc6e74f5b6fddcd6a4286ee9abe9e828cb762e",
        "size": 355336130,
        "license": "Apache-2.0",
    },
]


def fetch(entry, out):
    target = out / (entry["id"] + ".onnx")
    url = f"https://huggingface.co/{entry['repo']}/resolve/{entry['revision']}/{entry['file']}"
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
        print("Downloading " + entry["id"], flush=True)
        temp = target.with_suffix(".partial")
        with urllib.request.urlopen(url, timeout=30) as response, temp.open("wb") as handle:
            digest = hashlib.sha256()
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > entry["size"]:
                    raise ValueError("Oversize weight")
                digest.update(block)
                handle.write(block)
        if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError("Weight digest mismatch")
        temp.replace(target)
    notices = Path(__file__).resolve().parents[1] / "src/cassound/resources/model-notices" / entry["id"]
    notices.mkdir(parents=True, exist_ok=True)
    for name in ["README.md", "LICENSE"]:
        path = notices / name
        if path.exists():
            continue
        url = f"https://huggingface.co/{entry['repo']}/resolve/{entry['revision']}/{name}"
        if name == "LICENSE" and entry["id"] == "gtcrn":
            url = "https://raw.githubusercontent.com/Xiaobin-Rong/gtcrn/main/LICENSE"
        if name == "LICENSE" and entry["id"] == "deepfake":
            url = "https://www.apache.org/licenses/LICENSE-2.0.txt"
        with urllib.request.urlopen(url, timeout=30) as response:
            path.write_bytes(response.read(100000))
    with zipfile.ZipFile(out / (entry["id"] + ".casp"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({**entry, "version": 1, "file": "model.onnx"}, indent=2))
        z.write(target, "model.onnx")
        for name in ["README.md", "LICENSE"]:
            z.write(notices / name, name)
    print("Verified " + entry["id"], flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("output", type=Path)
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    (Path(__file__).resolve().parents[1] / "src/cassound/resources/model-catalog.json").write_text(
        json.dumps(CATALOG, indent=2)
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda e: fetch(e, a.output), CATALOG))


if __name__ == "__main__":
    main()
