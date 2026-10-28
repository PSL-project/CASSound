"""Build real package artifacts; desktop builds use the current native OS."""

import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["python", "desktop", "installer"], required=True)
    parser.add_argument("--require-windows", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.require_windows and sys.platform != "win32":
        parser.error("A Windows executable must be built and verified on Windows.")
    if args.target == "python":
        command = [sys.executable, "-m", "build", str(root)]
    elif args.target == "installer":
        if sys.platform != "win32":
            parser.error("Inno Setup requires Windows.")
        import shutil

        compiler = shutil.which("ISCC") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
        command = [compiler, str(root / "packaging/installer.iss")]
    else:
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(root / "packaging" / "desktop.spec"),
        ]
    subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
