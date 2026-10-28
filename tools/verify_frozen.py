"""Standard-library-only verification of a frozen binary outside its source tree."""

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable")
    parser.add_argument("--features", action="store_true")
    parser.add_argument("--p3", action="store_true")
    parser.add_argument("--vst3", help="Test VST3 bundle using the bundled native host")
    args = parser.parse_args()
    executable = str(Path(args.executable).resolve())
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        path = root / "32-bit native.wav"
        values = [-2147483648, 2147483647, 1, -1, 123456789]
        encoded = struct.pack("<5i", *values)
        with wave.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(4)
            f.setframerate(48000)
            f.writeframes(encoded)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("VIRTUAL_ENV", None)
        env.pop("CASSOUND_VST3_HOST", None)
        if os.name == "nt":
            windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
            env["PATH"] = str(windows / "System32") + os.pathsep + str(windows)

        def run(*parts):
            completed = subprocess.run(
                [executable, "--json", *parts],
                text=True,
                capture_output=True,
                check=True,
                timeout=180,
                cwd=root,
                env=env,
            )
            return json.loads(completed.stdout)

        assert run("doctor")["media.native"]["available"]
        imported = run(
            "--workdir",
            str(root / "import"),
            "import",
            str(path),
            "--project",
            str(root / "test.cassound"),
            "--portable",
        )
        assert imported["valid_bits"] == 32
        opened = run("--workdir", str(root / "opened"), "project-info", str(root / "test.cassound"))
        assert not opened["missing_sources"]
        result = run(
            "--workdir",
            str(root / "export"),
            "export",
            str(path),
            str(root / "out.wav"),
            "--subtype",
            "PCM_32",
        )
        assert result["frames"] == len(values)
        with wave.open(str(root / "out.wav")) as f:
            assert f.readframes(f.getnframes()) == encoded
        analysis = run("--workdir", str(root / "science"), "analyze", str(path), "--task", "time.metrics")
        assert analysis["task"] == "time.metrics" and "rms" in analysis["arrays"]
        if args.p3:
            recipe = root / "generator.json"
            recipe.write_text(
                json.dumps(
                    {"kind": "sine", "sample_rate": 8000, "duration_seconds": 0.1, "frequency_hz": 440}
                )
            )
            generated = run(
                "--workdir",
                str(root / "p3"),
                "generate",
                str(root / "generated.wav"),
                "--recipe",
                str(recipe),
            )
            assert generated["frames"] == 800
            effects = root / "effects.json"
            effects.write_text('[{"type":"gain","db":-6}]')
            rendered = run(
                "--workdir",
                str(root / "p3"),
                "edit",
                str(root / "generated.wav"),
                str(root / "edited.wav"),
                "--recipe",
                str(effects),
                "--project",
                str(root / "edits.cassound"),
            )
            assert rendered["frames"] == 800
            saved = run("--workdir", str(root / "p3_opened"), "project-info", str(root / "edits.cassound"))
            assert not saved["missing_sources"] and any(a.get("render_identity") for a in saved["sources"])
            print("Frozen P3 generation, edit worker and portable render graph passed")
            if args.vst3:
                inventory = run("--workdir", str(root / "p3"), "plugin-scan", str(Path(args.vst3).resolve()))
                effect = {
                    "type": "vst3",
                    "plugin": str(Path(args.vst3).resolve()),
                    "plugin_digest": inventory["digest"],
                    "class_id": inventory["classes"][0]["class_id"],
                    "parameters": {"1": 0.25},
                    "tail_seconds": 0.01,
                }
                effects.write_text(json.dumps([effect]))
                rendered = run(
                    "--workdir",
                    str(root / "p3"),
                    "edit",
                    str(root / "generated.wav"),
                    str(root / "plugin.wav"),
                    "--recipe",
                    str(effects),
                )
                assert rendered["frames"] == 880
                print("Frozen P3 bundled native VST3 host scan/process passed")
        if args.features:
            import math

            sound = root / "scientific.wav"
            samples = [int(0.4 * 32767 * math.sin(2 * math.pi * 400 * i / 8000)) for i in range(8000)]
            with wave.open(str(sound), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(8000)
                f.writeframes(struct.pack("<8000h", *samples))
            for task in (
                "spectral.stft",
                "speech.pitch",
                "levels.loudness",
                "transforms.cqt",
                "transforms.synchrosqueezed",
                "music.features",
                "patterns.cluster",
            ):
                result = run(
                    "--workdir", str(root / task.replace(".", "_")), "analyze", str(sound), "--task", task
                )
                assert result["task"] == task and result["arrays"]
                print("Frozen scientific task passed:", task)
    print("Frozen CLI, isolated child worker, portable project and exact PCM export passed")


if __name__ == "__main__":
    main()
