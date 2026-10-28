"""Private child entry point. Never reads a project-supplied Python object."""

import json
import sys
import time
from pathlib import Path
from . import media, storage
from ..domain.errors import CASSoundError


def main():
    started = time.monotonic()
    last = [0.0]

    def progress(stage, fraction):
        now = time.monotonic()
        if now - last[0] > 0.1:
            print(json.dumps({"progress": {"stage": stage, "fraction": fraction}}), flush=True)
            last[0] = now

    try:
        raw = sys.stdin.readline(131073)
        if len(raw) > 131072:
            raise CASSoundError("Control message is too large.", code="resource_limit")
        req = json.loads(raw)
        secret_size = req.get("private_size", 0)
        if type(secret_size) is not int or not 0 <= secret_size <= 16384:
            raise CASSoundError("Private request too large.")
        private = json.loads(sys.stdin.read(secret_size)) if secret_size else {}
        cmd = req["command"]
        if cmd.get("protocol_version") != 1:
            raise CASSoundError("Unsupported protocol.", code="invalid_input")
        payload = cmd["payload"]
        root, stage, limits = Path(req["workdir"]), Path(req["stage"]), req["limits"]
        from .extensions import load

        load(root)
        context = (root, stage, limits, progress)
        name = cmd["command"]
        if name == "lab.run":
            from .laboratory import run

            result = run(payload, private, *context)
        elif name in {"analysis.run", "detection.run", "result.export", "result.view"}:
            from . import analysis

            if name == "result.export":
                result = analysis.export_result(payload, *context)
            elif name == "result.view":
                result = analysis.view_result(payload, *context)
            else:
                result = analysis.run(payload, *context, query=name == "detection.run")
        elif name == "edit.render":
            from . import editing

            result = editing.run(payload, *context)
        elif name == "audio.prepare":
            from . import playback

            result = playback.prepare(payload, *context)
        elif name == "audio.devices":
            from .playback import devices

            result = devices()
        elif name == "plugin.scan":
            from .vst3 import scan

            result = scan(payload, *context)
        elif name == "media.probe":
            result = media.probe(payload["path"])
        elif name in {"media.import", "project.relink"}:
            result = media.import_audio(payload["path"], payload.get("stream_index"), *context)
            if payload.get("expected_digest") and result["sha256"] != payload["expected_digest"]:
                raise CASSoundError(
                    "Replacement source does not match the recorded SHA-256.", code="integrity_failed"
                )
        elif name == "media.extract":
            result = media.extract_stream(payload, *context)
        elif name == "media.envelope":
            result = media.envelope(payload, root)
        elif name == "media.export":
            result = media.export_audio(payload, *context)
        elif name == "project.save":
            result = storage.save_project(payload, *context)
        elif name == "project.open":
            result = storage.open_project(payload, *context)
        else:
            raise CASSoundError("Unknown worker command.", code="invalid_input")
        metrics = {"worker_seconds": time.monotonic() - started}
        if sys.platform != "win32":
            import resource

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            metrics["peak_rss_bytes"] = peak if sys.platform == "darwin" else peak * 1024
        print(
            json.dumps({"result": result, "metrics": metrics}, ensure_ascii=False, allow_nan=False),
            flush=True,
        )
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "code": getattr(exc, "code", "processing_failed")}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
