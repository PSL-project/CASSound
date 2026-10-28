"""Catalog-pinned, data-only offline packs and CPU ONNX runtime."""

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from importlib.resources import files
from ..domain.errors import ValidationError, IntegrityError, BackendUnavailable


def catalog():
    return json.loads((files("cassound") / "resources/model-catalog.json").read_text())


def descriptor(model_id):
    for item in catalog():
        if item["id"] == model_id:
            return item
    raise ValidationError("Unknown model ID.")


def install(pack, root):
    root = Path(root) / "models"
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(pack) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or set(names) != {
            "manifest.json",
            "model.onnx",
            "README.md",
            "LICENSE",
        }:
            raise ValidationError("Invalid pack paths/members.")
        if (
            sum(v.file_size for v in z.infolist()) > 501 * 1024**2
            or z.getinfo("manifest.json").file_size > 8192
        ):
            raise ValidationError("Pack expansion limit exceeded.")
        manifest = json.loads(z.read("manifest.json"))
        entry = descriptor(manifest.get("id"))
        if manifest != {**entry, "version": 1, "file": "model.onnx"}:
            raise IntegrityError("Pack differs from pinned catalog.")
        temp = Path(tempfile.mkdtemp(prefix=".install-", dir=root))
        try:
            for name in names:
                if name != "model.onnx" and z.getinfo(name).file_size > 100000:
                    raise ValidationError("Oversize notice.")
                digest = hashlib.sha256()
                size = 0
                with z.open(name) as source, (temp / name).open("wb") as target:
                    while block := source.read(1024 * 1024):
                        target.write(block)
                        digest.update(block)
                        size += len(block)
                if name == "model.onnx" and (digest.hexdigest() != entry["sha256"] or size != entry["size"]):
                    raise IntegrityError("Weight hash/size mismatch.")
            target = root / entry["id"]
            if target.exists():
                raise ValidationError("Model already installed; explicitly remove before replacing.")
            os.replace(temp, target)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
    return entry


def remove(model_id, root):
    descriptor(model_id)
    shutil.rmtree(Path(root) / "models" / model_id, ignore_errors=True)
    return {"removed": model_id}


def inventory(root):
    rows = []
    for entry in catalog():
        path = Path(root) / "models" / entry["id"] / "model.onnx"
        state = "missing"
        if path.exists():
            state = (
                "ready"
                if path.stat().st_size == entry["size"]
                and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
                else "corrupt"
            )
        rows.append({**entry, "state": state})
    return rows


def load(model_id, root):
    entry = descriptor(model_id)
    path = Path(root) / "models" / model_id / "model.onnx"
    if not path.is_file():
        raise BackendUnavailable("Install offline pack: " + model_id)
    data = path.read_bytes()
    if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise IntegrityError("Installed weight digest mismatch.")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise BackendUnavailable("Install cassound[models] for CPU inference.") from exc
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 2
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(data, sess_options=opts, providers=["CPUExecutionProvider"])
