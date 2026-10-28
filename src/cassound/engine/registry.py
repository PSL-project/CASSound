"""Versioned task allowlist. Availability means a callable implementation, not an accuracy claim."""

import importlib.util
from . import dsp, features, transforms, levels, patterns, acoustics, editing, forensics, models
from ..domain.errors import ValidationError, BackendUnavailable

FRAME = {"frame_length", "hop_length", "nperseg", "noverlap", "window"}
PSD = FRAME | {"nfft", "detrend", "scaling"}
BANK = FRAME | {"fmin", "fmax", "clipping_threshold", "fundamental", "harmonic_threshold_db"}
TASKS = {}
LOADED_EXTENSIONS = set()


def register(name, fn, parameters=(), backend=None, kind="line"):
    if name in TASKS:
        raise ValidationError("Task is already registered: " + name)
    TASKS[name] = {
        "function": fn,
        "parameters": set(parameters),
        "backend": backend,
        "kind": kind,
        "version": 1,
    }


register(
    "forensics.spectral_changes", forensics.spectral_changes, {"frame_length", "threshold_db"}, kind="table"
)
register("forensics.discontinuities", forensics.discontinuities, {"threshold"}, kind="table")
register("forensics.enf", forensics.enf, {"nominal_hz", "bandwidth_hz", "window_seconds"})
register("models.nmf_separation", models.separate, {"components"}, "sklearn")
register("models.linear_dereverberation", models.dereverberate, {"taps", "delay_frames", "ridge"})
register("time.metrics", dsp.metrics, kind="table")
register("time.histogram", dsp.histogram, {"bins", "minimum", "maximum"})
register("spectral.fft", dsp.spectrum, {"nfft", "window", "detrend"})
register("spectral.welch_psd", dsp.welch, PSD)
register("spectral.stft", dsp.spectrogram, FRAME | {"nfft"}, kind="image")
register("spectral.peaks", dsp.peaks, PSD | {"prominence_db", "max_peaks"}, kind="table")
register("spectral.band_power", dsp.band_power, PSD | {"fmin", "fmax"}, kind="table")
register("spectral.harmonics", dsp.harmonics, PSD | {"fundamental", "harmonics", "tolerance_hz"})
register("time.analytic", dsp.analytic, {"envelope_floor"})
register("channels.relationships", dsp.relationships, {"max_lag_seconds"})
register("relationships.correlation", dsp.correlation, {"max_lag_seconds"})
register("relationships.coherence", dsp.coherence, FRAME)
register("filters.response", dsp.filter_response, {"kind", "mode", "order", "cutoff_hz", "taps"})
register("spectral.bicoherence", dsp.bicoherence, FRAME | {"bins"}, kind="image")
register("features.spectral", features.bank, BANK)
register("speech.pitch", features.pitch, (FRAME - {"window"}) | {"fmin", "fmax", "yin_threshold"})
register(
    "speech.acoustics",
    features.acoustics,
    (FRAME - {"window"}) | {"fmin", "fmax", "yin_threshold", "min_voiced_fraction", "lpc_order"},
)
register("speech.activity", features.voice_activity, BANK | {"activity_floor"})
register("music.features", features.music, {"frame_length", "hop_length", "activity_floor"}, "librosa")
register("transforms.cwt", transforms.wavelet, {"fmin", "fmax", "bands", "time_stride"}, kind="image")
register(
    "transforms.cqt",
    None,
    {"hop_length", "frame_length", "fmin", "bins", "bins_per_octave"},
    "librosa",
    "image",
)
register("transforms.mel", None, {"hop_length", "frame_length", "bands", "window"}, "librosa", "image")
register("transforms.reassigned", None, {"hop_length", "frame_length"}, "librosa", "image")
register("transforms.cepstrum", transforms.cepstrum, {"max_quefrency"})
register(
    "transforms.synchrosqueezed", transforms.synchrosqueezed, {"voices", "time_stride"}, "ssqueezepy", "image"
)
register("levels.loudness", levels.loudness, (), "pyloudnorm")
register(
    "physics.measurements",
    levels.physics,
    {
        "quantity",
        "frequency_hz",
        "speed_m_s",
        "calibration_id",
        "pa_per_fs",
        "plane_wave_assumption",
        "density_kg_m3",
    },
    kind="table",
)
register("patterns.cluster", None, BANK | {"clusters", "seed"}, "sklearn")
register("patterns.template", None, {"threshold", "minimum_gap_seconds", "allow_inversion"})
register("patterns.train", None, BANK | {"seed", "test_fraction"}, "sklearn", "table")
register("patterns.predict", None, BANK | {"model"})
register("analysis.compare", None, {"methods"})
register("signals.dtmf", None)
register("signals.morse", None, {"unit_seconds", "amplitude_floor"})


