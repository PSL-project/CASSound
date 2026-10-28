"""Check ctypes-loaded runtime paths before distributing the desktop bundle."""

import argparse
from importlib.metadata import distribution
from pathlib import Path
import sys


def verify_sklearn_runtime(bundle: Path):
    wheel = distribution("scikit-learn")
    # These files are loaded by path, not by ordinary extension-module imports.
    required = [
        Path(str(file))
        for file in wheel.files or []
        if str(file).lower().endswith(".dll")
        and str(file).replace("\\", "/").split("/")[0] in {"sklearn", "scikit_learn.libs"}
    ]
    if sys.platform == "win32" and not required:
        raise RuntimeError("Cannot audit scikit-learn Windows DLLs: wheel RECORD has no runtime DLLs")
    missing = [str(path) for path in required if not (bundle / "_internal" / path).is_file()]
    if missing:
        raise RuntimeError("Frozen scikit-learn runtime files missing at wheel paths: " + ", ".join(missing))
    print(f"Frozen scikit-learn runtime layout passed ({len(required)} DLLs)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    verify_sklearn_runtime(parser.parse_args().bundle)
