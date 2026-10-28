import numpy as np
import pytest
from scipy import signal
from cassound.engine import registry, detection, patterns
from cassound.domain.errors import ValidationError


def fixture(seconds=1.0, fs=8000, frequency=1000, amplitude=0.4, channels=1):
    t = np.arange(round(seconds * fs)) / fs
    x = (amplitude * np.sin(2 * np.pi * frequency * t))[:, None]
    return np.repeat(x, channels, axis=1), fs


def run(task, x, fs, p=None):
    if x.ndim == 1:
        x = x[:, None]
    return registry.execute(task, lambda a, b: x[a:b], len(x), x.shape[1], fs, p or {})


def test_metrics_energy_zero_crossing_and_block_boundary():
    x, fs = fixture(seconds=10, amplitude=0.5)
    result = run("time.metrics", x, fs)["arrays"]
    assert result["rms"][0] == pytest.approx(0.5 / np.sqrt(2), abs=1e-12)
    assert result["signal_energy"][0] == pytest.approx(0.125 * 10, abs=1e-10)
    assert result["dc"][0] == pytest.approx(0, abs=1e-12)
    assert result["crest_factor"][0] == pytest.approx(np.sqrt(2), abs=1e-10)
    square = np.tile([-1.0, 1.0], 40000)[:, None]
    assert run("time.metrics", square, fs)["arrays"]["zero_crossings"][0] == 79999
    silence = run("time.metrics", np.zeros((32, 1)), fs)["arrays"]
    assert np.isnan(silence["crest_factor"][0]) and silence["signal_energy"][0] == 0


@pytest.mark.parametrize("nfft", [8000, 16000])
def test_fft_amplitude_padding_dc_nyquist(nfft):
    x, fs = fixture(amplitude=0.37)
    result = run("spectral.fft", x, fs, {"nfft": nfft})
    f, a = result["arrays"]["frequency"], result["arrays"]["amplitude"][:, 0]
    assert f[np.argmax(a)] == 1000
    assert a.max() == pytest.approx(0.37, abs=1e-10)
    assert run("spectral.fft", np.ones((8000, 1)) * 0.2, fs)["arrays"]["amplitude"][0, 0] == pytest.approx(
        0.2
    )
    nyquist = 0.3 * (-1.0) ** np.arange(8000)
    assert run("spectral.fft", nyquist, fs)["arrays"]["amplitude"][-1, 0] == pytest.approx(0.3)
    assert result["summary"]["zero_padding_improves_resolution"] is False


@pytest.mark.parametrize("window", ["hann", "boxcar", "hamming"])
@pytest.mark.parametrize("detrend", [True, False])
def test_streamed_welch_matches_scipy(window, detrend):
    rng = np.random.default_rng(42)
    x = rng.normal(size=(9371, 2)) + 0.3
    p = {"frame_length": 256, "hop_length": 71, "window": window, "detrend": detrend}
    actual = run("spectral.welch_psd", x, 8000, p)["arrays"]
    f, expected = signal.welch(
        x,
        fs=8000,
        window=signal.get_window(window, 256),
        nperseg=256,
        noverlap=185,
        detrend="constant" if detrend else False,
        axis=0,
    )
    np.testing.assert_allclose(actual["frequency"], f)
    np.testing.assert_allclose(actual["psd"], expected, rtol=1e-12, atol=1e-15)


def test_psd_parseval_and_band_power():
    x, fs = fixture(amplitude=0.6)
    result = run("spectral.welch_psd", x, fs, {"frame_length": 1000})["arrays"]
    assert np.sum(result["psd"][:, 0]) * (fs / 1000) == pytest.approx(0.6**2 / 2, rel=1e-12)
    band = run("spectral.band_power", x, fs, {"frame_length": 1000, "fmin": 950, "fmax": 1050})
    assert band["arrays"]["power"][0] == pytest.approx(0.18, rel=1e-10)
    with pytest.raises(ValidationError):
        run("spectral.band_power", x, fs, {"fmin": 1001, "fmax": 1001.1, "frame_length": 256})


def test_stft_chirp_axis_ridge_and_energy():
    fs = 8000
    t = np.arange(8000) / fs
    x = signal.chirp(t, 300, 1.0, 1300)[:, None]
    r = run("spectral.stft", x, fs, {"frame_length": 256, "hop_length": 64})["arrays"]
    expected = 300 + 1000 * r["time"]
    assert np.median(abs(r["ridge"][:, 0] - expected)) < fs / 256
    assert np.median(r["power"].sum(axis=1) * fs / 256) == pytest.approx(0.5, rel=0.02)