register(
    "acoustics.transfer", acoustics.transfer, {"method", "regularization", "frame_length", "impulse_frames"}
)
register("acoustics.room", acoustics.room, {"direct_frame", "minimum_fit_r2"})
register(
    "acoustics.distortion", acoustics.distortion, {"mode", "fundamental_hz", "harmonics", "f1_hz", "f2_hz"}
)
register("acoustics.calibrated", acoustics.calibrated, {"profile", "chain"}, kind="table")


register("editing.response", editing.response, editing.EFFECT_PARAMETERS["filter"])


def describe():
    return [
        {
            "task": name,
            "version": 1,
            "parameters": sorted(spec["parameters"]),
            "presentation": spec["kind"],
            "available": not spec["backend"] or importlib.util.find_spec(spec["backend"]) is not None,
            "optional_backend": spec["backend"],
        }
        for name, spec in TASKS.items()
    ]


def validate(name, p):
    if name not in TASKS:
        raise ValidationError("Unknown analysis task. Use analysis_tasks() or cassound tasks.")
    spec = TASKS[name]
    unknown = set(p) - spec["parameters"]
    if unknown:
        raise ValidationError("Unknown parameters for " + name + ": " + ", ".join(sorted(unknown)))
    if spec["backend"] and importlib.util.find_spec(spec["backend"]) is None:
        raise BackendUnavailable("Install cassound[features] for " + spec["backend"])
    return spec


def execute(name, read, n, c, fs, p, extra=None):
    from . import detection

    spec = validate(name, p)
    extra = extra or {}
    if name.startswith("transforms.") and name in {
        "transforms.cqt",
        "transforms.mel",
        "transforms.reassigned",
    }:
        return transforms.librosa_transform(name, read, n, c, fs, p)
    if name == "patterns.template":
        return patterns.template_search(read, n, c, fs, p, extra["template"])
    if name == "patterns.train":
        return patterns.train(extra["vectors"], extra["labels"], extra["groups"], p)
    if name in {"patterns.cluster", "patterns.predict"}:
        base = {k: v for k, v in p.items() if k in BANK}
        bank = features.bank(read, n, c, fs, base)["arrays"]
        return patterns.cluster(bank, p) if name.endswith("cluster") else patterns.predict(bank, p)
    if name == "signals.dtmf":
        return detection.dtmf(read, n, c, fs, p)
    if name == "signals.morse":
        return detection.morse(read, n, c, fs, p)
    if name == "analysis.compare":
        methods = p.get("methods")
        if not isinstance(methods, list) or not 2 <= len(methods) <= 4:
            raise ValidationError("Compare requires two to four task specifications.")
        result = {"arrays": {}, "summary": {"methods": []}, "units": {}, "presentation": {}}
        for i, method in enumerate(methods):
            task = method.get("task", "")
            if task == "analysis.compare" or task.startswith("patterns."):
                raise ValidationError(
                    "Nested comparisons and pattern-training tasks are not comparison methods."
                )
            current = execute(task, read, n, c, fs, method.get("parameters", {}))
            result["summary"]["methods"].append(
                {
                    "task": task,
                    "parameters": method.get("parameters", {}),
                    "summary": current["summary"],
                    "presentation": current["presentation"],
                }
            )
            result["arrays"].update({f"method{i}_{key}": value for key, value in current["arrays"].items()})
            result["units"].update({f"method{i}_{key}": value for key, value in current["units"].items()})
        result["presentation"] = {"kind": "compare"}
        return result
    return spec["function"](read, n, c, fs, p)
