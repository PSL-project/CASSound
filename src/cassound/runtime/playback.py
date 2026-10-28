"""Blocking PortAudio I/O on feeder threads. No file I/O or DSP in an audio callback."""

import importlib
from pathlib import Path
import threading
from uuid import uuid4
import numpy as np
from ..domain.errors import BackendUnavailable, ValidationError, ResourceLimit, CASSoundError


def backend():
    try:
        return importlib.import_module("sounddevice")
    except (ImportError, OSError) as exc:
        raise BackendUnavailable("Install cassound[audio] and a working PortAudio backend.") from exc


def devices(sd=None):
    sd = sd or backend()
    hosts = sd.query_hostapis()
    entries = []
    for i, d in enumerate(sd.query_devices()):
        entries.append(
            {
                "index": i,
                "name": d["name"],
                "host_api": hosts[d["hostapi"]]["name"],
                "input_channels": d["max_input_channels"],
                "output_channels": d["max_output_channels"],
                "default_sample_rate": d["default_samplerate"],
            }
        )
    return {
        "devices": entries,
        "wasapi": any("WASAPI" in x["host_api"] for x in entries),
        "asio": any("ASIO" in x["host_api"] for x in entries),
        "loopback": False,
        "loopback_reason": "This blocking-stream backend has no explicit loopback-capture implementation. An OS-provided recording endpoint can be selected by name.",
        "device_identity": "indices are session-local; reselect if device inventory changes",
    }


def settings(sd, device, exclusive):
    if not exclusive:
        return None
    info = sd.query_devices(device)
    host = sd.query_hostapis(info["hostapi"])
    if "WASAPI" not in host["name"]:
        raise ValidationError("Exclusive mode requires an actual WASAPI device.")
    return sd.WasapiSettings(exclusive=True)


def prepare(payload, root, stage, limits, progress):
    from .analysis import reader
    from .cache import cache_key, Cache

    read, n, c, fs = reader(root, payload["asset"], payload["region"])
    size = n * c * 4
    if size > limits["decoded_bytes"]:
        raise ResourceLimit("Audition spool exceeds decoded byte budget.")
    key = cache_key({"playback": payload, "version": 1})
    directory = Cache(root / "cache", limits["cache_bytes"]).path(key)
    from .locking import cache_write_lock

    with cache_write_lock(root):
        if not (directory / "audio.npy").is_file():
            Cache(root / "cache", limits["cache_bytes"]).admit(size + 1024)
            spool = stage / "audio.npy"
            out = np.lib.format.open_memmap(spool, mode="w+", dtype="float32", shape=(n, c))
            peak = 0
            for a in range(0, n, 65536):
                x = read(a, min(n, a + 65536))
                peak = max(peak, float(np.max(np.abs(x))))
                out[a : a + len(x)] = x
                progress("audio.prepare", min(n, a + 65536) / n)
            out.flush()
            del out
            if peak > 1:
                raise ValidationError("Audition would clip; reduce gain or normalize a derived revision.")
            directory.mkdir(exist_ok=True)
            spool.replace(directory / "audio.npy")
    return {
        "path": str(directory / "audio.npy"),
        "sample_rate": fs,
        "frames": n,
        "channels": c,
        "region": payload["region"],
    }


