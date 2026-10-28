import hashlib
import json
from pathlib import Path
import shutil
import os
from ..domain.errors import ResourceLimit, ValidationError


def digest_file(path, progress=lambda *_: None):
    hasher = hashlib.sha256()
    size = Path(path).stat().st_size
    done = 0
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            hasher.update(block)
            done += len(block)
            progress("hash", done / max(1, size))
    return hasher.hexdigest()


class Cache:
    def __init__(self, root, quota=20 * 1024**3):
        self.root = Path(root).resolve()
        self.quota = quota
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValidationError("Invalid cache identity.")
        return self.root / key

    def usage(self):
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())

    def admit(self, size):
        if size < 0 or self.usage() + size > self.quota:
            raise ResourceLimit("Cache quota exceeded. Increase the disk budget or remove unused sessions.")
        if shutil.disk_usage(self.root).free < size + 64 * 1024**2:
            raise ResourceLimit("Insufficient free disk space for decoded audio.")

    def prune(self, pinned=(), target_bytes=0):
        removed = 0
        candidates = sorted(
            (p for p in self.root.iterdir() if p.is_dir() and p.name not in pinned),
            key=lambda p: p.stat().st_mtime,
        )
        for p in candidates:
            if self.usage() <= target_bytes:
                break
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            shutil.rmtree(p)
            removed += size
        return removed


def cache_key(identity):
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
