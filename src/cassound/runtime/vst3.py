"""Digest-pinned, isolated native effects jobs. No plugin code enters the GUI/Python process."""

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import numpy as np
from .cache import digest_file, cache_key, atomic_json
from .locking import FileLock
from ..domain.errors import ValidationError, BackendUnavailable, CASSoundError, IntegrityError


def host_path():
    configured = os.environ.get("CASSOUND_VST3_HOST")
    bundled = (
        (Path(sys._MEIPASS) / "cassound" if hasattr(sys, "_MEIPASS") else Path(__file__).resolve().parents[1])
        / "native"
        / ("cassound-vst3.exe" if sys.platform == "win32" else "cassound-vst3")
    )
    p = Path(configured).expanduser().resolve() if configured else bundled
    if not p.is_file():
        raise BackendUnavailable(
            "Build the CASSound native VST3 host and set CASSOUND_VST3_HOST, or install a bundle containing it."
        )
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise BackendUnavailable("Native VST3 host supports x64 only.")
    return p


def plugin_identity(path):
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() != ".vst3" or not path.exists():
        raise ValidationError("Select an explicit .vst3 effect file or bundle.")
    if path.is_file():
        files = [path]
    else:
        files = sorted(p for p in path.rglob("*") if p.is_file())
        if len(files) > 4096 or not files:
            raise ValidationError("Invalid/oversized VST3 bundle.")
    if any(p.is_symlink() for p in files):
        raise ValidationError("Symlinked plugin contents are unsupported.")
    if sum(p.stat().st_size for p in files) > 2 * 1024**3:
        raise ValidationError("Plugin bundle exceeds 2 GiB scan bound.")
    return path, cache_key(
        [(str(p.relative_to(path)) if path.is_dir() else p.name, digest_file(p)) for p in files]
    )


def status(root):
    path = Path(root) / "plugins.json"
    return json.loads(path.read_text()) if path.is_file() else {"version": 1, "plugins": {}}


def update(root, digest, value):
    root = Path(root)
    lock = FileLock(root / "plugins.lock").acquire(timeout=10)
    try:
        data = status(root)
        data["plugins"][digest] = value
        atomic_json(root / "plugins.json", data)
    finally:
        lock.release()


def request(req, folder, timeout=30):
    executable = host_path()
    folder = Path(folder)
    with tempfile.TemporaryDirectory(prefix="vst3-", dir=folder) as d:
        directory = Path(d)
        incoming = directory / "request.json"
        outgoing = directory / "response.json"
        incoming.write_text(json.dumps(req, allow_nan=False), encoding="utf8")
        # Children inherit the worker's process group. Cancellation terminates the entire group.
        process = subprocess.Popen(
            [str(executable), str(incoming), str(outgoing)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise CASSoundError("VST3 process timed out.", code="plugin_timeout")
        if not outgoing.is_file():
            raise CASSoundError(
                "VST3 process crashed; no processed audio was committed.", code="plugin_crash"
            )
        if outgoing.stat().st_size > 32 * 1024**2:
            raise ValidationError("Plugin response exceeds bound.")
        data = json.loads(outgoing.read_text(encoding="utf8"))
        if code or "error" in data:
            raise CASSoundError(data.get("error", "VST3 host failed."), code="plugin_failed")
        return data["result"]


def scan(payload, root, stage, limits, progress):
    path, digest = plugin_identity(payload["plugin"])
    previous = status(root)["plugins"].get(digest, {})
    if previous.get("quarantined") and not payload.get("retry"):
        raise ValidationError("Plugin is quarantined; explicitly retry after correcting the cause.")
    try:
        progress("plugin.scan", 0)
        data = request({"plugin": str(path), "mode": "scan"}, stage, timeout=payload.get("timeout", 15))
        record = {"path": str(path), "digest": digest, "quarantined": False, **data}
        update(root, digest, record)
        return record
    except Exception as exc:
        update(
            root,
            digest,
            {"path": str(path), "digest": digest, "quarantined": True, "reason": str(exc)[:1024]},
        )
        raise


def process(x, fs, p, root, stage):
    if type(p.get("bypass", False)) is not bool:
        raise ValidationError("Plugin bypass must be Boolean.")
    if p.get("bypass"):
        return x.copy(), {"plugin": "bypassed", "processed": False}
    path, digest = plugin_identity(p.get("plugin", ""))
    record = status(root)["plugins"].get(digest)
    if not record or record.get("quarantined"):
        raise ValidationError("Scan and approve this exact plugin build before processing.")
    if p.get("plugin_digest") != digest:
        raise IntegrityError("Effect recipe must pin the scanned plugin_digest.")
    if x.shape[1] not in (1, 2):
        raise ValidationError("VST3 supports mono/stereo main buses only; no sidechains/events/instruments.")
    if p.get("class_id") not in {v["class_id"] for v in record.get("classes", [])}:
        raise ValidationError("Choose a scanned effect class ID.")
    parameters = p.get("parameters", {})
    if not isinstance(parameters, dict) or len(parameters) > 4096:
        raise ValidationError("Invalid normalized parameter dictionary.")
    from ..engine.common import number

    if "tail_seconds" in p:
        number(p, "tail_seconds", 0, 0, 10)
    with tempfile.TemporaryDirectory(prefix="plugin-audio-", dir=stage) as d:
        folder = Path(d)
        source = folder / "input.f32"
        destination = folder / "output.f32"
        np.asarray(x, dtype="<f4").tofile(source)
        req = {
            "mode": "process",
            "plugin": str(path),
            "class_id": p["class_id"],
            "parameters": parameters,
            "state": p.get("state"),
            "channels": x.shape[1],
            "frames": len(x),
            "sample_rate": fs,
            "input": str(source),
            "output": str(destination),
        }
        if "tail_seconds" in p:
            req["tail_seconds"] = p["tail_seconds"]
        try:
            result = request(req, folder, timeout=60)
            expected = result["frames"] * x.shape[1]
            if (
                expected > 8_000_000
                or not destination.is_file()
                or destination.stat().st_size != expected * 4
            ):
                raise IntegrityError("Plugin output length mismatch.")
            out = np.fromfile(destination, dtype="<f4").reshape(-1, x.shape[1]).astype(float)
            if not np.isfinite(out).all():
                raise IntegrityError("Nonfinite plugin output.")
            result.update(plugin_digest=digest, processed=True)
            return out, result
        except Exception as exc:
            update(root, digest, {**record, "quarantined": True, "reason": str(exc)[:1024]})
            raise
