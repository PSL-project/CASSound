import numpy as np
import pytest
from scipy import signal
from cassound.engine import editing as e, generation as g, acoustics as a
from cassound.domain.errors import ValidationError, ResourceLimit


def source(x, fs=48000):
    if x.ndim == 1:
        x = x[:, None]
    return lambda i, j: x[i:j], len(x), x.shape[1], fs


def test_gain_automation_fades_routing_and_headroom():
    x = np.ones((16, 2)) * 0.5
    y, fs, _ = e.apply(x, 48000, [{"type": "gain", "db": 12}])
    assert y.max() > 1 and np.array_equal(x, np.ones((16, 2)) * 0.5)
    y, _, _ = e.apply(
        x,
        48000,
        [
            {"type": "gain", "automation": [[0, -6], [15, 0]]},
            {"type": "fade", "in_frames": 4, "out_frames": 4},
            {"type": "route", "matrix": [[1], [1]]},
        ],
    )
    assert y.shape == (16, 1) and y[0] == 0 and y[-1] == 0
    expected = 10 ** (np.interp(np.arange(16), [0, 15], [-6, 0]) / 20)
    assert y[5, 0] == pytest.approx(expected[5])
    y, _, _ = e.apply(x, 48000, [{"type": "normalize", "target_db": -3}])
    assert y.max() == pytest.approx(10 ** (-3 / 20))


def test_cut_split_paste_reverse_silence():
    clip = {"region": {"revision_id": "r", "start_frame": 10, "end_frame": 30, "channels": [0]}, "at": 0}
    left, r = e.split_clip(clip, 20)
    assert left["region"]["end_frame"] == 20 and r["at"] == 10
    clips = e.cut_clip(clip, 15, 25)
    assert clips[1]["at"] == 5 and clips[1]["region"]["start_frame"] == 25
    x = np.arange(20)[:, None] / 20
    y, _, _ = e.apply(
        x,
        8000,
        [
            {"type": "reverse"},
            {"type": "trim", "start_frame": 2, "end_frame": 12},
            {"type": "silence", "start_frame": 3, "end_frame": 5},
        ],
    )
    np.testing.assert_array_equal(y[:3], x[::-1][2:5])
    assert not y[3:5].any()


def test_filters_zero_state_eq_and_notch():
    fs = 8000
    t = np.arange(fs) / fs
    x = np.sin(2 * np.pi * 1000 * t)[:, None]
    p = {"type": "filter", "mode": "lowpass", "cutoff_hz": 1000, "order": 4}
    y, _, _ = e.apply(x, fs, [p])
    assert np.sqrt(np.mean(y[1000:] ** 2)) == pytest.approx(0.5, abs=0.002)
    y, _, _ = e.apply(x, fs, [{"type": "filter", "mode": "notch", "frequency_hz": 1000, "q": 20}])
    assert np.sqrt(np.mean(y[4000:] ** 2)) < 1e-4
    impulse = np.zeros((1000, 1))
    impulse[0] = 1
    y, _, _ = e.apply(impulse, fs, [{"type": "filter", "kind": "fir", "taps": 129, "cutoff_hz": 1500}])
    assert np.argmax(abs(y[:, 0])) == 64
    y, _, _ = e.apply(
        x, fs, [{"type": "filter", "mode": "peaking", "frequency_hz": 1000, "q": 1, "gain_db": 6}]
    )
    assert np.sqrt(np.mean(y[1000:] ** 2)) == pytest.approx(10 ** (0.3) / np.sqrt(2), rel=0.005)


def test_dynamics_static_curves_and_ceiling():
    x = np.ones((100, 1)) * 10 ** (-6 / 20)
    y, _, _ = e.apply(
        x, 8000, [{"type": "dynamics", "threshold_db": -18, "ratio": 4, "attack_ms": 0, "release_ms": 0}]
    )
    assert 20 * np.log10(y[-1, 0]) == pytest.approx(-15)
    y, _, _ = e.apply(x, 8000, [{"type": "dynamics", "mode": "limiter", "threshold_db": -12}])
    assert np.max(y) <= 10 ** (-12 / 20)
    y, _, _ = e.apply(
        x * 0.001, 8000, [{"type": "dynamics", "mode": "gate", "threshold_db": -20, "attack_ms": 0}]
    )
    assert y.max() < 1e-8
    y, _, _ = e.apply(
        np.ones((10, 1)) * 0.01,
        8000,
        [{"type": "dynamics", "mode": "expander", "threshold_db": -20, "ratio": 2, "attack_ms": 0}],
    )
    assert y[-1, 0] == pytest.approx(0.001)


