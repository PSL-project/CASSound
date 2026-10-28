import json
import subprocess
import sys
import numpy as np
import pytest
import soundfile as sf
from cassound import Session, Region, AnalysisSpec, QuerySpec
from cassound.domain.errors import ValidationError, CASSoundError


def source(root, frequency=1000):
    fs = 8000
    t = np.arange(fs * 3) / fs
    x = 0.005 * np.sin(2 * np.pi * 300 * t)
    x[(t >= 0.6) & (t < 1.8)] += 0.4 * np.sin(2 * np.pi * frequency * t[(t >= 0.6) & (t < 1.8)])
    path = root / "source.wav"
    sf.write(path, x, fs, subtype="DOUBLE")
    return path


def test_analysis_cache_artifact_export_and_review_roundtrip(tmp_path):
    path = source(tmp_path)
    recipe = QuerySpec(
        {
            "all": [
                {"metric": "dominant_frequency", "op": "between", "value": [990, 1010]},
                {"metric": "rms", "op": ">", "value": 0.1},
            ]
        },
        parameters={"frame_length": 512, "hop_length": 128},
        min_duration=0.3,
    )
    with Session(tmp_path / "first") as s:
        a = s.asset(s.import_media(path).wait().asset_id)
        r = Region(a.original_revision_id, 0, a.frames, (0,))
        psd = s.analyze(r, AnalysisSpec("spectral.welch_psd")).wait(30)
        same = s.analyze(r, AnalysisSpec("spectral.welch_psd")).wait(30)
        assert same.result_id == psd.result_id
        assert not s.result_array(psd.result_id, "psd").flags.writeable
        view = s.result_view(psd.result_id).wait(30)
        assert len(view["x"]) <= 1500
        matches = s.detect(r, recipe).wait(30)
        findings = s.findings(matches.result_id)
        assert len(findings) == 1 and findings[0]["start_frame"] / a.sample_rate == pytest.approx(
            0.6, abs=0.1
        )
        s.review_finding(findings[0]["finding_id"], "accepted", "Known synthetic tone")
        s.export_result(psd.result_id, tmp_path / "values.csv").wait(30)
        s.export_result(matches.result_id, tmp_path / "events.json").wait(30)
        assert (tmp_path / "values.csv").read_text().startswith("array,index,value,unit")
        assert (
            json.loads((tmp_path / "events.json").read_text())["findings"][0]["review_status"] == "accepted"
        )
        s.save_project(tmp_path / "project.cassound", portable=True).wait(30)
        recipe.save(tmp_path / "recipe.json")
        assert QuerySpec.load(tmp_path / "recipe.json").to_dict() == recipe.to_dict()
    with Session(tmp_path / "opened") as s:
        s.open_project(tmp_path / "project.cassound").wait(30)
        assert len(s.findings(matches.result_id)) == 1
        assert s.findings()[0]["review_status"] == "accepted"
        with pytest.raises(ValidationError, match="rerun"):
            s.result_array(psd.result_id, "psd")
        rerun = s.detect(r, recipe).wait(30)
        assert rerun.result_id == matches.result_id
        assert s.findings()[0]["review_status"] == "accepted"


def test_selection_time_origin_and_parameter_validation(tmp_path):
    path = source(tmp_path)
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(path).wait().asset_id)
        r = Region(a.original_revision_id, 4000, 16000, (0,))
        q = QuerySpec(
            {"metric": "rms", "op": ">", "value": 0.1}, parameters={"frame_length": 512, "hop_length": 128}
        )
        ref = s.detect(r, q).wait(30)
        assert all(v["start_frame"] >= 4000 for v in s.findings(ref.result_id))
        assert ref.summary["time_origin_seconds"] == 0.5
        with pytest.raises(ValidationError):
            s.analyze(r, AnalysisSpec("spectral.fft", {"made_up": 10}))
        with pytest.raises(ValidationError):
            s.detect(r, QuerySpec({"metric": "rms", "op": ">", "value": 1, "unit": "Hz"}))
        task = s.analyze(r, AnalysisSpec("spectral.stft", {"frame_length": 512})).wait(30)
        image = s.result_view(task.result_id).wait(30)
        assert image["kind"] == "image" and len(image["values"]) == len(image["x"])


