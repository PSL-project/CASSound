"""Device-free transport and capture contract tests; hardware qualification remains separate."""

import time
import numpy as np
import pytest
import soundfile as sf
from cassound.runtime.playback import Transport, Recorder, devices
from cassound.domain.errors import CASSoundError, ValidationError


class FakeStream:
    latency = 0.01

    def __init__(self, owner, **options):
        self.owner = owner
        self.options = options
        self.closed = False

    def start(self):
        pass

    def stop(self):
        pass

    def abort(self):
        self.owner.aborts += 1

    def close(self):
        self.closed = True

    def write(self, x):
        self.owner.written.append(x.copy())
        if self.owner.disconnect:
            raise OSError("device disconnected")
        if self.owner.slow:
            time.sleep(0.01)
        return self.owner.overflow

    def read(self, n):
        if self.owner.disconnect:
            raise OSError("device disconnected")
        if self.owner.slow:
            time.sleep(0.01)
        return np.ones((n, self.options["channels"]), dtype=np.float32) * 0.1, self.owner.overflow


class FakeAudio:
    def __init__(self):
        self.written = []
        self.aborts = 0
        self.disconnect = False
        self.slow = False
        self.overflow = False
        self.streams = []

    def OutputStream(self, **kwargs):
        obj = FakeStream(self, **kwargs)
        self.streams.append(obj)
        return obj

    InputStream = OutputStream

    def query_devices(self, device=None):
        value = {
            "name": "Test device",
            "hostapi": 0,
            "max_input_channels": 2,
            "max_output_channels": 2,
            "default_samplerate": 48000,
        }
        return value if device is not None else [value]

    def query_hostapis(self, index=None):
        return {"name": "TestAPI"} if index is not None else [{"name": "TestAPI"}]


def prepared(tmp_path, n=1000):
    x = np.linspace(-0.5, 0.5, n, dtype=np.float32)[:, None]
    path = tmp_path / "audio.npy"
    np.save(path, x)
    return {"path": str(path), "frames": n, "channels": 1, "sample_rate": 8000}, x


def test_transport_exact_selection_blocks_cursor_and_device_failure(tmp_path):
    p, x = prepared(tmp_path)
    sd = FakeAudio()
    t = Transport(p, sd=sd, gain_db=0, block_frames=128).start()
    assert t.wait() == 1000
    np.testing.assert_array_equal(np.concatenate(sd.written), x)
    assert t.state == "finished" and sd.streams[0].closed
    sd = FakeAudio()
    sd.disconnect = True
    t = Transport(p, sd=sd).start()
    with pytest.raises(CASSoundError, match="disconnected"):
        t.wait()
    assert t.state == "failed"
    recovered = Transport(p, sd=FakeAudio(), gain_db=0).start()
    assert recovered.wait() == 1000


def test_loop_seek_stop_and_underflow_count(tmp_path):
    p, x = prepared(tmp_path, 64)
    sd = FakeAudio()
    sd.slow = True
    sd.overflow = True
    t = Transport(p, sd=sd, loop=True, gain_db=0, block_frames=16).start()
    time.sleep(0.03)
    t.seek(32)
    time.sleep(0.03)
    t.stop()
    assert t.state == "stopped" and sd.aborts >= 1 and t.underflows > 0
    assert any(np.array_equal(block, x[32:48]) for block in sd.written)
    with pytest.raises(ValidationError):
        t.seek(64)


def test_record_bounded_exact_length_overflow_partial_and_recovery(tmp_path):
    sd = FakeAudio()
    r = Recorder(
        tmp_path / "capture.wav", sd=sd, sample_rate=8000, channels=2, duration_seconds=0.125
    ).start()
    r.wait()
    x, fs = sf.read(r.path)
    assert len(x) == 1000 and x.shape[1] == 2 and fs == 8000 and r.state == "finished"
    assert not r.partial.exists()
    with pytest.raises(ValidationError):
        Recorder(r.path, sd=sd)
    sd = FakeAudio()
    sd.overflow = True
    r = Recorder(tmp_path / "overflow.wav", sd=sd, sample_rate=8000, duration_seconds=0.1).start()
    with pytest.raises(CASSoundError, match="overflow"):
        r.wait()
    assert not r.path.exists() and r.partial.exists()
    sd = FakeAudio()
    sd.disconnect = True
    r = Recorder(tmp_path / "failure.wav", sd=sd, duration_seconds=0.1).start()
    with pytest.raises(CASSoundError, match="disconnected"):
        r.wait()
    assert r.state == "failed"
    recovered = Recorder(
        tmp_path / "recovered.wav", sd=FakeAudio(), sample_rate=8000, duration_seconds=0.1
    ).start()
    recovered.wait()
    assert recovered.frames == 800


def test_device_inventory_never_invents_hostapi_or_loopback():
    info = devices(FakeAudio())
    assert not info["wasapi"] and not info["asio"] and not info["loopback"]
