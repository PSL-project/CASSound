"""Build failures must preserve the real diagnostic, not only a wrapper traceback."""

import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest


def helper():
    path = Path(__file__).resolve().parents[1] / "tools/build_native.py"
    spec = importlib.util.spec_from_file_location("cassound_build_native", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_command_preserves_compiler_failure(tmp_path, capsys):
    log = tmp_path / "logs/build.log"
    command = [
        sys.executable,
        "-c",
        'import sys; print("compile started", flush=True); print("host.cpp(222): error C2589: illegal token", file=sys.stderr); sys.exit(7)',
    ]
    with pytest.raises(subprocess.CalledProcessError) as result:
        helper().run(command, log_path=log)
    assert result.value.returncode == 7
    text = log.read_text(encoding="utf8")
    assert "compile started" in text and "error C2589" in text
    assert "error C2589" in capsys.readouterr().out
    assert any("First compiler/linker diagnostic: host.cpp" in note for note in result.value.__notes__)
    assert any(str(log) in note for note in result.value.__notes__)


def test_native_command_logs_success(tmp_path):
    log = tmp_path / "configure.log"
    result = helper().run([sys.executable, "-c", 'print("configured")'], log_path=log)
    assert result.returncode == 0
    assert "configured" in log.read_text(encoding="utf8")
