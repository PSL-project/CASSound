from pathlib import Path
import os
import tempfile


def default_workdir():
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    folder = base / "CASSound" / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="session-", dir=folder))
