"""Regression coverage for DLLs loaded by absolute ctypes paths on Windows."""

from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_hook_collects_both_windows_runtime_layouts(tmp_path, monkeypatch):
    hooks = pytest.importorskip("PyInstaller.utils.hooks")
    package = tmp_path / "sklearn"
    (package / ".libs").mkdir(parents=True)
    (tmp_path / "scikit_learn.libs").mkdir()
    internal = package / ".libs/vcomp140.dll"
    external = tmp_path / "scikit_learn.libs/runtime.dll"
    load_order = tmp_path / "scikit_learn.libs/.load-order-sklearn"
    for path in [internal, external, load_order]:
        path.write_bytes(b"fixture")
    monkeypatch.setattr(hooks, "is_package", lambda _: True)
    monkeypatch.setattr(hooks, "get_all_package_paths", lambda _: [str(package)])
    monkeypatch.setattr(hooks, "get_package_paths", lambda _: (str(tmp_path), str(package)))
    monkeypatch.setattr(hooks.compat, "is_win", True)
    # Use the real PyInstaller collectors, including their destination path logic.
    result = runpy.run_path(str(ROOT / "packaging/hooks/hook-sklearn.py"))
    binaries = {(Path(src), Path(dst)) for src, dst in result["binaries"]}
    assert (internal, Path("sklearn/.libs")) in binaries
    assert (external, Path("scikit_learn.libs")) in binaries
    assert (str(load_order), "scikit_learn.libs") in result["datas"]


@pytest.mark.parametrize("missing", ["vcomp140.dll", "msvcp140.dll"])
def test_bundle_audit_rejects_missing_or_relocated_runtime(tmp_path, monkeypatch, missing):
    module = runpy.run_path(str(ROOT / "tools/verify_bundle_layout.py"))
    audit = module["verify_sklearn_runtime"]
    files = [Path("sklearn/.libs") / name for name in ["vcomp140.dll", "msvcp140.dll"]]
    monkeypatch.setitem(audit.__globals__, "distribution", lambda _: SimpleNamespace(files=files))
    for path in files:
        destination = tmp_path / "_internal" / (path.name if path.name == missing else path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fixture")
    with pytest.raises(RuntimeError, match=missing):
        audit(tmp_path)
    destination = tmp_path / "_internal/sklearn/.libs" / missing
    destination.write_bytes(b"fixture")
    audit(tmp_path)
