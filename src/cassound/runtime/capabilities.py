import importlib.util
from .media import backend_path


def capabilities():
    ffmpeg = backend_path("ffmpeg", required=False)
    ffprobe = backend_path("ffprobe", required=False)
    return {
        "phase": "P3",
        "media.native": {"available": True, "backend": "libsndfile"},
        "media.containers": {
            "available": bool(ffmpeg and ffprobe),
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "reason": None
            if ffmpeg and ffprobe
            else "Install FFmpeg/ffprobe or configure CASSOUND_FFMPEG/CASSOUND_FFPROBE.",
        },
        "desktop": {
            "available": importlib.util.find_spec("PySide6") is not None
            and importlib.util.find_spec("pyqtgraph") is not None
        },
        "project": {"available": True, "schema": 1},
        "analysis": {"available": True, "work_package": "P2", "core_backend": "scipy"},
        "editing": {"available": True, "work_package": "P3", "dense_sample_budget": 8000000},
        "audio": {
            "available": importlib.util.find_spec("sounddevice") is not None,
            "devices": "explicit enumeration required; no device opened by inventory",
        },
        "acoustics": {"available": True, "calibration": "explicit chain identity required"},
        "vst3": vst_capability(),
        "messages": {"available": False, "work_package": "P4"},
    }


def vst_capability():
    from .vst3 import host_path
    from ..domain.errors import CASSoundError

    try:
        return {"available": True, "host": str(host_path()), "scope": "x64 mono/stereo offline effects"}
    except CASSoundError as exc:
        return {"available": False, "reason": str(exc), "scope": "x64 mono/stereo offline effects"}
