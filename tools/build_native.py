"""Build the isolated native host from pinned MIT sources. No plugin is loaded during build."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

SDK_REV = "3cdf9ca5d1f5b1b21e0a86832aa4abe55607bd96"
JSON_REV = "55f93686c01528224f448c19128836e7df245f72"


def run(parts, **kw):
    subprocess.run([str(x) for x in parts], check=True, **kw)


def checkout(url, rev, path):
    if not (path / ".git").is_dir():
        path.mkdir(parents=True, exist_ok=True)
        run(["git", "init", path])
        run(["git", "-C", path, "remote", "add", "origin", url])
    run(["git", "-C", path, "fetch", "--depth", "1", "origin", rev])
    run(["git", "-C", path, "checkout", "--detach", rev])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deps", default=".native-deps")
    parser.add_argument("--build", default="build/native")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--mda", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    deps = Path(args.deps).resolve()
    build = Path(args.build).resolve()
    sdk = deps / "vst3sdk"
    js = deps / "json"
    if args.fetch:
        checkout("https://github.com/steinbergmedia/vst3sdk.git", SDK_REV, sdk)
        run(
            [
                "git",
                "-C",
                sdk,
                "submodule",
                "update",
                "--init",
                "--depth",
                "1",
                "base",
                "cmake",
                "pluginterfaces",
                "public.sdk",
            ]
        )
        checkout("https://github.com/nlohmann/json.git", JSON_REV, js)
    for path, rev in [(sdk, SDK_REV), (js, JSON_REV)]:
        current = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
        if current != rev:
            raise SystemExit("Native dependency differs from pinned revision: " + str(path))
    run(
        [
            "cmake",
            "-S",
            root / "native/vst3_host",
            "-B",
            build,
            *(["-A", "x64"] if sys.platform == "win32" else []),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DVST3_SDK_ROOT=" + str(sdk),
            "-DJSON_ROOT=" + str(js),
            "-DCASSOUND_BUILD_MDA_TESTS=" + ("ON" if args.mda else "OFF"),
        ]
    )
    targets = ["cassound-vst3", "cassound-testfx"] + (["mda-vst3"] if args.mda else [])
    run(["cmake", "--build", build, "--config", "Release", "--target", *targets, "--parallel", "4"])
    licenses = build / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(sdk / "LICENSE.txt", licenses / "VST3-SDK-MIT.txt")
    shutil.copy2(js / "LICENSE.MIT", licenses / "nlohmann-json-MIT.txt")
    for host in list(build.rglob("cassound-vst3.exe")) + list(build.rglob("cassound-vst3")):
        if host.is_file() and host.parent != build:
            target = host.parent / "licenses"
            target.mkdir(exist_ok=True)
            for source in licenses.glob("*.txt"):
                shutil.copy2(source, target / source.name)
    fixture = next(
        p for p in build.rglob("cassound-testfx.vst3") if p.is_dir() and "fixture_bundle" not in p.parts
    )
    bundle = build / "fixture_bundle"
    bundle.mkdir(exist_ok=True)
    shutil.copytree(fixture, bundle / fixture.name, dirs_exist_ok=True)
    shutil.copytree(licenses, bundle / "licenses", dirs_exist_ok=True)
    print("Native host build complete:", build)


if __name__ == "__main__":
    main()