class Transport:
    """Prepared-audio audition. Cursor counts submitted frames, not a hardware timestamp."""

    def __init__(
        self, prepared, *, device=None, exclusive=False, loop=False, gain_db=-12, sd=None, block_frames=512
    ):
        if type(loop) is not bool or not np.isfinite(gain_db) or not -120 <= gain_db <= 0:
            raise ValidationError("Audition gain must be −120..0 dB; loop Boolean.")
        if type(block_frames) is not int or not 16 <= block_frames <= 8192:
            raise ValidationError("Invalid playback block size.")
        self.sd = sd or backend()
        self.prepared = prepared
        self.device = device
        self.exclusive = exclusive
        self.loop = loop
        self.gain = 10 ** (gain_db / 20)
        self.block_frames = block_frames
        self.state = "ready"
        self.error = None
        self.underflows = 0
        self.submitted_frame = 0
        self.latency_seconds = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._seek = None
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            raise ValidationError("Transport is already running.")
        self._stop.clear()
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def seek(self, frame):
        if type(frame) is not int or not 0 <= frame < self.prepared["frames"]:
            raise ValidationError("Seek frame exceeds prepared audio.")
        if self._thread is None or not self._thread.is_alive():
            self.submitted_frame = frame
            self.start()
            return
        with self._lock:
            self._seek = frame

    def _run(self):
        stream = None
        try:
            x = np.load(self.prepared["path"], mmap_mode="r", allow_pickle=False)
            if x.shape != (self.prepared["frames"], self.prepared["channels"]) or x.dtype != np.float32:
                raise ValidationError("Invalid audition spool.")
            stream = self.sd.OutputStream(
                samplerate=self.prepared["sample_rate"],
                channels=x.shape[1],
                dtype="float32",
                device=self.device,
                blocksize=self.block_frames,
                extra_settings=settings(self.sd, self.device, self.exclusive),
            )
            stream.start()
            self.latency_seconds = float(stream.latency)
            self.state = "playing"
            pos = self.submitted_frame
            while not self._stop.is_set():
                with self._lock:
                    seek = self._seek
                    self._seek = None
                if seek is not None:
                    stream.abort()
                    stream.start()
                    pos = seek
                if pos >= len(x):
                    if not self.loop:
                        break
                    pos = 0
                end = min(len(x), pos + self.block_frames)
                # Blocking write is on this feeder thread; finite selection never auto-loops.
                self.underflows += int(
                    bool(stream.write(np.asarray(x[pos:end] * self.gain, dtype="float32")))
                )
                pos = end
                self.submitted_frame = pos
            self.state = "stopped" if self._stop.is_set() else "finished"
        except Exception as exc:
            self.error = str(exc)
            self.state = "failed"
        finally:
            if stream:
                try:
                    if self._stop.is_set():
                        stream.abort()
                    else:
                        stream.stop()
                except Exception:
                    pass
                stream.close()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._thread and self._thread.is_alive():
            self.state = "failed"
            self.error = "Audio driver did not return from blocking I/O; restart the audio device/session."

    def wait(self, timeout=30):
        if self._thread:
            self._thread.join(timeout)
        if self._thread and self._thread.is_alive():
            raise TimeoutError("Transport still running.")
        if self.error:
            raise CASSoundError(self.error, code="audio_device_failed")
        return self.submitted_frame


class Recorder:
    """Explicit bounded capture to a distinct WAV. Failed capture is kept as a labeled partial file."""

    def __init__(
        self,
        path,
        *,
        sample_rate=48000,
        channels=1,
        duration_seconds=30,
        device=None,
        exclusive=False,
        sd=None,
        max_bytes=512 * 1024**2,
    ):
        if (
            type(sample_rate) is not int
            or not 8000 <= sample_rate <= 384000
            or type(channels) is not int
            or not 1 <= channels <= 32
            or not np.isfinite(duration_seconds)
            or not 0 < duration_seconds <= 3600
        ):
            raise ValidationError("Invalid bounded recording specification.")
        self.frames_limit = round(sample_rate * duration_seconds)
        if self.frames_limit * channels * 4 > max_bytes:
            raise ResourceLimit("Recording exceeds byte budget.")
        self.path = Path(path).resolve()
        if self.path.exists():
            raise ValidationError("Recording destination already exists.")
        self.partial = self.path.with_name(self.path.name + "." + uuid4().hex + ".partial.wav")
        self.sd = sd or backend()
        self.fs = sample_rate
        self.channels = channels
        self.device = device
        self.exclusive = exclusive
        self.state = "ready"
        self.error = None
        self.overflows = 0
        self.frames = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.state != "ready":
            raise ValidationError("Recorder is single-use.")
        self.state = "starting"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        import soundfile as sf
        import os

        stream = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.sd.InputStream(
                samplerate=self.fs,
                channels=self.channels,
                dtype="float32",
                device=self.device,
                blocksize=512,
                extra_settings=settings(self.sd, self.device, self.exclusive),
            )
            stream.start()
            self.state = "recording"
            with sf.SoundFile(
                self.partial, "w", samplerate=self.fs, channels=self.channels, format="WAV", subtype="FLOAT"
            ) as f:
                while self.frames < self.frames_limit and not self._stop.is_set():
                    x, overflow = stream.read(min(512, self.frames_limit - self.frames))
                    self.overflows += int(overflow)
                    if x.ndim != 2 or x.shape[1] != self.channels or not np.isfinite(x).all():
                        raise ValidationError("Invalid data from recording device.")
                    f.write(x)
                    self.frames += len(x)
            if not self.frames:
                raise ValidationError("Recording contains no frames.")
            if self.overflows:
                raise CASSoundError(
                    "Capture overflow: discontinuous recording retained as partial.", code="capture_overflow"
                )
            os.link(self.partial, self.path)
            self.partial.unlink()
            self.state = "finished"
        except Exception as exc:
            self.state = "failed"
            self.error = str(exc)
        finally:
            if stream:
                try:
                    stream.stop()
                except Exception:
                    pass
                stream.close()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def wait(self, timeout=30):
        if self._thread:
            self._thread.join(timeout)
        if self._thread and self._thread.is_alive():
            raise TimeoutError("Capture still running.")
        if self.error:
            raise CASSoundError(self.error, code="capture_failed")
        return self.path
