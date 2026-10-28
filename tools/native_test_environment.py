"""Resolve built test artifacts for CI; never discover or load user plugins."""

import os
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
executable = "cassound-vst3.exe" if os.name == "nt" else "cassound-vst3"
host = next(p for p in root.rglob(executable) if p.is_file())
fixture = next(p for p in root.rglob("cassound-testfx.vst3") if p.is_dir())
values = {"CASSOUND_VST3_HOST": str(host), "CASSOUND_TEST_VST3": str(fixture)}
mda = next((p for p in root.rglob("mda-vst3.vst3") if p.is_dir()), None)
if mda:
    values["CASSOUND_MDA_VST3"] = str(mda)
with open(os.environ["GITHUB_ENV"], "a", encoding="utf8") as f:
    for key, value in values.items():
        f.write(key + "=" + value + "\n")
