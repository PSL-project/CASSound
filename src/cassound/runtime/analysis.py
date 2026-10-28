"""Source readers, reproducible result artifacts and P2 worker integration."""

import csv
import importlib.metadata
import json
import os
from pathlib import Path
import numpy as np
from .cache import Cache, cache_key, atomic_json, digest_file
from .locking import cache_write_lock
from ..domain.errors import ValidationError, ResourceLimit, IntegrityError
from ..domain.units import normalized
from ..engine import registry, features, detection, patterns

MAX_OUTPUT = 256 * 1024**2


def reader(root, asset, region):
    path = Cache(root / "cache").path(asset["cache_key"])
    metadata = json.loads((path / "asset.json").read_text())
    if any(
        metadata.get(k) != asset[k] for k in ("sha256", "stream_index", "frames", "channels", "sample_rate")
    ):
        raise IntegrityError("Source-cache identity mismatch.")
    raw = np.load(path / "samples.npy", mmap_mode="r", allow_pickle=False)
    start, end = region["start_frame"], region["end_frame"]
    channels = region["channels"]
    if not (
        type(start) is int
        and type(end) is int
        and 0 <= start < end <= asset["frames"]
        and channels
        and all(type(c) is int and 0 <= c < asset["channels"] for c in channels)
    ):
        raise ValidationError("Analysis region exceeds source bounds.")
    if raw.shape != (asset["frames"], asset["channels"]):
        raise IntegrityError("Cached sample shape is invalid.")

    def read(a, b):
        if not (0 <= a <= b <= end - start):
            raise ValidationError("Reader request exceeds selected region.")
        return normalized(raw[start + a : start + b, channels], asset["sample_kind"], asset["valid_bits"])

    return read, end - start, len(channels), asset["sample_rate"]