def test_cross_source_search_and_grouped_training(tmp_path):
    rng = np.random.default_rng(9)
    fs = 8000
    template = rng.normal(0, 0.1, size=1000)
    target = np.zeros(12000)
    target[5000:6000] = template
    sf.write(tmp_path / "template.wav", template, fs, subtype="DOUBLE")
    sf.write(tmp_path / "target.wav", target, fs, subtype="DOUBLE")
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(tmp_path / "template.wav").wait().asset_id)
        b = s.asset(s.import_media(tmp_path / "target.wav").wait().asset_id)
        ref = s.search(
            Region(b.original_revision_id, 0, b.frames, (0,)),
            Region(a.original_revision_id, 0, a.frames, (0,)),
            threshold=0.99,
        ).wait(30)
        assert s.findings(ref.result_id)[0]["start_frame"] == 5000
        examples = []
        for i in range(8):
            t = np.arange(8000) / fs
            frequency = 200 if i < 4 else 1200
            wave = 0.4 * np.sin(2 * np.pi * (frequency + i) * t)
            path = tmp_path / f"train{i}.wav"
            sf.write(path, wave, fs, subtype="DOUBLE")
            a = s.asset(s.import_media(path).wait().asset_id)
            examples.append((Region(a.original_revision_id, 0, a.frames, (0,)), "low" if i < 4 else "high"))
        trained = s.train_classifier(examples, {"frame_length": 512, "hop_length": 256}).wait(60)
        assert trained.summary["evaluation_accuracy"] == 1
        model = dict(trained.summary["model"])
        from cassound.domain.types import thaw

        prediction = s.analyze(
            examples[0][0],
            AnalysisSpec("patterns.predict", {"model": thaw(model), "frame_length": 512, "hop_length": 256}),
        ).wait(30)
        assert set(s.result_array(prediction.result_id, "labels")) == {"low"}


def test_cli_scientific_task(tmp_path):
    path = source(tmp_path)
    result = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "cassound",
            "--workdir",
            str(tmp_path / "s"),
            "--json",
            "analyze",
            str(path),
            "--task",
            "time.metrics",
        ],
        text=True,
    )
    assert json.loads(result)["task"] == "time.metrics"


def test_numeric_readout_and_window_comparison_views(tmp_path):
    path = source(tmp_path)
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(path).wait().asset_id)
        r = Region(a.original_revision_id, 0, a.frames, (0,))
        summary = s.analyze(r, AnalysisSpec("time.metrics")).wait(30)
        view = s.result_view(summary.result_id).wait(30)
        assert view["tables"]["rms"][0] > 0
        comparison = s.analyze(
            r,
            AnalysisSpec(
                "analysis.compare",
                {
                    "methods": [
                        {"task": "spectral.welch_psd", "parameters": {"window": "hann"}},
                        {"task": "spectral.welch_psd", "parameters": {"window": "boxcar"}},
                    ]
                },
            ),
        ).wait(30)
        view = s.result_view(comparison.result_id).wait(30)
        assert view["kind"] == "compare" and len(view["series"]) == 2


def test_result_metadata_and_query_requirements_reject_bad_inputs(tmp_path):
    from cassound import ResultRef

    path = source(tmp_path)
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(path).wait().asset_id)
        r = Region(a.original_revision_id, 0, a.frames, (0,))
        ref = s.analyze(r, AnalysisSpec("time.metrics")).wait(30)
        value = ref.to_dict()
        value["arrays"]["rms"]["file"] = "../outside.npy"
        with pytest.raises(ValidationError):
            ResultRef.from_dict(value)
        bad = QuerySpec({"metric": "dominant_frequency", "op": ">", "value": 10000})
        with pytest.raises(CASSoundError, match="Nyquist"):
            s.detect(r, bad).wait(30)
        missing = QuerySpec({"metric": "harmonic_count", "op": ">", "value": 1})
        with pytest.raises(CASSoundError, match="fundamental"):
            s.detect(r, missing).wait(30)
    with pytest.raises(ValidationError):
        QuerySpec({"metric": "rms", "op": ">", "value": 0}, min_duration="invalid")