def test_peaks_and_harmonic_family():
    x, fs = fixture(frequency=200)
    t = np.arange(len(x)) / fs
    x[:, 0] += 0.2 * np.sin(2 * np.pi * 400 * t)
    peaks = run("spectral.peaks", x, fs, {"frame_length": 2000, "prominence_db": 20})["arrays"]["peaks"]
    assert abs(peaks[0, 1] - 200) < 5
    family = run("spectral.harmonics", x, fs, {"frame_length": 2000, "fundamental": 200, "harmonics": 3})[
        "arrays"
    ]
    np.testing.assert_allclose(family["frequency"][:2, 0], [200, 400], atol=4)
    with pytest.raises(ValidationError):
        run("spectral.harmonics", x, fs)


def test_feature_grid_chunk_boundary_and_silence():
    x, fs = fixture(seconds=5, frequency=997, amplitude=0.4)
    p = {"frame_length": 512, "hop_length": 23, "fmin": 950, "fmax": 1050, "fundamental": 997}
    bank = run("features.spectral", x, fs, p)["arrays"]
    assert np.median(bank["dominant_frequency"]) == pytest.approx(997, abs=1)
    assert np.min(bank["tonal_prominence"]) > 25
    assert np.median(bank["band_power"]) == pytest.approx(0.08, rel=0.02)
    assert np.all(bank["harmonic_count"] >= 1)
    assert len(bank["time"]) == 1 + (len(x) - 512) // 23
    assert np.max(bank["flux"][1:]) < 0.001
    silent = run("features.spectral", np.zeros((4000, 1)), fs)["arrays"]
    assert np.isnan(silent["dominant_frequency"]).all()
    assert not detection.evaluate({"not": {"metric": "dominant_frequency", "op": ">", "value": 100}}, silent)[
        0
    ].any()


def test_query_boolean_units_hysteresis_and_duration():
    fake = {"rms": np.array([[0.0], [0.6], [0.45], [0.4], [0.2], [0.6]])}
    rule = {"metric": "rms", "op": ">=", "value": 0.5, "hysteresis": 0.15, "unit": "FS"}
    detection.validate_rule(rule)
    np.testing.assert_array_equal(detection.evaluate(rule, fake)[0].ravel(), [0, 1, 1, 1, 0, 1])
    with pytest.raises(ValidationError):
        detection.validate_rule({**rule, "unit": "Hz"})
    with pytest.raises(ValidationError):
        detection.validate_rule({"metric": "__import__", "op": "==", "value": 1})
    x, fs = fixture(seconds=2)
    x[:2000] = 0
    x[10000:] = 0
    query = {
        "rule": {
            "all": [
                {"metric": "dominant_frequency", "op": "between", "value": [990, 1010]},
                {"metric": "rms", "op": ">", "value": 0.1},
            ]
        },
        "parameters": {"frame_length": 512, "hop_length": 128},
        "min_duration": 0.2,
        "merge_gap": 0.02,
        "channels": "each",
    }
    out = detection.detect(lambda a, b: x[a:b], len(x), 1, fs, query)
    event = out["summary"]["findings"][0]
    assert event["start_frame"] / fs == pytest.approx(0.25, abs=0.08)
    assert event["end_frame"] / fs == pytest.approx(1.25, abs=0.08)


def test_channel_relationships_coherence_and_analytic():
    rng = np.random.default_rng(18)
    a = rng.normal(size=8192)
    delay = 13
    b = np.r_[np.zeros(delay), a[:-delay]]
    x = np.column_stack((a, b))
    r = run("channels.relationships", x, 8000)
    assert r["summary"]["second_channel_delay_seconds"] == pytest.approx(delay / 8000)
    coherence = run("relationships.coherence", np.column_stack((a, a)), 8000)["arrays"]["coherence"]
    np.testing.assert_allclose(coherence, 1, atol=1e-12)
    tone, fs = fixture(seconds=2, frequency=500)
    result = run("time.analytic", tone, fs)["arrays"]
    np.testing.assert_allclose(result["envelope"][500:-500, 0], 0.4, atol=1e-10)
    assert np.median(result["instantaneous_frequency"][500:-500]) == pytest.approx(500, abs=1e-8)


