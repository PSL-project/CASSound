"""Local media adapter, bounded decode, exact native PCM and display envelopes."""

from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import numpy as np
import soundfile as sf

from ..domain.errors import BackendUnavailable, ValidationError, IntegrityError, ResourceLimit, CASSoundError
from ..domain.units import normalized
from .cache import Cache, cache_key, digest_file, atomic_json, mapped_array

BLOCK = 65536
FORMAT_ALLOWLIST = "wav,flac,aiff,mp3,mov,matroska,webm,ogg,asf,aac,avi,ape,wv,au,caf"


def backend_path(name, required=True):
    configured = os.environ.get("CASSOUND_" + name.upper())
    bundled = (
        Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
        / "backends"
        / (name + (".exe" if os.name == "nt" else ""))
    )
    value = configured or (str(bundled) if bundled.is_file() else shutil.which(name))
    if value and Path(value).is_file():
        return str(Path(value).resolve())
    if required:
        raise BackendUnavailable(
            f"{name} is required for this format. Install it or set CASSOUND_{name.upper()}."
        )
    return None


def local_file(path):
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise ValidationError(f"Local media file not found: {file}")
    return file


def process(args, output=None, max_bytes=8 * 1024**3, timeout=600):
    # External system tools must not inherit PyInstaller's private dynamic-library path.
    env = os.environ.copy()
    if getattr(sys, "frozen", False) and os.name != "nt":
        key = "LD_LIBRARY_PATH"
        if key + "_ORIG" in env:
            env[key] = env[key + "_ORIG"]
        else:
            env.pop(key, None)
    creation = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # stdout is bounded for probes; decode tools write to disk instead.
    import tempfile

    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen(args, stdout=out, stderr=err, env=env, **creation)
        start = time.monotonic()
        try:
            while child.poll() is None:
                if output and Path(output).exists() and Path(output).stat().st_size > max_bytes:
                    raise ResourceLimit("Decoded audio exceeds the configured disk bound.")
                if time.monotonic() - start > timeout:
                    raise CASSoundError("Media tool timed out.", code="timeout")
                if out.tell() > 8 * 1024**2 or err.tell() > 2 * 1024**2:
                    raise ResourceLimit("Media diagnostics exceeded their output bound.")
                time.sleep(0.02)
            if output and Path(output).stat().st_size > max_bytes:
                raise ResourceLimit("Decoded audio exceeds the configured disk bound.")
            if child.returncode:
                err.seek(0)
                detail = err.read(4096).decode("utf-8", "replace")
                raise CASSoundError(f"Media tool rejected the file: {detail}", code="decode_failed")
            out.seek(0)
            return out.read(8 * 1024**2)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


def probe(path):
    path = local_file(path)
    tool = backend_path("ffprobe", required=False)
    if tool:
        result = json.loads(
            process(
                [
                    tool,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    FORMAT_ALLOWLIST,
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout=30,
            )
        )
        streams = result.get("streams", [])
        for stream in streams:
            if stream.get("codec_type") == "audio":
                if not (
                    0 < int(stream.get("sample_rate", 0)) <= 768000
                    and 0 < int(stream.get("channels", 0)) <= 128
                ):
                    raise ValidationError("Unsupported sample rate or channel count.")
        result["probe_backend"] = process([tool, "-version"], timeout=10).decode().splitlines()[0]
        return result
    try:
        info = sf.info(path)
    except (RuntimeError, OSError) as exc:
        raise BackendUnavailable("Install FFmpeg/ffprobe to inspect this media format.") from exc
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": info.subtype,
                "sample_rate": str(info.samplerate),
                "channels": info.channels,
                "duration": str(info.duration),
                "start_time": "0",
                "time_base": f"1/{info.samplerate}",
            }
        ],
        "format": {"filename": str(path), "format_name": info.format, "duration": str(info.duration)},
        "probe_backend": "libsndfile " + sf.__libsndfile_version__,
    }