def test_convolution_mask_reconstruction_and_restoration():
    rng = np.random.default_rng(42)
    x = rng.normal(0, 0.1, (8000, 2))
    h = [0.5, 0.2, -0.1]
    y, _, _ = e.apply(x, 8000, [{"type": "convolve", "impulse": h}])
    np.testing.assert_allclose(y[:, 0], np.convolve(x[:, 0], h), atol=1e-14)
    y, _, _ = e.apply(x, 8000, [{"type": "spectral_mask", "regions": []}])
    np.testing.assert_allclose(y, x, atol=1e-14)
    t = np.arange(8000) / 8000
    mix = (0.3 * np.sin(2 * np.pi * 1000 * t) + 0.2 * np.sin(2 * np.pi * 500 * t))[:, None]
    y, _, _ = e.apply(
        mix, 8000, [{"type": "spectral_mask", "regions": [{"fmin": 900, "fmax": 1100, "gain_db": -100}]}]
    )
    assert np.sqrt(np.mean(y[1000:-1000] ** 2)) == pytest.approx(0.2 / np.sqrt(2), abs=0.001)
    noisy = x.copy()
    noisy[4000] = 1
    y, _, _ = e.apply(noisy, 8000, [{"type": "declick", "threshold": 5}])
    assert np.max(abs(y[4000])) < 0.5
    noise = rng.normal(0, 0.03, (8000, 1))
    noisy = noise.copy()
    noisy[3000:] += mix[3000:]
    y, _, _ = e.apply(noisy, 8000, [{"type": "denoise", "noise_start": 0, "noise_end": 2000}])
    assert np.mean(y[500:1500] ** 2) < np.mean(noisy[500:1500] ** 2)
    assert np.isfinite(y).all()


def test_resampling_and_phasevocoder_pitch_stretch():
    fs = 8000
    t = np.arange(8000) / fs
    x = np.sin(2 * np.pi * 440 * t)[:, None]
    y, rate, _ = e.apply(x, fs, [{"type": "resample", "sample_rate": 16000}])
    assert len(y) == 16000 and rate == 16000
    assert np.argmax(np.abs(np.fft.rfft(y[:, 0]))) == 440
    pytest.importorskip("librosa")
    y, _, _ = e.apply(x, fs, [{"type": "stretch", "rate": 0.5, "n_fft": 512}])
    assert len(y) == 16000
    assert np.argmax(np.abs(np.fft.rfft(y[:, 0]))) == 880
    y, _, _ = e.apply(x, fs, [{"type": "pitch", "semitones": 12, "n_fft": 512}])
    assert len(y) == 8000
    assert abs(np.argmax(np.abs(np.fft.rfft(y[:, 0]))) - 880) <= 1


@pytest.mark.parametrize(
    "kind",
    [
        "sine",
        "white",
        "pink",
        "brown",
        "chirp",
        "log_sweep",
        "am",
        "fm",
        "multitone",
        "impulse",
        "step",
        "silence",
        "square",
        "sawtooth",
    ],
)
def test_generators_seed_and_finite_groundtruth(kind):
    p = {
        "kind": kind,
        "sample_rate": 8000,
        "duration_seconds": 0.1,
        "frequency_hz": 440,
        "end_frequency_hz": 3000,
    }
    x, fs, _ = g.generate(p)
    y, _, _ = g.generate(p)
    assert x.shape == (800, 1) and fs == 8000
    np.testing.assert_array_equal(x, y)
    assert np.isfinite(x).all()


