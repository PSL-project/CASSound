"""Bounded worker entry points for integrated file, model and forensic operations."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
import numpy as np
import soundfile as sf
from ..domain.errors import ValidationError, ResourceLimit
from .analysis import clean_json
from .cache import digest_file
from . import models


def audio(path):
    info = sf.info(path)
    if info.frames * info.channels > 8_000_000:
        raise ResourceLimit("Laboratory input exceeds 8,000,000 samples.")
    x, fs = sf.read(path, dtype="float32", always_2d=True)
    if not np.all(np.isfinite(x)):
        raise ValidationError("Audio contains nonfinite samples.")
    return x, fs


def write_json(path, data):
    from ..message_cli import new_output

    with new_output(path) as handle:
        handle.write(json.dumps(clean_json(data), indent=2, ensure_ascii=False, allow_nan=False).encode())


def run(payload, private, root, stage, limits, progress):
    started = time.monotonic()
    op = payload["operation"]
    p = dict(payload["parameters"])
    progress("laboratory", 0.05)
    result = {}
    if op == "model.inventory":
        return {"models": models.inventory(root)}
    if op == "model.install":
        result = models.install(p["path"], root)
    elif op == "model.remove":
        result = models.remove(p["model_id"], root)
    elif op == "model.run":
        from ..engine.neural import run as inference

        x, fs = audio(p["input"])
        if p["model_id"] in {"nmf_separation", "linear_dereverberation"}:
            from ..engine.models import separate, dereverberate

            method = separate if p["model_id"] == "nmf_separation" else dereverberate
            data = method(lambda a, b: x[a:b], len(x), x.shape[1], fs, {})
            y = np.column_stack(list(data["arrays"].values())).astype(np.float32)
            result = {"model": p["model_id"], "method": data["summary"], "sample_rate": fs}
        else:
            y, result = inference(x, fs, p["model_id"], root, p.get("bits"))
        result["source_sha256"] = digest_file(p["input"])
        if y is not None:
            if not p.get("output"):
                raise ValidationError("Processed audio requires a new output WAV path.")
            from ..message_cli import new_output

            if np.max(np.abs(y)) > 1:
                result["over_fs"] = True
            with new_output(p["output"]) as handle:
                sf.write(handle, y, result["sample_rate"], format="WAV", subtype="FLOAT")
            result["output_path"] = p["output"]
            result["output_sha256"] = digest_file(p["output"])
        elif p.get("output"):
            write_json(p["output"], result)
    elif op in {"message-encode", "message-decode"}:
        from ..message_cli import process

        credential = private.get("credential")
        if private.get("raw_key"):
            credential = bytes.fromhex(credential)
        args = SimpleNamespace(
            input=p["input"],
            output=p["output"],
            host=p.get("host"),
            carrier=p.get("carrier", "fsk"),
            rate=p.get("rate", 48000),
            symbol_frames=p.get("symbol_frames", 128),
            host_mode=p.get("host_mode", False),
        )
        result = process(args, credential, op)
    elif op == "keygen":
        from ..message_cli import new_output
        import os

        with new_output(p["output"]) as handle:
            handle.write(os.urandom(32))
        result = {
            "outcome": "key_created",
            "note": "Keep this 32-byte key private; it is not included in project history.",
        }
    elif op == "message.inspect":
        from scipy.io import wavfile
        from ..engine.messages import inspect_bits, demodulate, recover_host

        if Path(p["input"]).stat().st_size > 64 * 1024**2:
            raise ResourceLimit("WAV too large.")
        fs, x = wavfile.read(p["input"])
        mode = p.get("carrier", "lsb")
        if mode == "lsb":
            result = inspect_bits(x, int(p.get("offset", 0)), int(p.get("count", 256)))
        else:
            if x.dtype.kind in "iu":
                x = (x.astype(float) - (128 if x.dtype == np.uint8 else 0)) / max(
                    abs(np.iinfo(x.dtype).min), np.iinfo(x.dtype).max
                )
            decoder = recover_host if p.get("host_mode") else demodulate
            _, result = decoder(x, mode, int(p.get("symbol_frames", 128)), trace=True)
        result.update(
            {
                "sample_rate": fs,
                "native_dtype": str(x.dtype),
                "native_channels": x.shape[1] if x.ndim == 2 else 1,
                "container_header_hex": Path(p["input"]).open("rb").read(64).hex(),
            }
        )
    elif op == "forensics.compare":
        from ..engine.forensics import compare
        from scipy.signal import resample_poly
        import math

        a, fs = audio(p["input"])
        b, other = audio(p["reference"])
        if other != fs:
            g = math.gcd(fs, other)
            b = resample_poly(b, fs // g, other // g)
        rate_scale = float(p.get("rate_scale", 1))
        if not 0.5 <= rate_scale <= 2:
            raise ValidationError("Rate scale outside .5..2.")
        if rate_scale != 1:
            from scipy.signal import resample

            b = resample(b, round(len(b) * rate_scale))
        result = compare(a.mean(axis=1), b.mean(axis=1), fs, float(p.get("max_delay_seconds", 1)))
        residual = result.pop("residual")
        result["channel_comparisons"] = []
        for channel in range(min(a.shape[1], b.shape[1])):
            channel_result = compare(a[:, channel], b[:, channel], fs, float(p.get("max_delay_seconds", 1)))
            channel_result.pop("residual")
            result["channel_comparisons"].append({"channel": channel, **channel_result})
        from scipy.signal import welch

        frequencies, reference_psd = welch(a.mean(axis=1), fs, nperseg=min(4096, len(a)))
        _, candidate_psd = welch(b.mean(axis=1), fs, nperseg=min(4096, len(a)), nfft=min(4096, len(a)))
        result["spectral_difference_db"] = (
            10 * np.log10(np.maximum(candidate_psd, 1e-30) / np.maximum(reference_psd, 1e-30))
        ).tolist()
        result["frequencies_hz"] = frequencies.tolist()
        result["input_channels"] = a.shape[1]
        result["reference_channels"] = b.shape[1]
        result.update(
            {
                "source_sha256": digest_file(p["input"]),
                "reference_sha256": digest_file(p["reference"]),
                "sample_rate": fs,
                "rate_scale": rate_scale,
                "residual_preview": residual[:1024].tolist(),
            }
        )
        if p.get("output"):
            from ..message_cli import new_output

            with new_output(p["output"]) as handle:
                sf.write(handle, residual, fs, format="WAV", subtype="FLOAT")
    elif op == "forensics.enf_reference":
        from ..engine.forensics import enf, match_enf

        x, fs = audio(p["input"])
        data = enf(lambda a, b: x[a:b], len(x), x.shape[1], fs, p.get("parameters", {}))
        if Path(p["reference"]).stat().st_size > 16 * 1024**2:
            raise ResourceLimit("ENF CSV exceeds 16 MiB.")
        ref = np.loadtxt(p["reference"], delimiter=",", skiprows=1, ndmin=2)
        result = match_enf(
            data["arrays"]["time"],
            data["arrays"]["frequency"],
            ref[:, 0],
            ref[:, 1],
            int(p.get("max_shift_seconds", 60)),
        )
    elif op == "forensics.profile":
        from ..engine.forensics import recorder_profile, profile_compare

        x, fs = audio(p["input"])
        feature = recorder_profile(x, fs)
        if p.get("reference"):
            if Path(p["reference"]).stat().st_size > 1024 * 1024:
                raise ResourceLimit("Profile JSON too large.")
            reference = json.loads(Path(p["reference"]).read_text())
            result = profile_compare(feature, reference)
        else:
            result = {
                "profile": feature,
                "interpretation": "User recording feature profile; not a unique device identity.",
            }
        if p.get("output"):
            write_json(p["output"], result)
    elif op == "message.robustness":
        from ..engine import messages, robustness

        credential = private.get("credential") or "ephemeral-fixture-password"
        envelope = messages.seal(b"CASSound robustness fixture", credential)
        result = robustness.sweep(
            envelope, p.get("carrier", "fsk"), int(p.get("symbol_frames", 128)), int(p.get("seed", 0))
        )
        if p.get("output"):
            write_json(p["output"], result)
    elif op == "report.project":
        from .reporting import redact

        result = redact(p.get("snapshot", {}))
        write_json(p["output"], result)
        result = {"outcome": "report_written", "record_count": len(result.get("laboratory", []))}
    else:
        raise ValidationError("Unknown laboratory operation.")
    progress("laboratory", 1)
    result = clean_json(result)
    result.update({"operation": op, "elapsed_seconds": time.monotonic() - started})
    result["receipt_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    return result