def specification(subtype):
    return {
        "PCM_U8": ("uint8", "unsigned", 8),
        "PCM_S8": ("int8", "signed", 8),
        "PCM_16": ("int16", "signed", 16),
        "PCM_24": ("int32", "signed", 24),
        "PCM_32": ("int32", "signed", 32),
        "FLOAT": ("float32", "float", 32),
        "DOUBLE": ("float64", "float", 64),
    }.get(subtype, ("float64", "float", 64))


def native_block(file, count, subtype):
    if subtype == "PCM_U8":
        return ((file.read(count, dtype="int16", always_2d=True).astype("int32") >> 8) + 128).astype("uint8")
    if subtype == "PCM_S8":
        return (file.read(count, dtype="int16", always_2d=True) >> 8).astype("int8")
    if subtype == "PCM_16":
        return file.read(count, dtype="int16", always_2d=True)
    if subtype in {"PCM_24", "PCM_32"}:
        data = file.read(count, dtype="int32", always_2d=True)
        return data >> 8 if subtype == "PCM_24" else data
    return file.read(count, dtype="float32" if subtype == "FLOAT" else "float64", always_2d=True)


def import_audio(path, stream_index, root, stage, limits, progress):
    from .locking import cache_write_lock

    with cache_write_lock(root):
        return _import_audio(path, stream_index, root, stage, limits, progress)