def test_transfer_ir_step_delay_and_room_reference():
    rng = np.random.default_rng(3)
    x = rng.normal(size=16000)
    h = np.zeros(24)
    h[7] = 0.5
    h[8] = 0.25
    x[-128:] = 0
    y = signal.lfilter(h, [1], x)
    result = a.transfer(
        *source(np.column_stack([x, y]), 8000), {"method": "deconvolution", "impulse_frames": 128}
    )
    np.testing.assert_allclose(result["arrays"]["impulse"][:24], h, atol=0.0001)
    assert result["summary"]["delay_frames"] == 7
    result = a.transfer(*source(np.column_stack([x, y]), 8000), {"method": "h1", "frame_length": 2048})
    assert result["summary"]["delay_frames"] == 7 and np.mean(result["arrays"]["coherence"]) > 0.99
    fs = 8000
    rt = 0.8
    t = np.arange(fs * 3) / fs
    ir = np.exp(-np.log(1000) * t / rt)
    result = a.room(*source(ir, fs), {})
    row = result["summary"]["channels"][0]
    assert row["decays"]["t30"]["rt60_seconds"] == pytest.approx(rt, rel=0.001)
    assert row["decays"]["t20"]["rt60_seconds"] == pytest.approx(rt, rel=0.001)
    expected = 10 * np.log10(
        (1 - np.exp(-2 * np.log(1000) * 0.05 / rt)) / np.exp(-2 * np.log(1000) * 0.05 / rt)
    )
    assert row["c50_db"] == pytest.approx(expected, abs=0.02)
    invalid = a.room(*source(rng.normal(size=8000), 8000), {})
    assert invalid["summary"]["channels"][0]["decays"]["t30"]["rt60_seconds"] is None


def test_distortion_joint_fit_known_thd_and_imd():
    fs = 48000
    t = np.arange(fs) / fs
    x = 0.5 * np.sin(2 * np.pi * 1000 * t) + 0.005 * np.sin(2 * np.pi * 2000 * t)
    result = a.distortion(*source(x), {"fundamental_hz": 1000})
    assert result["summary"]["thd_ratio"][0] == pytest.approx(0.01, rel=1e-8)
    x = (
        0.2 * np.sin(2 * np.pi * 19000 * t)
        + 0.2 * np.sin(2 * np.pi * 20000 * t)
        + 0.002 * np.sin(2 * np.pi * 1000 * t)
    )
    result = a.distortion(*source(x), {"mode": "imd"})
    assert result["summary"]["selected_product_imd_ratio"][0] == pytest.approx(
        0.002 / np.hypot(0.2, 0.2), rel=1e-8
    )


def test_calibration_chain_integrity_and_changed_gain():
    fs = 8000
    t = np.arange(fs) / fs
    x = 0.1 * np.sin(2 * np.pi * 1000 * t)
    chain = {
        "device": "input-A",
        "input_channel": 0,
        "sample_rate": fs,
        "gain_db": 20,
        "microphone": "mic-A",
        "interface": "interface-A",
        "mode": "shared",
    }
    profile = a.calibrate(x[:, None], fs, {"chain": chain, "reference_hz": 1000, "reference_spl_db": 94})
    result = a.calibrated(*source(x, fs), {"chain": chain, "profile": profile})
    assert result["arrays"]["spl"][0] == pytest.approx(94)
    with pytest.raises(ValidationError, match="changed"):
        a.calibrated(*source(x, fs), {"chain": {**chain, "gain_db": 21}, "profile": profile})
    with pytest.raises(ValidationError, match="digest"):
        a.calibrated(*source(x, fs), {"chain": chain, "profile": {**profile, "pa_per_fs": 1}})


def test_bad_effects_bounds_and_invalid_filters():
    x = np.zeros((100, 1))
    for p in [
        {"type": "oops"},
        {"type": "gain", "db": float("nan")},
        {"type": "filter", "cutoff_hz": 5000},
        {"type": "filter", "kind": "fir", "taps": 4},
        {"type": "fade", "curve": "oops"},
        {"type": "resample", "sample_rate": 1},
    ]:
        with pytest.raises(ValidationError):
            e.apply(x, 8000, [p])
    with pytest.raises(ResourceLimit):
        g.generate({"duration_seconds": 3600, "channels": 32})


def test_environment_model_and_edit_filter_response():
    profile = a.environment({"temperature_c": 20, "relative_humidity_percent": 50, "pressure_pa": 101325})
    assert profile["speed_m_s"] == pytest.approx(343.23, abs=0.02)
    assert profile["density_kg_m3"] == pytest.approx(1.204, abs=0.001)
    result = e.response(
        *source(np.zeros(8000), 8000), {"mode": "peaking", "frequency_hz": 1000, "q": 1, "gain_db": 6}
    )
    i = np.argmin(abs(result["arrays"]["frequency"] - 1000))
    assert result["arrays"]["magnitude_db"][i] == pytest.approx(6, abs=0.01)
    assert result["summary"]["stable"]
