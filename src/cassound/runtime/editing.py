"""Cancellable render/generator/calibration worker commands and immutable materialization."""

import json
import os
import numpy as np
import struct
from .cache import cache_key, digest_file, Cache
from .analysis import reader, versions
from .media import import_audio
from ..domain.errors import ValidationError, ResourceLimit, IntegrityError
from ..engine import editing, generation, acoustics
from ..engine.common import integer as integer_value, number as number_value


def read_request(payload, root):
    token = payload["recipe_token"]
    if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValidationError("Invalid render recipe reference.")
    path = root / "requests" / (token + ".json")
    if path.stat().st_size > 16 * 1024**2:
        raise ResourceLimit("Render recipe exceeds metadata budget.")
    return json.loads(path.read_text())


def remap(maps, p, before, after):
    op = p["type"]
    result = []
    for m in maps:
        v = dict(m)
        a, b = v["output_start"], v["output_end"]
        ia, ib = v["input_start"], v["input_end"]
        if op == "trim":
            left = p.get("start_frame", 0)
            right = p.get("end_frame", before)
            a2, b2 = max(a, left), min(b, right)
            if b2 <= a2:
                continue
            v.update(
                output_start=a2 - left,
                output_end=b2 - left,
                input_start=ia + (ib - ia) * (a2 - a) / (b - a),
                input_end=ia + (ib - ia) * (b2 - a) / (b - a),
            )
        elif op == "reverse":
            v.update(output_start=before - b, output_end=before - a, input_start=ib, input_end=ia)
        elif op in {"stretch", "resample"}:
            ratio = after / before
            v.update(output_start=a * ratio, output_end=b * ratio)
        if op in {
            "stretch",
            "pitch",
            "spectral_mask",
            "denoise",
            "convolve",
            "filter",
            "dehum",
            "vst3",
            "declick",
        }:
            v["support"] = (
                "nominal affine position; processing mixes neighboring samples; no pointwise inverse"
            )
        result.append(v)
    return result


def chain(x, fs, effects, maps, plugin):
    notes = []
    for effect in effects:
        before = len(x)
        x, fs, current = editing.apply(x, fs, [effect], plugin)
        maps = remap(maps, effect, before, len(x))
        notes.extend(current)
    return x, fs, maps, notes


def materialize(x, fs, recipe, parents, maps, notes, root, stage, limits, progress):
    x = editing.checked(x)
    directory = root / "renders"
    directory.mkdir(exist_ok=True)
    temporary = stage / "render.wav"
    # Canonical IEEE-float RIFF: libsndfile can add a timestamped PEAK chunk.
    data = np.ascontiguousarray(x, dtype="<f8")
    channels = x.shape[1]
    size = data.nbytes
    with temporary.open("wb") as f:
        f.write(
            struct.pack(
                "<4sI4s4sIHHIIHH4sII4sI",
                b"RIFF",
                48 + size,
                b"WAVE",
                b"fmt ",
                16,
                3,
                channels,
                fs,
                fs * channels * 8,
                channels * 8,
                64,
                b"fact",
                4,
                len(x),
                b"data",
                size,
            )
        )
        f.write(memoryview(data).cast("B"))
    digest = digest_file(temporary)
    key = cache_key(
        {
            "recipe": recipe,
            "parents": parents,
            "sample_rate": fs,
            "libraries": versions(),
            "engine_contract": 1,
            "output_sha256": digest,
        }
    )
    destination = directory / (digest + ".wav")
    if destination.exists():
        if digest_file(destination) != digest:
            raise IntegrityError("Rendered source hash conflict.")
        temporary.unlink()
    else:
        if (
            sum(p.stat().st_size for p in directory.glob("*.wav")) + temporary.stat().st_size
            > limits["decoded_bytes"]
        ):
            raise ResourceLimit("Rendered source storage budget exceeded.")
        Cache(root / "cache", limits["cache_bytes"]).admit(x.nbytes * 2)
        os.replace(temporary, destination)
    asset = import_audio(str(destination), None, root, stage / "import", limits, progress)
    asset["name"] = recipe.get("name", "Rendered audio")
    asset.update(
        render_identity=key, asset_id="asset-render-" + key, original_revision_id="rev-render-" + key
    )
    node = {
        "version": 1,
        "recipe_id": key,
        "revision_id": asset["original_revision_id"],
        "parents": parents,
        "recipe": recipe,
        "mappings": maps,
        "notes": notes,
        "output_sha256": digest,
        "libraries": versions(),
        "peak_fs": float(np.max(np.abs(x))),
        "float_headroom": True,
    }
    if len(json.dumps(node, allow_nan=False)) > 2 * 1024**2:
        raise ResourceLimit("Rendered recipe/state metadata exceeds the 2 MiB result budget.")
    return {"asset": asset, "node": node}


