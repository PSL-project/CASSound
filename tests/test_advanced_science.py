import numpy as np
import pytest
from cassound.engine import registry


def run(name, x, fs, p=None):
    return registry.execute(name, lambda a, b: x[a:b], len(x), x.shape[1], fs, p or {})


@pytest.mark.parametrize(
    "task,parameters",
    [
        ("transforms.cqt", {"fmin": 200, "bins": 24, "hop_length": 128}),
        ("transforms.mel", {"frame_length": 256, "hop_length": 128}),
        ("transforms.reassigned", {"frame_length": 256, "hop_length": 128}),
        ("transforms.synchrosqueezed", {"voices": 8, "time_stride": 16}),
        ("music.features", {"frame_length": 256, "hop_length": 128}),
        ("patterns.cluster", {"frame_length": 256, "clusters": 2}),
        ("speech.activity", {"frame_length": 256}),
        (
            "analysis.compare",
            {
                "methods": [
                    {"task": "spectral.welch_psd", "parameters": {"window": "hann"}},
                    {"task": "spectral.welch_psd", "parameters": {"window": "boxcar"}},
                ]
            },
        ),
    ],
)
def test_advanced_tasks_execute_real_algorithms(task, parameters):
    pytest.importorskip("librosa")
    pytest.importorskip("ssqueezepy")
    pytest.importorskip("sklearn")
    fs = 4000
    t = np.arange(4000) / fs
    x = (0.3 * np.sin(2 * np.pi * 400 * t) + 0.1 * np.sin(2 * np.pi * 800 * t))[:, None]
    result = run(task, x, fs, parameters)
    assert result["arrays"]
    assert all(np.asarray(v).size > 0 for v in result["arrays"].values())
    if task == "transforms.synchrosqueezed":
        frequencies = result["arrays"]["frequency"]
        power = result["arrays"]["power"]
        strongest = frequencies[np.argmax(np.median(power, axis=0))]
        assert strongest == pytest.approx(400, rel=0.15)


def test_loudness_gain_and_intersample_peak():
    pytest.importorskip("pyloudnorm")
    fs = 48000
    t = np.arange(fs * 4) / fs
    x = (0.3 * np.sin(2 * np.pi * 1000 * t))[:, None]
    first = run("levels.loudness", x, fs)
    second = run("levels.loudness", x * 2, fs)
    assert second["summary"]["integrated_lufs"] - first["summary"]["integrated_lufs"] == pytest.approx(
        20 * np.log10(2), abs=0.001
    )
    assert first["summary"]["loudness_range_lu"] == pytest.approx(0, abs=0.01)
    inter = (0.9 * np.sin(2 * np.pi * 12000 * t + np.pi / 4))[:, None]
    result = run("levels.loudness", inter, fs)["arrays"]
    assert result["true_peak_estimate"][0] > result["sample_peak"][0] * 1.3
    assert result["true_peak_estimate"][0] == pytest.approx(0.9, abs=0.05)


@pytest.mark.parametrize(
    "db_levels,expected",
    [([-20, -30], 10), ([-20, -15], 5), ([-40, -20], 20), ([-50, -35, -20, -35, -50], 15)],
)
def test_ebu_tech3342_synthetic_lra_reference_profiles(db_levels, expected):
    pytest.importorskip("pyloudnorm")
    fs = 8000
    t = np.arange(20 * fs) / fs
    wave = np.concatenate([10 ** (level / 20) * np.sin(2 * np.pi * 1000 * t) for level in db_levels])
    x = np.column_stack((wave, wave))
    result = run("levels.loudness", x, fs)
    assert result["summary"]["loudness_range_lu"] == pytest.approx(expected, abs=1)