def _import_audio(path, stream_index, root, stage, limits, progress):
    stage.mkdir(parents=True, exist_ok=True)
    path = local_file(path)
    metadata = probe(path)
    streams = [s for s in metadata["streams"] if s.get("codec_type") == "audio"]
    if not streams:
        raise ValidationError("The file contains no audio stream.")
    if stream_index is None:
        if len(streams) != 1:
            raise ValidationError("Multiple audio streams found; select an absolute stream index.")
        selected = streams[0]
    else:
        selected = next((s for s in streams if s["index"] == stream_index), None)
        if selected is None:
            raise ValidationError("Selected stream is not an audio stream in this container.")
    index = selected["index"]
    digest = digest_file(path, progress)
    source = path
    decoder = "libsndfile " + sf.__libsndfile_version__
    try:
        info = sf.info(path)
        if len(metadata["streams"]) != 1:
            raise RuntimeError("Container requires explicit stream decoding")
    except (RuntimeError, OSError):
        tool = backend_path("ffmpeg")
        decoder = process([tool, "-version"], timeout=10).decode().splitlines()[0]
        source = stage / "decoded.wav"
        progress("decode", 0)
        process(
            [
                tool,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                FORMAT_ALLOWLIST,
                "-i",
                str(path),
                "-map",
                f"0:{index}",
                "-vn",
                "-sn",
                "-dn",
                "-c:a",
                "pcm_f64le",
                "-rf64",
                "auto",
                str(source),
            ],
            output=source,
            max_bytes=limits.get("decoded_bytes", 8 * 1024**3),
        )
        info = sf.info(source)
    if info.frames <= 0 or info.samplerate <= 0 or not 1 <= info.channels <= 128:
        raise ValidationError("Audio stream has no valid decodable samples.")
    dtype, kind, bits = specification(info.subtype)
    key = cache_key(
        {"source": digest, "stream": index, "decoder": decoder, "sample_cache_version": 1, "dtype": dtype}
    )
    cache = Cache(root / "cache", limits.get("cache_bytes", 20 * 1024**3))
    target = cache.path(key)
    if target.is_dir() and (target / "asset.json").is_file():
        # A cache hit still verifies source identity; locator may have changed.
        obj = json.loads((target / "asset.json").read_text())
        obj["locator"] = str(path)
        obj["name"] = path.name
        return obj
    size = info.frames * info.channels * np.dtype(dtype).itemsize
    if size > limits.get("decoded_bytes", 8 * 1024**3):
        raise ResourceLimit("Audio sample cache exceeds the per-file decode limit.")
    cache.admit(size * 1.2 + 1024**2)
    output = stage / "cache-entry"
    output.mkdir()
    with ExitStack() as mappings:
        data = mappings.enter_context(
            mapped_array(output / "samples.npy", mode="w+", dtype=dtype, shape=(info.frames, info.channels))
        )
        envelopes = mappings.enter_context(
            mapped_array(
                output / "envelope-256.npy",
                mode="w+",
                dtype="float64",
                shape=(math.ceil(info.frames / 256), info.channels, 2),
            )
        )
        with sf.SoundFile(source) as file:
            for start in range(0, info.frames, BLOCK):
                block = native_block(file, min(BLOCK, info.frames - start), info.subtype)
                if not len(block):
                    raise IntegrityError("Unexpected end of decoded stream.")
                data[start : start + len(block)] = block
                values = normalized(block, kind, bits)
                if not np.isfinite(values).all():
                    raise ValidationError("Audio contains non-finite sample values.")
                for off in range(0, len(values), 256):
                    part = values[off : off + 256]
                    envelopes[(start + off) // 256] = np.stack([part.min(axis=0), part.max(axis=0)], axis=-1)
                progress("cache", (start + len(block)) / info.frames)
        if digest_file(path) != digest:
            raise IntegrityError("Source changed during import; no asset was registered.")
        level = envelopes
        step = 256
        while len(level) > 1:
            next_level = mappings.enter_context(
                mapped_array(
                    output / f"envelope-{step * 2}.npy",
                    mode="w+",
                    dtype="float64",
                    shape=(math.ceil(len(level) / 2), info.channels, 2),
                )
            )
            for offset in range(0, len(next_level), BLOCK):
                end = min(offset + BLOCK, len(next_level))
                first = level[2 * offset : min(2 * end, len(level)) : 2]
                second = level[2 * offset + 1 : min(2 * end, len(level)) : 2]
                if len(second) < len(first):
                    second = np.concatenate([second, first[-1:]], axis=0)
                next_level[offset:end, :, 0] = np.minimum(first[:, :, 0], second[:, :, 0])
                next_level[offset:end, :, 1] = np.maximum(first[:, :, 1], second[:, :, 1])
            level = next_level
            step *= 2
    # Every writable mapping is flushed and closed before publishing the directory.
    ident = cache_key({"source": digest, "stream": index})
    obj = {
        "name": path.name,
        "asset_id": "asset-" + ident,
        "sha256": digest,
        "locator": str(path),
        "stream_index": index,
        "original_revision_id": "rev-" + ident,
        "frames": info.frames,
        "sample_rate": info.samplerate,
        "channels": info.channels,
        "native_dtype": dtype,
        "sample_kind": kind,
        "valid_bits": bits,
        "decoder": decoder,
        "timestamp_origin": selected.get("start_time", "0"),
        "time_base": selected.get("time_base", f"1/{info.samplerate}"),
        "probe": metadata,
        "cache_key": key,
    }
    atomic_json(output / "asset.json", obj)
    try:
        os.rename(output, target)
    except OSError:
        if not (target / "asset.json").is_file():
            raise
        shutil.rmtree(output)
    return obj


def envelope(payload, root):
    asset = payload["asset"]
    start, end, channels, pixels = payload["start"], payload["end"], payload["channels"], payload["pixels"]
    if not (
        0 <= start < end <= asset["frames"]
        and 1 <= pixels <= 8192
        and channels
        and all(type(c) is int and 0 <= c < asset["channels"] for c in channels)
    ):
        raise ValidationError("Invalid envelope viewport.")
    path = Cache(root / "cache").path(asset["cache_key"])
    pixels = min(pixels, max(1, 32768 // len(channels)))
    step = 256
    available = sorted(int(p.stem.split("-")[1]) for p in path.glob("envelope-*.npy"))
    wanted = max(1, (end - start) // pixels)
    eligible = [s for s in available if s <= wanted]
    if eligible:
        step = max(eligible)
        raw = np.load(path / f"envelope-{step}.npy", mmap_mode="r", allow_pickle=False)
        first, last = start // step, math.ceil(end / step)
        selected = raw[first:last, channels, :]
        times = np.arange(first, last) * step / asset["sample_rate"]
    else:
        raw = np.load(path / "samples.npy", mmap_mode="r", allow_pickle=False)
        values = normalized(raw[start:end, channels], asset["sample_kind"], asset["valid_bits"])
        step = max(1, math.ceil(len(values) / pixels))
        selected = np.asarray(
            [
                np.stack([values[i : i + step].min(axis=0), values[i : i + step].max(axis=0)], axis=-1)
                for i in range(0, len(values), step)
            ]
        )
        times = np.arange(start, end, step) / asset["sample_rate"]
    return {
        "time": times.tolist(),
        "minimum": selected[:, :, 0].tolist(),
        "maximum": selected[:, :, 1].tolist(),
        "step_frames": step,
        "revision_id": payload["revision_id"],
    }


def export_audio(payload, root, stage, limits, progress):
    asset, spec = payload["asset"], payload["spec"]
    destination = Path(spec["path"]).expanduser().resolve()
    if destination == Path(asset["locator"]).resolve():
        raise ValidationError("Export cannot overwrite an original source.")
    if destination.exists() and not spec.get("overwrite"):
        raise ValidationError("Destination exists. Choose another path or explicitly enable overwrite.")
    start, end = spec.get("start_frame", 0), spec.get("end_frame") or asset["frames"]
    if not (type(start) is int and type(end) is int and 0 <= start < end <= asset["frames"]):
        raise ValidationError("Invalid export frame bounds.")
    fmt, subtype = spec.get("format", "WAV").upper(), spec.get("subtype", "PCM_24").upper()
    if not sf.check_format(fmt, subtype):
        raise ValidationError("Unsupported format/subtype pair. Inspect supported libsndfile formats.")
    raw = np.load(
        Cache(root / "cache").path(asset["cache_key"]) / "samples.npy", mmap_mode="r", allow_pickle=False
    )
    output = stage / "audio-export"
    with sf.SoundFile(
        output,
        mode="w",
        samplerate=asset["sample_rate"],
        channels=asset["channels"],
        format=fmt,
        subtype=subtype,
    ) as file:
        for pos in range(start, end, BLOCK):
            chunk = raw[pos : min(pos + BLOCK, end)]
            # Bit-exact PCM_24/32 export avoids floating arithmetic and libsndfile alignment ambiguity.
            if asset["sample_kind"] == "signed" and subtype == f"PCM_{asset['valid_bits']}":
                chunk = chunk.astype("int32") << (32 - asset["valid_bits"])
            else:
                chunk = normalized(chunk, asset["sample_kind"], asset["valid_bits"])
                if subtype.startswith("PCM_") and (np.any(chunk < -1) or np.any(chunk >= 1)):
                    raise ValidationError(
                        "Integer export would clip. Adjust gain or choose floating-point output."
                    )
            file.write(chunk)
            progress("export", (min(pos + BLOCK, end) - start) / (end - start))
    return {
        "staged_path": str(output),
        "destination": str(destination),
        "frames": end - start,
        "format": fmt,
        "subtype": subtype,
        "sha256": digest_file(output),
        "overwrite": spec.get("overwrite", False),
    }


def extract_stream(payload, root, stage, limits, progress):
    source = local_file(payload["path"])
    destination = Path(payload["destination"]).expanduser().resolve()
    if source == destination:
        raise ValidationError("Stream extraction cannot overwrite its original.")
    metadata = probe(source)
    streams = [item for item in metadata["streams"] if item.get("codec_type") == "audio"]
    index = payload.get("stream_index")
    if index is None and len(streams) == 1:
        index = streams[0]["index"]
    if not any(s["index"] == index for s in streams):
        raise ValidationError("Select an absolute audio-stream index for extraction.")
    if not destination.suffix:
        raise ValidationError("Extraction destination requires a container extension such as .mka or .wav.")
    output = stage / ("extracted" + destination.suffix)
    tool = backend_path("ffmpeg")
    progress("stream-copy", 0)
    process(
        [
            tool,
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            FORMAT_ALLOWLIST,
            "-i",
            str(source),
            "-map",
            f"0:{index}",
            "-vn",
            "-sn",
            "-dn",
            "-c:a",
            "copy",
            str(output),
        ],
        output=output,
        max_bytes=limits.get("decoded_bytes", 8 * 1024**3),
    )
    return {
        "staged_path": str(output),
        "destination": str(destination),
        "stream_index": index,
        "method": "stream-copy",
        "sha256": digest_file(output),
        "overwrite": bool(payload.get("overwrite", False)),
    }
