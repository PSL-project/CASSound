"""Inventory actual/locked package versions and hashes for selected artifacts."""

import argparse
import hashlib
import json
import tomllib
from pathlib import Path
from importlib import metadata


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("docs/release-inventory.json"))
    p.add_argument("--artifacts", type=Path)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[1]
    lock = tomllib.loads((root / "uv.lock").read_text())
    rows = []
    for item in lock["package"]:
        name = item["name"]
        try:
            installed = metadata.distribution(name)
            actual = installed.version
            license = (
                installed.metadata.get("License-Expression")
                or installed.metadata.get("License")
                or "See distribution notices"
            )
        except metadata.PackageNotFoundError:
            actual = None
            license = "See upstream package notices"
        if name == "cassound":
            actual = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        rows.append(
            {
                "name": name,
                "locked_version": item["version"],
                "installed_version": actual,
                "license": license,
                "purl": f"pkg:pypi/{name}@{item['version']}",
            }
        )
    artifacts = []
    if a.artifacts:
        for file in sorted(a.artifacts.iterdir()):
            if file.is_file():
                artifacts.append(
                    {
                        "name": file.name,
                        "size": file.stat().st_size,
                        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    }
                )
    result = {
        "format": "CASSound dependency/artifact inventory v1",
        "version": "1.0.0rc1",
        "packages": rows,
        "artifacts": artifacts,
        "signing": "No signing credential used; no signed-artifact claim",
        "windows_execution": "Not executed in Linux authoring environment",
    }
    a.output.write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