def test_pitch_missing_fundamental_silence_and_acoustics():
    fs = 8000
    t = np.arange(fs) / fs
    x = (
        0.6 * np.sin(2 * np.pi * 400 * t)
        + 0.4 * np.sin(2 * np.pi * 600 * t)
        + 0.1 * np.sin(2 * np.pi * 800 * t)
    )[:, None]
    r = run("speech.pitch", x, fs, {"frame_length": 1024, "hop_length": 256})["arrays"]
    assert np.nanmedian(r["pitch"]) == pytest.approx(200, rel=0.01)
    result = run("speech.acoustics", x, fs, {"frame_length": 1024, "hop_length": 256})
    assert result["summary"]["jitter_local_estimate"] < 0.03
    assert result["summary"]["voiced_fraction"] > 0.9
    with pytest.raises(ValidationError):
        run("speech.acoustics", np.zeros((8000, 1)), fs)


def test_lpc_resonance_candidates():
    fs = 10000
    n = fs * 2
    excitation = np.zeros(n)
    excitation[::80] = 1
    x = excitation
    for f, bw in [(600, 70), (1600, 100), (2800, 150)]:
        radius = np.exp(-np.pi * bw / fs)
        a = [1, -2 * radius * np.cos(2 * np.pi * f / fs), radius**2]
        x = signal.lfilter([1], a, x)
    x = x / max(abs(x)) * 0.7
    r = run(
        "speech.acoustics",
        x,
        fs,
        {"frame_length": 800, "hop_length": 200, "fmin": 70, "fmax": 500, "lpc_order": 12},
    )["arrays"]
    candidates = np.asarray(
        [np.median(col[np.isfinite(col)]) for col in r["formants"].T if np.isfinite(col).any()]
    )
    assert min(abs(candidates - 600)) < 180
    assert min(abs(candidates - 1600)) < 220


def test_filter_response_known_cutoff_and_delay():
    x, fs = fixture()
    r = run("filters.response", x, fs, {"cutoff_hz": 1000, "order": 4})["arrays"]
    at = np.argmin(abs(r["frequency"] - 1000))
    assert r["magnitude"][at] == pytest.approx(1 / np.sqrt(2), rel=0.01)
    fir = run("filters.response", x, fs, {"kind": "fir", "taps": 101, "cutoff_hz": 1000})["arrays"]
    assert np.median(fir["group_delay"][:200]) == pytest.approx(50 / fs, rel=1e-6)
    with pytest.raises(ValidationError):
        run("filters.response", x, fs, {"cutoff_hz": fs / 2})


def test_bicoherence_known_quadratic_coupling():
    fs = 1024
    length = 128
    rng = np.random.default_rng(3)
    blocks = []
    for _ in range(64):
        t = np.arange(length) / fs
        a, b = rng.uniform(0, 2 * np.pi, 2)
        blocks.append(
            np.cos(2 * np.pi * 80 * t + a)
            + np.cos(2 * np.pi * 120 * t + b)
            + 0.8 * np.cos(2 * np.pi * 200 * t + a + b)
        )
    x = np.concatenate(blocks)[:, None]
    result = run(
        "spectral.bicoherence",
        x,
        fs,
        {"frame_length": 128, "hop_length": 128, "window": "boxcar", "bins": 32},
    )["arrays"]["bicoherence"]
    assert result[10, 15] > 0.99
    assert np.nanmax(result) <= 1


def test_wavelet_tone_and_cepstrum():
    x, fs = fixture(frequency=500)
    r = run("transforms.cwt", x, fs, {"fmin": 200, "fmax": 1000, "bands": 32})["arrays"]
    frequency = r["frequency"][np.argmax(np.mean(r["power"][200:-200], axis=0))]
    assert frequency == pytest.approx(500, rel=0.08)
    cep = run("transforms.cepstrum", x, fs)["arrays"]
    assert len(cep["quefrency"]) == len(cep["cepstrum"])


def test_physics_calibration_and_units():
    x, fs = fixture(amplitude=0.4)
    wavelength = run("physics.measurements", x, fs, {"frequency_hz": 1000})["summary"]["wavelength_m"]
    assert wavelength == pytest.approx(0.343)
    with pytest.raises(ValidationError):
        run("physics.measurements", x, fs, {"quantity": "spl"})
    r = run(
        "physics.measurements",
        x,
        fs,
        {
            "quantity": "intensity",
            "calibration_id": "synthetic reference",
            "pa_per_fs": 0.1,
            "plane_wave_assumption": True,
        },
    )["arrays"]
    assert r["rms_pressure"][0] == pytest.approx(0.04 / np.sqrt(2))
    assert r["spl"][0] == pytest.approx(20 * np.log10(0.04 / np.sqrt(2) / 20e-6))