def run(payload, root, stage, limits, progress):
    request = read_request(payload, root)
    spec = request["recipe"]
    assets = request.get("assets", {})
    if payload["mode"] == "generate":
        x, fs, meta = generation.generate(spec)
        return materialize(
            x, fs, {"type": "generate", "parameters": spec}, [], [], [meta], root, stage, limits, progress
        )
    if payload["mode"] == "calibrate":
        item = request["source"]
        read, n, c, fs = reader(root, item["asset"], item["region"])
        if item["region"]["channels"] != [spec.get("chain", {}).get("input_channel")]:
            raise ValidationError("Calibration channel differs from the acquisition chain.")
        profile = acoustics.calibrate(editing.checked(read(0, n)), fs, spec)
        profile["source_sha256"] = item["asset"]["sha256"]
        profile.pop("calibration_id")
        profile["calibration_id"] = "cal-" + cache_key(profile)
        return profile
    if (
        set(spec) - {"version", "sample_rate", "channels", "tracks", "effects", "name"}
        or spec.get("version") != 1
    ):
        raise ValidationError("Unsupported timeline fields/version.")
    fs = integer_value(spec, "sample_rate", 48000, 8000, 384000)
    c = integer_value(spec, "channels", 2, 1, 32)
    tracks = spec.get("tracks")
    if not isinstance(tracks, list) or not 1 <= len(tracks) <= 32:
        raise ValidationError("Timeline needs 1–32 tracks.")
    out = None
    maps = []
    parents = []
    notes = []
    clips_seen = 0
    from .vst3 import process as plugin_process

    def plugin(x, rate, p):
        return plugin_process(x, rate, p, root, stage)

    for index, track in enumerate(tracks):
        if set(track) - {"name", "gain_db", "mute", "solo", "clips", "effects"}:
            raise ValidationError("Unknown track field.")
        if type(track.get("mute", False)) is not bool or type(track.get("solo", False)) is not bool:
            raise ValidationError("Track mute/solo must be Boolean.")
        clips = track.get("clips", [])
        if not isinstance(clips, list):
            raise ValidationError("Track clips must be a list.")
        track_x = np.zeros((1, c))
        tm = []
        for clip in clips:
            clips_seen += 1
            if clips_seen > 256 or set(clip) - {"region", "at", "effects", "name"}:
                raise ValidationError("Invalid clip or more than 256 clips.")
            r = clip["region"]
            asset = assets.get(r["revision_id"])
            if asset is None:
                raise ValidationError("Unknown clip revision.")
            read, n, cc, rate = reader(root, asset, r)
            if rate != fs:
                raise ValidationError("Source rate differs from timeline; render a resampled revision first.")
            if n * cc > editing.MAX_SAMPLES:
                raise ResourceLimit("Clip exceeds working budget.")
            x = read(0, n)
            m = [
                {
                    "parent_revision": r["revision_id"],
                    "input_start": r["start_frame"],
                    "input_end": r["end_frame"],
                    "input_channels": r["channels"],
                    "output_start": 0,
                    "output_end": n,
                    "track": index,
                    "support": "exact affine timeline coordinates; mixing can be many-to-one",
                }
            ]
            x, rate, m, info = chain(x, rate, clip.get("effects", []), m, plugin)
            if rate != fs or x.shape[1] != c:
                raise ValidationError(
                    "Clip effects must produce timeline sample rate and channel count. Use route for channel changes."
                )
            offset = integer_value(clip, "at", 0, 0, editing.MAX_SAMPLES // c)
            if (offset + len(x)) * c > editing.MAX_SAMPLES:
                raise ResourceLimit("Timeline exceeds working budget.")
            for v in m:
                v["output_start"] += offset
                v["output_end"] += offset
            tm.extend(m)
            if offset + len(x) > len(track_x):
                grown = np.zeros((offset + len(x), c))
                grown[: len(track_x)] = track_x
                track_x = grown
            track_x[offset : offset + len(x)] += x
            notes.extend(info)
            if r["revision_id"] not in parents:
                parents.append(r["revision_id"])
        track_x, rate, tm, info = chain(track_x, fs, track.get("effects", []), tm, plugin)
        if rate != fs or track_x.shape[1] != c:
            raise ValidationError("Track effects cannot change timeline rate/layout.")
        track_x *= 10 ** (number_value(track, "gain_db", 0, -120, 36) / 20)
        audible = not track.get("mute", False) and (
            not any(t.get("solo", False) for t in tracks) or track.get("solo", False)
        )
        if audible:
            maps.extend(tm)
            if out is None:
                out = np.zeros_like(track_x)
            if len(track_x) > len(out):
                grown = np.zeros_like(track_x)
                grown[: len(out)] = out
                out = grown
            out[: len(track_x)] += track_x
        notes.extend(info)
        progress("render.tracks", (index + 1) / len(tracks))
    if out is None:
        raise ValidationError("No audible track.")
    out, fs, maps, info = chain(out, fs, spec.get("effects", []), maps, plugin)
    notes.extend(info)
    return materialize(out, fs, spec, parents, maps, notes, root, stage, limits, progress)
