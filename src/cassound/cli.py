import argparse
from dataclasses import asdict, is_dataclass
import json
import sys
from .api import Session
from .domain.errors import CASSoundError
from .domain.types import Region, ExportSpec, AnalysisSpec, QuerySpec, thaw


def serial(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return thaw(value)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Permit the original global workdir syntax for the newer isolated lab CLI.
    if (
        len(argv) >= 3
        and argv[0] == "--workdir"
        and argv[2]
        in {
            "lab",
            "model-inventory",
            "model-install",
            "model-remove",
            "model-run",
            "extensions",
            "extension-enable",
        }
    ):
        argv = [argv[2], "--workdir", argv[1], *argv[3:]]
    if argv and argv[0] == "--gui-startup-check":
        from PySide6.QtWidgets import QApplication
        from .desktop.window import MainWindow
        import tempfile

        app = QApplication([])
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow(Session(root))
            window.show()
            app.processEvents()
            window.modified = False
            window.close()
            app.processEvents()
        return 0
    if argv and argv[0] in {
        "lab",
        "model-inventory",
        "model-install",
        "model-remove",
        "model-run",
        "extensions",
        "extension-enable",
    }:
        from .lab_cli import main as lab_main

        try:
            return lab_main(argv)
        except (CASSoundError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if argv and argv[0] in {"message-encode", "message-decode"}:
        from .message_cli import main as messages_main

        try:
            return messages_main(argv)
        except (CASSoundError, OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if argv and argv[0] == "--worker":
        from .runtime.worker import main as worker

        return worker()
    parser = argparse.ArgumentParser(prog="cassound", description="CASSound audio workbench")
    parser.add_argument("--version", action="version", version="CASSound 1.0.0rc1")
    parser.add_argument("--workdir", help="Session cache and temporary data directory")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="Open the desktop")
    for command in [
        "message-encode",
        "message-decode",
        "lab",
        "model-inventory",
        "model-install",
        "model-remove",
        "model-run",
        "extensions",
        "extension-enable",
    ]:
        sub.add_parser(command, help="Use cassound " + command + " --help for options")
    sub.add_parser("doctor", help="Show installed backend capabilities")
    sub.add_parser("tasks", help="List registered scientific tasks and parameters")
    for name in ("analyze", "detect"):
        q = sub.add_parser(name)
        q.add_argument("path")
        q.add_argument("--stream", type=int)
        q.add_argument("--start", type=float, default=0)
        q.add_argument("--end", type=float)
        q.add_argument("--channels", default="0", help="Comma-separated zero-based channel indices")
        q.add_argument("--export", help="Write result .json metadata or .csv values")
        q.add_argument("--project", help="Save sources, recipes and annotations")
        if name == "analyze":
            q.add_argument("--task", default="spectral.welch_psd")
            q.add_argument("--parameters", default="{}", help="JSON object; task parameters")
        else:
            q.add_argument("--recipe", required=True, help="QuerySpec JSON file")
    for name in ("probe", "import"):
        p = sub.add_parser(name)
        p.add_argument("path")
        if name == "import":
            p.add_argument("--stream", type=int)
            p.add_argument("--project")
            p.add_argument("--portable", action="store_true")
    p = sub.add_parser("project-info")
    p.add_argument("path")
    p = sub.add_parser("export")
    p.add_argument("path")
    p.add_argument("destination")
    p.add_argument("--stream", type=int)
    p.add_argument("--format", default="WAV")
    p.add_argument("--subtype", default="PCM_24")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("extract")
    p.add_argument("path")
    p.add_argument("destination")
    p.add_argument("--stream", type=int)
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("waveform")
    p.add_argument("path")
    p.add_argument("--stream", type=int)
    p.add_argument("--pixels", type=int, default=1200)
    sub.add_parser("devices", help="Enumerate actual PortAudio devices/host APIs")
    p = sub.add_parser("plugin-scan", help="Scan one explicitly selected isolated VST3 effect")
    p.add_argument("path")
    p.add_argument("--retry", action="store_true")
    for name in ("edit", "generate", "timeline"):
        p = sub.add_parser(name)
        if name != "generate":
            p.add_argument("path", help="Media input, or .cassound project for timeline")
        p.add_argument("destination", help="New rendered audio output")
        p.add_argument(
            "--recipe", required=True, help="Effects list, generator parameters, or timeline JSON file"
        )
        p.add_argument("--project", help="Save a portable project including rendered revisions")
        p.add_argument("--subtype", default="DOUBLE")
    p = sub.add_parser("play", help="Explicitly audition a file at reduced gain")
    p.add_argument("path")
    p.add_argument("--device", type=int)
    p.add_argument("--gain-db", type=float, default=-12)
    p = sub.add_parser("record", help="Explicit bounded recording into a new file")
    p.add_argument("destination")
    p.add_argument("--device", type=int)
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--rate", type=int, default=48000)
    p.add_argument("--channels", type=int, default=1)
    args = parser.parse_args(argv)
    if args.command in {None, "gui"}:
        try:
            from .desktop.app import run
        except ImportError:
            print("Install cassound[desktop] to use the GUI.", file=sys.stderr)
            return 3
        return run()
    try:
        with Session(args.workdir) as session:
            if args.command == "doctor":
                result = session.capabilities()
            elif args.command == "tasks":
                result = session.analysis_tasks()
            elif args.command == "devices":
                result = session.audio_devices().wait()
            elif args.command == "plugin-scan":
                result = session.scan_plugin(args.path, retry=args.retry).wait()
            elif args.command in {"edit", "generate", "timeline"}:
                from pathlib import Path

                if Path(args.recipe).stat().st_size > 16 * 1024**2:
                    raise ValueError("Recipe exceeds 16 MiB.")
                recipe = json.loads(Path(args.recipe).read_text(encoding="utf8"))
                if args.command == "generate":
                    rendered = session.generate(recipe).wait()
                elif args.command == "timeline":
                    session.open_project(args.path).wait()
                    rendered = session.render_timeline(recipe).wait()
                else:
                    a = session.asset(session.import_media(args.path).wait().asset_id)
                    rendered = session.edit(
                        Region(a.original_revision_id, 0, a.frames, tuple(range(a.channels))), recipe
                    ).wait()
                result = session.render(
                    rendered.revision_id, ExportSpec(args.destination, subtype=args.subtype)
                ).wait()
                result["revision_id"] = rendered.revision_id
                if args.project:
                    session.save_project(args.project, portable=True).wait()
            elif args.command == "play":
                a = session.asset(session.import_media(args.path).wait().asset_id)
                prepared = session.prepare_playback(
                    Region(a.original_revision_id, 0, a.frames, tuple(range(a.channels)))
                ).wait()
                handle = session.play(prepared, device=args.device, gain_db=args.gain_db)
                try:
                    handle.wait(a.frames / a.sample_rate + 30)
                except KeyboardInterrupt:
                    handle.stop()
                result = {
                    "state": handle.state,
                    "submitted_frames": handle.submitted_frame,
                    "underflows": handle.underflows,
                }
            elif args.command == "record":
                handle = session.record(
                    args.destination,
                    sample_rate=args.rate,
                    channels=args.channels,
                    duration_seconds=args.duration,
                    device=args.device,
                )
                try:
                    handle.wait(args.duration + 30)
                except KeyboardInterrupt:
                    handle.stop()
                if handle.error:
                    raise CASSoundError(handle.error)
                result = {
                    "state": handle.state,
                    "frames": handle.frames,
                    "path": str(handle.path),
                    "overflows": handle.overflows,
                }
            elif args.command in {"analyze", "detect"}:
                imported = session.import_media(args.path, args.stream).wait()
                asset = session.asset(imported.asset_id)
                channels = tuple(int(v) for v in args.channels.split(","))
                region = Region(
                    asset.original_revision_id,
                    round(args.start * asset.sample_rate),
                    min(asset.frames, round(args.end * asset.sample_rate))
                    if args.end is not None
                    else asset.frames,
                    channels,
                )
                if args.command == "analyze":
                    reference = session.analyze(
                        region, AnalysisSpec(args.task, json.loads(args.parameters))
                    ).wait()
                else:
                    reference = session.detect(region, QuerySpec.load(args.recipe)).wait()
                result = reference.to_dict()
                result["findings"] = [thaw(v) for v in session.findings(reference.result_id)]
                if args.export:
                    session.export_result(reference.result_id, args.export).wait()
                if args.project:
                    session.save_project(args.project).wait()
            elif args.command == "probe":
                result = session.probe(args.path).wait()
            elif args.command == "extract":
                result = session.extract_stream(
                    args.path, args.destination, args.stream, overwrite=args.overwrite
                ).wait()
            elif args.command == "project-info":
                result = session.open_project(args.path).wait()
                result["sources"] = [a.to_dict() for a in session.assets()]
            else:
                imported = session.import_media(args.path, stream_index=args.stream).wait()
                asset = session.asset(imported.asset_id)
                if args.command == "import":
                    result = asset.to_dict()
                    if args.project:
                        session.save_project(args.project, portable=args.portable).wait()
                elif args.command == "export":
                    result = session.render(
                        asset.original_revision_id,
                        ExportSpec(
                            args.destination,
                            format=args.format,
                            subtype=args.subtype,
                            overwrite=args.overwrite,
                        ),
                    ).wait()
                else:
                    region = Region(asset.original_revision_id, 0, asset.frames, tuple(range(asset.channels)))
                    result = session.waveform(region, args.pixels).wait()
            if args.json:
                print(json.dumps(serial(result), ensure_ascii=False, allow_nan=False))
            elif args.command == "doctor":
                for key, value in result.items():
                    print(f"{key}: {value}")
            else:
                print(json.dumps(serial(result), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (CASSoundError, OSError, ValueError) as exc:
        obj = {"error": getattr(exc, "code", "invalid_input"), "message": str(exc)}
        print(json.dumps(obj) if args.json else obj["message"], file=sys.stderr)
        return {"invalid_input": 2, "backend_unavailable": 3, "cancelled": 5, "integrity_failed": 6}.get(
            obj["error"], 4
        )


if __name__ == "__main__":
    raise SystemExit(main())