def test_dtmf_and_morse_known_messages():
    fs = 8000
    t = np.arange(int(0.15 * fs)) / fs
    pieces = []
    for digit, row, col in [("1", 697, 1209), ("5", 770, 1336), ("#", 941, 1477)]:
        pieces.extend(
            [
                0.25 * np.sin(2 * np.pi * row * t) + 0.25 * np.sin(2 * np.pi * col * t),
                np.zeros(int(0.08 * fs)),
            ]
        )
    x = np.concatenate(pieces)[:, None]
    assert run("signals.dtmf", x, fs)["summary"]["decoded_text"] == "15#"
    unit = 0.06
    wave = []
    for i, character in enumerate(["...", "---", "..."]):
        for j, part in enumerate(character):
            duration = unit * (1 if part == "." else 3)
            tt = np.arange(round(duration * fs)) / fs
            wave.append(0.4 * np.sin(2 * np.pi * 700 * tt))
            if j < 2:
                wave.append(np.zeros(round(unit * fs)))
        if i < 2:
            wave.append(np.zeros(round(3 * unit * fs)))
    x = np.concatenate(wave)[:, None]
    assert run("signals.morse", x, fs, {"unit_seconds": unit})["summary"]["decoded_text"] == "SOS"
    assert run("signals.dtmf", np.zeros((4000, 1)), fs)["summary"]["decoded_text"] == ""


def test_template_repetition_and_safe_classifier():
    rng = np.random.default_rng(8)
    template = rng.normal(size=(200, 1))
    x = rng.normal(0, 0.01, size=(5000, 1))
    x[600:800] = template
    x[3200:3400] = template
    result = patterns.template_search(lambda a, b: x[a:b], len(x), 1, 8000, {"threshold": 0.99}, template)
    assert [f["start_frame"] for f in result["summary"]["findings"]] == [600, 3200]
    v = np.r_[np.zeros((4, 8)), np.ones((4, 8)) * 10] + rng.normal(0, 0.01, (8, 8))
    trained = patterns.train(v, ["a"] * 4 + ["b"] * 4, [f"source{i}" for i in range(8)], {})
    assert trained["summary"]["evaluation_accuracy"] == 1
    a = set(trained["summary"]["evaluation_groups_train"])
    b = set(trained["summary"]["evaluation_groups_test"])
    assert not a & b
    labels, _ = patterns.predict_vectors(v, trained["summary"]["model"])
    assert list(labels) == ["a"] * 4 + ["b"] * 4
    with pytest.raises(ValidationError):
        patterns.predict_vectors(v, {"pickle": "do not execute"})


def test_template_search_peak_at_processing_block_boundary():
    rng = np.random.default_rng(84)
    template = rng.normal(0, 0.1, (512, 1))
    x = np.zeros((265000, 1))
    x[262144:262656] = template
    result = patterns.template_search(lambda a, b: x[a:b], len(x), 1, 8000, {"threshold": 0.999}, template)
    assert [v["start_frame"] for v in result["summary"]["findings"]] == [262144]


def test_clipping_silence_and_impulse_candidate_intervals():
    fs = 8000
    x = np.zeros((fs * 2, 1))
    x[4000:8000] = 1.0
    x[8000:12000] = 0.2
    t = np.arange(4000) / fs
    x[12000:, 0] = 0.05 * np.sin(2 * np.pi * 500 * t)
    x[14400] = 0.9

    def find(metric, op, value):
        q = {
            "rule": {"metric": metric, "op": op, "value": value},
            "parameters": {"frame_length": 256, "hop_length": 64},
            "min_duration": 0.0,
            "merge_gap": 0.0,
            "channels": "each",
        }
        return detection.detect(lambda a, b: x[a:b], len(x), 1, fs, q)["summary"]["findings"]

    clipped = find("clipping_count", ">", 0)
    assert len(clipped) == 1 and clipped[0]["start_frame"] <= 4000 and clipped[0]["end_frame"] >= 8000
    silence = find("rms", "<", 0.001)
    assert silence[0]["start_frame"] == 0 and silence[0]["end_frame"] <= 4500
    clicks = find("click_score", ">", 5)
    assert any(event["start_frame"] <= 14400 < event["end_frame"] for event in clicks)
