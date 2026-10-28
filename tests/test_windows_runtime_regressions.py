"""Exercise Windows handle/encoding constraints even when these tests run on POSIX."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from cassound.runtime import media
from cassound.domain.errors import IntegrityError


def capture_mappings(monkeypatch):
    original = np.lib.format.open_memmap
    owners = []

    def opened(*args, **kwargs):
        array = original(*args, **kwargs)
        # Deliberately retain array owners: publication cannot rely on CPython
        # refcounts, debugger references or garbage collection closing handles.
        owners.append(array)
        return array

    monkeypatch.setattr(np.lib.format, "open_memmap", opened)
    return owners


def test_cache_publication_releases_every_mapping(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    x = np.column_stack((np.arange(17001) % 1000, -(np.arange(17001) % 999))).astype("int16")
    sf.write(source, x, 48000, subtype="PCM_16")
    owners = capture_mappings(monkeypatch)
    original = os.rename
    published = []

    def windows_rename(src, dst):
        if Path(src).name == "cache-entry":
            closed = [owner._mmap.closed for owner in owners]
            assert closed and all(closed)
            published.append(Path(dst))
        return original(src, dst)

    monkeypatch.setattr(os, "rename", windows_rename)
    root = tmp_path / "session"
    root.mkdir()
    asset = media.import_audio(source, None, root, root / "stage", {}, lambda *_: None)
    assert len(owners) > 2 and len(published) == 1
    closed = [owner._mmap.closed for owner in owners]
    assert all(closed)
    cached = np.load(published[0] / "samples.npy", allow_pickle=False)
    assert np.array_equal(cached, x)
    assert asset["frames"] == len(x)


def test_failed_import_releases_staging_mappings(tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    sf.write(source, np.zeros((1000, 1)), 8000, subtype="PCM_16")
    owners = capture_mappings(monkeypatch)

    def failed_read(*args):
        raise IntegrityError("controlled decoder failure")

    monkeypatch.setattr(media, "native_block", failed_read)
    root = tmp_path / "session"
    root.mkdir()
    stage = root / "stage"
    with pytest.raises(IntegrityError, match="controlled decoder failure"):
        media.import_audio(source, None, root, stage, {}, lambda *_: None)
    closed = [owner._mmap.closed for owner in owners]
    assert closed and all(closed)
    shutil.rmtree(stage)
    assert not any((root / "cache").iterdir())


def test_worker_protocol_overrides_non_utf8_stdio(tmp_path):
    source = tmp_path / "تست 🎵 source.wav"
    sf.write(source, np.zeros((1000, 1)), 8000, subtype="PCM_16")
    root = tmp_path / "جلسه"
    stage = root / "staging" / "req-encoding"
    stage.mkdir(parents=True)
    request = {
        "command": {"protocol_version": 1, "command": "media.import", "payload": {"path": str(source)}},
        "workdir": str(root),
        "stage": str(stage),
        "limits": {},
    }
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONUTF8"] = "0"
    result = subprocess.run(
        [sys.executable, "-m", "cassound.runtime.worker"],
        input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf8"),
        capture_output=True,
        env=env,
        timeout=30,
    )
    messages = [json.loads(line) for line in result.stdout.decode("utf8").splitlines()]
    assert result.returncode == 0, (messages, result.stderr.decode("utf8", errors="replace"))
    assert messages[-1]["result"]["locator"] == str(source.resolve())