def clean_json(value):
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, complex):
        return str(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    return value


def versions():
    values = {}
    for name in (
        "numpy",
        "scipy",
        "librosa",
        "ssqueezepy",
        "scikit-learn",
        "pyloudnorm",
        "soundfile",
        "numba",
        "llvmlite",
        "soxr",
    ):
        try:
            values[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return values


def run(payload, root, stage, limits, progress, query=False):
    asset = payload["asset"]
    region = payload["region"]
    spec = payload["spec"]
    if query:
        detection.validate_rule(spec["rule"])
        registry.validate("features.spectral", spec.get("parameters", {}))
    else:
        registry.validate(spec["task"], spec.get("parameters", {}))
    provenance = {
        "source_sha256": asset["sha256"],
        "stream_index": asset["stream_index"],
        "decoder": asset["decoder"],
        "sample_cache_key": asset["cache_key"],
        "revision_id": region["revision_id"],
        "region": region,
        "spec": spec,
        "algorithm_contract": 1,
        "application": "1.0.0.dev3",
        "libraries": versions(),
    }
    for field in ("template", "examples"):
        if field in payload:
            entries = payload[field] if field == "examples" else [payload[field]]
            canonical = [
                {
                    "source_sha256": v["asset"]["sha256"],
                    "stream_index": v["asset"]["stream_index"],
                    "sample_cache_key": v["asset"]["cache_key"],
                    "region": v["region"],
                    **({"label": v["label"]} if "label" in v else {}),
                }
                for v in entries
            ]
            provenance[field] = canonical if field == "examples" else canonical[0]
    key = cache_key(provenance)
    directory = root / "results" / key
    if (directory / "manifest.json").is_file():
        return json.loads((directory / "manifest.json").read_text())
    progress("analysis", 0)
    read, n, c, fs = reader(root, asset, region)
    extra = {}
    task = "detection.query" if query else spec["task"]
    p = spec.get("parameters", {})
    if task == "acoustics.calibrated" and region["channels"] != [p.get("chain", {}).get("input_channel")]:
        raise ValidationError("Selected channel differs from the calibration chain.")
    if task == "patterns.template":
        template = payload.get("template")
        if not template:
            raise ValidationError("Template search requires a registered template region.")
        tr, tn, tc, tfs = reader(root, template["asset"], template["region"])
        if tfs != fs:
            raise ValidationError(
                "Template and target sample rates differ; resample explicitly before matching."
            )
        if tn * tc > 1000000:
            raise ResourceLimit("Template exceeds size budget.")
        extra["template"] = tr(0, tn)
    if task == "patterns.train":
        examples = payload.get("examples", [])
        if not 4 <= len(examples) <= 64:
            raise ValidationError("Training requires four to 64 labeled regions.")
        extra = {"vectors": [], "labels": [], "groups": []}
        for i, example in enumerate(examples):
            er, en, ec, ef = reader(root, example["asset"], example["region"])
            if ef != fs:
                raise ValidationError("Training examples must use the same sample rate in this profile.")
            bp = {k: v for k, v in p.items() if k in registry.BANK}
            bank = features.bank(er, en, ec, ef, bp)["arrays"]
            vec = patterns.vectors(bank)
            finite = np.isfinite(vec).all(axis=1)
            if not finite.any():
                raise ValidationError("Training selection has no valid feature frames.")
            if len(extra["vectors"]) + int(finite.sum()) > 100000:
                raise ResourceLimit("Training frame budget exceeded.")
            extra["vectors"].extend(vec[finite].tolist())
            extra["labels"].extend([example["label"]] * int(finite.sum()))
            # Group identity is always derived from source content, not caller-controlled labels.
            extra["groups"].extend([example["asset"]["sha256"]] * int(finite.sum()))
            progress("training.features", (i + 1) / len(examples))
    result = (
        detection.detect(read, n, c, fs, spec) if query else registry.execute(task, read, n, c, fs, p, extra)
    )
    stage_result = stage / "result"
    stage_result.mkdir()
    arrays = {}
    size = 0
    for name, values in result["arrays"].items():
        if not name.replace("_", "").isalnum() or len(name) > 80:
            raise ValidationError("Invalid array artifact name.")
        values = np.asarray(values)
        if values.dtype.hasobject:
            raise ValidationError("Object arrays are not permitted in scientific artifacts.")
        size += values.nbytes
        if size > limits.get("analysis_bytes", MAX_OUTPUT):
            raise ResourceLimit("Analysis output budget exceeded; use a smaller selection.")
        np.save(stage_result / (name + ".npy"), values, allow_pickle=False)
        arrays[name] = {
            "file": name + ".npy",
            "shape": list(values.shape),
            "dtype": str(values.dtype),
            "unit": result["units"].get(name),
            "sha256": digest_file(stage_result / (name + ".npy")),
        }
    summary = clean_json(result["summary"])
    findings = summary.pop("findings", [])
    if len(findings) > 10000:
        raise ResourceLimit("Too many findings; narrow the query or raise its minimum duration.")
    for finding in findings:
        finding["start_frame"] += region["start_frame"]
        finding["end_frame"] += region["start_frame"]
        finding["channels"] = [region["channels"][v] for v in finding["channels"]]
        finding["revision_id"] = region["revision_id"]
        finding["result_id"] = "result-" + key
        finding["method"] = task
        finding["review_status"] = "unreviewed"
        finding["finding_id"] = "finding-" + cache_key({"result": key, "finding": finding})
    atomic_json(stage_result / "findings.json", findings)
    summary["finding_count"] = len(findings)
    summary["presentation"] = result.get("presentation", {})
    summary["time_origin_seconds"] = region["start_frame"] / fs
    value = {
        "result_id": "result-" + key,
        "revision_id": region["revision_id"],
        "task": task,
        "summary": summary,
        "arrays": arrays,
        "region": region,
        "provenance": provenance,
    }
    atomic_json(stage_result / "manifest.json", value)
    if (stage_result / "manifest.json").stat().st_size > 256 * 1024:
        raise ResourceLimit("Analysis manifest exceeds its bound.")
    with cache_write_lock(root):
        result_root = root / "results"
        result_root.mkdir(exist_ok=True)
        used = sum(path.stat().st_size for path in result_root.rglob("*") if path.is_file())
        if Cache(root / "cache").usage() + used + size > limits.get("cache_bytes", 20 * 1024**3):
            raise ResourceLimit("Scientific artifacts exceed the combined cache quota.")
        if not directory.exists():
            os.rename(stage_result, directory)
    progress("analysis", 1)
    return value


def result_directory(root, result_id):
    if not result_id.startswith("result-"):
        raise ValidationError("Invalid result ID.")
    key = result_id[7:]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValidationError("Invalid result digest.")
    directory = Path(root) / "results" / key
    if not (directory / "manifest.json").is_file():
        raise ValidationError("Result arrays are unavailable; rerun its saved analysis recipe.")
    return directory


def export_result(payload, root, stage, limits, progress):
    directory = result_directory(root, payload["result_id"])
    manifest = json.loads((directory / "manifest.json").read_text())
    verified_arrays = {}
    for name, descriptor in manifest["arrays"].items():
        if not name.replace("_", "").isalnum() or descriptor["file"] != name + ".npy":
            raise ValidationError("Invalid result array path.")
        file = directory / descriptor["file"]
        if digest_file(file) != descriptor["sha256"]:
            raise IntegrityError("Result array hash mismatch.")
        verified_arrays[name] = np.load(file, mmap_mode="r", allow_pickle=False)
    destination = Path(payload["path"]).expanduser().resolve()
    output = stage / "export"
    if destination.suffix.lower() == ".json":
        findings = json.loads((directory / "findings.json").read_text())
        token = payload.get("review_token")
        if token:
            if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
                raise ValidationError("Invalid review snapshot reference.")
            file = root / "requests" / (token + ".json")
            if file.stat().st_size > 32 * 1024**2:
                raise ResourceLimit("Review snapshot is too large.")
            overlays = {v["finding_id"]: v for v in json.loads(file.read_text())}
            for item in findings:
                if item["finding_id"] in overlays:
                    overlay = overlays[item["finding_id"]]
                    item["review_status"] = overlay["review_status"]
                    item["review_note"] = overlay.get("review_note", "")
        data = {"result": manifest, "findings": findings}
        output.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
    elif destination.suffix.lower() == ".csv":
        # Long-form CSV supports arbitrary array dimensions and includes units/index coordinates.
        with output.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["array", "index", "value", "unit"])
            for name, descriptor in manifest["arrays"].items():
                values = np.load(directory / descriptor["file"], mmap_mode="r", allow_pickle=False)
                for indices in np.ndindex(values.shape):
                    value = values[indices]
                    if isinstance(value, np.complexfloating):
                        value = str(value)
                    if isinstance(value, (str, np.str_)) and value.startswith(
                        ("=", "+", "-", "@", "\t", "\r")
                    ):
                        value = "'" + value
                    writer.writerow([name, ",".join(map(str, indices)), value, descriptor.get("unit") or ""])
        progress("result.export", 1)
    elif destination.suffix.lower() in {".html", ".parquet", ".png", ".svg", ".pdf"}:
        from .reporting import write

        write(output, destination.suffix.lower(), manifest, verified_arrays)
    else:
        raise ValidationError("Supported formats: JSON, CSV, HTML, Parquet, PNG, SVG, PDF.")
    return {
        "staged_path": str(output),
        "destination": str(destination),
        "overwrite": payload.get("overwrite", False),
        "sha256": digest_file(output),
    }


def view_result(payload, root, stage, limits, progress):
    directory = result_directory(root, payload["result_id"])
    manifest = json.loads((directory / "manifest.json").read_text())
    plot = manifest["summary"].get("presentation", {})
    kind = plot.get("kind", "table")
    channel = payload.get("channel", 0)

    def load(name):
        if name not in manifest["arrays"]:
            raise ValidationError("Result lacks the requested display array.")
        descriptor = manifest["arrays"][name]
        if descriptor["file"] != name + ".npy" or not name.replace("_", "").isalnum():
            raise ValidationError("Invalid display array reference.")
        file = directory / descriptor["file"]
        if digest_file(file) != descriptor["sha256"]:
            raise IntegrityError("Display array digest mismatch.")
        return np.load(file, mmap_mode="r", allow_pickle=False)

    answer = {
        "kind": kind,
        "presentation": plot,
        "summary": manifest["summary"],
        "task": manifest["task"],
        "region": manifest["region"],
        "revision_id": manifest["revision_id"],
        "result_id": manifest["result_id"],
    }
    if kind == "compare":
        answer["series"] = []
        for i, method in enumerate(manifest["summary"]["methods"]):
            presentation = method["presentation"]
            xx = presentation.get("x")
            yy = presentation.get("y")
            if not xx or not yy:
                answer["kind"] = "table"
                break
            xx = "method" + str(i) + "_" + xx
            yy = "method" + str(i) + "_" + yy
            x = load(xx)
            y = load(yy)
            if y.ndim == 2:
                y = y[:, 0]
            if y.ndim != 1 or len(x) != len(y) or y.dtype.kind not in "iuf":
                answer["kind"] = "table"
                break
            positions = np.linspace(0, len(y), min(1500, len(y)) + 1, dtype=int)
            answer["series"].append(
                {
                    "x": x[positions[:-1]].tolist(),
                    "y": clean_json(np.fmax.reduceat(y, positions[:-1]).tolist()),
                    "label": method["task"] + " " + str(method["parameters"]),
                    "unit": manifest["arrays"][yy].get("unit"),
                }
            )
        if len({v["unit"] for v in answer["series"]}) > 1:
            answer["kind"] = "table"
    elif kind == "image" and all(name in plot for name in ("x", "y", "z")):
        x, y, z = np.asarray(load(plot["x"])), np.asarray(load(plot["y"])), load(plot["z"])
        if z.ndim == 3:
            if not 0 <= channel < z.shape[2]:
                raise ValidationError("Display channel is out of range.")
            z = z[:, :, channel]
        if z.ndim != 2:
            raise ValidationError("Image display requires a matrix.")
        tx = np.linspace(0, z.shape[0], min(600, z.shape[0]) + 1, dtype=int)
        fy = np.linspace(0, z.shape[1], min(384, z.shape[1]) + 1, dtype=int)
        reduced = np.fmax.reduceat(np.fmax.reduceat(z, tx[:-1], axis=0), fy[:-1], axis=1)
        answer.update(
            {
                "x": x[tx[:-1]].tolist(),
                "y": y[fy[:-1]].tolist(),
                "values": clean_json(reduced.tolist()),
                "aggregation": "peak pooling for display only",
            }
        )
    elif kind in {"line", "events", "histogram"} and plot.get("x") and plot.get("y"):
        x = np.asarray(load(plot["x"]))
        values = load(plot["y"])
        if values.ndim == 2:
            if not 0 <= channel < values.shape[1]:
                raise ValidationError("Display channel/column is out of range.")
            values = values[:, channel]
        if values.ndim != 1 or values.dtype.kind not in "iufb":
            answer["kind"] = "table"
            answer["rows"] = clean_json(np.asarray(values)[:32].tolist())
        else:
            if len(x) == len(values) + 1:
                x = (x[:-1] + x[1:]) / 2
            if len(x) != len(values):
                raise ValidationError("Display axes do not share a frame grid.")
            if len(values):
                indices = np.linspace(0, len(values), min(1500, len(values)) + 1, dtype=int)
                answer.update(
                    {
                        "x": x[indices[:-1]].tolist(),
                        "minimum": clean_json(np.fmin.reduceat(values, indices[:-1]).tolist()),
                        "maximum": clean_json(np.fmax.reduceat(values, indices[:-1]).tolist()),
                    }
                )
            else:
                answer.update({"x": [], "minimum": [], "maximum": []})
    else:
        answer["tables"] = {}
        for name in manifest["arrays"]:
            values = load(name)
            if values.ndim == 1:
                preview = values[:32]
            elif values.ndim == 2:
                preview = values[:32, :8]
            elif values.ndim == 3:
                preview = values[:16, :8, :2]
            else:
                continue
            answer["tables"][name] = clean_json(np.asarray(preview).tolist())
        answer["table_note"] = "Preview values only; full arrays remain available through API/CSV."
    return answer
