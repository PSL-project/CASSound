"""Explicitly trusted installed Python entry points; project files cannot enable code."""

import importlib.metadata
import json
from pathlib import Path
from .cache import atomic_json
from ..domain.errors import ValidationError


def available():
    return {entry.name: entry for entry in importlib.metadata.entry_points(group="cassound.analysis")}


def enable(name, root):
    if name not in available():
        raise ValidationError("No installed cassound.analysis entry point with that name.")
    path = Path(root) / "extensions.json"
    names = json.loads(path.read_text()) if path.exists() else []
    atomic_json(path, sorted(set(names + [name])))
    return {
        "enabled": name,
        "note": "Trusted Python executes with your account permissions. Restart CASSound to load.",
    }


def load(root):
    from ..engine import registry

    path = Path(root) / "extensions.json"
    if not path.exists():
        return
    if path.stat().st_size > 8192:
        raise ValidationError("Extension allowlist too large.")
    names = json.loads(path.read_text())
    if not isinstance(names, list) or len(names) > 64 or any(not isinstance(n, str) for n in names):
        raise ValidationError("Invalid extension allowlist.")
    entries = available()
    for name in names:
        if name in registry.LOADED_EXTENSIONS:
            continue
        if name not in entries:
            continue

        def register(task, function, parameters=(), kind="line"):
            if not task.startswith(name + "."):
                raise ValidationError("Extension task must use its entry point name prefix.")
            registry.register(task, function, parameters, kind=kind)

        entries[name].load()(1, register)
        registry.LOADED_EXTENSIONS.add(name)
