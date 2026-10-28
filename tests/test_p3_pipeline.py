import json
import sys
import subprocess
import os
import numpy as np
import pytest
import soundfile as sf
from cassound import Session, Region, AnalysisSpec, ExportSpec
from cassound.domain.errors import ValidationError, CASSoundError
from cassound.runtime.cache import digest_file


def audio(session, root):
    path = root / "source.wav"
    fs = 8000
    x = 0.2 * np.sin(2 * np.pi * 440 * np.arange(fs) / fs)
    sf.write(path, x, fs, subtype="DOUBLE")
    a = session.asset(session.import_media(path).wait().asset_id)
    return a, Region(a.original_revision_id, 0, a.frames, (0,)), x


def samples(session, revision):
    r = session.revision(revision)
    return np.concatenate(
        list(session.read_samples(Region(revision, 0, r.frames, tuple(range(r.channels))), dtype="float64"))
    )


def test_render_revision_branch_save_reopen_analysis_and_export(tmp_path):
    with Session(tmp_path / "first") as s:
        a, r, x = audio(s, tmp_path)
        digest = digest_file(a.locator)
        one = s.edit(r, [{"type": "gain", "db": -6}]).wait(30)
        y = samples(s, one.revision_id)
        np.testing.assert_allclose(y[:, 0], x * 10 ** (-6 / 20), atol=1e-15)
        same = s.edit(r, [{"type": "gain", "db": -6}]).wait(30)
        assert same.revision_id == one.revision_id
        assert s.undo() == r.revision_id
        branch = s.edit(r, [{"type": "reverse"}]).wait(30)
        with pytest.raises(ValidationError, match="redo"):
            s.redo()
        np.testing.assert_array_equal(samples(s, branch.revision_id)[:, 0], x[::-1])
        assert one.revision_id in s.project.edit_nodes and branch.revision_id != one.revision_id
        assert s.undo() == r.revision_id and s.redo() == branch.revision_id
        metrics = s.analyze(Region(one.revision_id, 0, len(y), (0,)), AnalysisSpec("time.metrics")).wait(30)
        assert metrics.revision_id == one.revision_id
        s.render(one.revision_id, ExportSpec(str(tmp_path / "processed.wav"), subtype="DOUBLE")).wait(30)
        np.testing.assert_array_equal(sf.read(tmp_path / "processed.wav")[0], y[:, 0])
        s.save_project(tmp_path / "project.cassound", portable=True).wait(30)
        assert digest_file(a.locator) == digest
    with Session(tmp_path / "opened") as s:
        s.open_project(tmp_path / "project.cassound").wait(30)
        np.testing.assert_array_equal(samples(s, one.revision_id), y)
        assert s.revision(one.revision_id).parents == (r.revision_id,)
        assert s.edit_node(branch.revision_id)["mappings"][0]["input_start"] == a.frames
        assert s.undo() == r.revision_id
        rerender = s.edit(r, [{"type": "gain", "db": -6}]).wait(30)
        assert rerender.revision_id == one.revision_id


def test_multitrack_crossfade_mix_mapping_and_float_headroom(tmp_path):
    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        clip = {
            "region": {"revision_id": r.revision_id, "start_frame": 0, "end_frame": 8000, "channels": [0]},
            "at": 0,
            "effects": [],
        }
        spec = {
            "version": 1,
            "sample_rate": 8000,
            "channels": 1,
            "tracks": [
                {"clips": [clip]},
                {
                    "gain_db": -6,
                    "clips": [{**clip, "at": 4000, "effects": [{"type": "fade", "in_frames": 4000}]}],
                },
            ],
            "effects": [],
        }
        result = s.render_timeline(spec).wait(30)
        y = samples(s, result.revision_id)[:, 0]
        assert len(y) == 12000
        np.testing.assert_allclose(y[:4000], x[:4000], atol=1e-15)
        expected = x[4000:] + x[:4000] * np.linspace(0, 1, 4000) * 10 ** (-6 / 20)
        np.testing.assert_allclose(y[4000:8000], expected, atol=1e-15)
        assert len(s.edit_node(result.revision_id)["mappings"]) == 2
        result = s.edit(
            r,
            [
                {"type": "resample", "sample_rate": 16000},
                {"type": "trim", "start_frame": 4000, "end_frame": 12000},
                {"type": "reverse"},
            ],
        ).wait(30)
        m = s.edit_node(result.revision_id)["mappings"][0]
        assert m["input_start"] == 6000 and m["input_end"] == 2000
        assert m["output_start"] == 0 and m["output_end"] == 8000
        assert s.revision(result.revision_id).sample_rate == 16000
        generated = s.generate(
            {"kind": "step", "amplitude_db": 0, "sample_rate": 8000, "duration_seconds": 0.1}
        ).wait(30)
        rg = Region(generated.revision_id, 0, 800, (0,))
        loud = s.edit(rg, [{"type": "gain", "db": 6}]).wait(30)
        assert samples(s, loud.revision_id).max() > 1
        with pytest.raises(CASSoundError, match="clip"):
            s.render(loud.revision_id, ExportSpec(str(tmp_path / "clipped.wav"), subtype="PCM_16")).wait(30)
        assert not (tmp_path / "clipped.wav").exists()


def test_render_quota_and_missing_sources_fail_without_project_mutation(tmp_path):
    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        count = len(s.assets())
        with pytest.raises(CASSoundError):
            s.edit(r, [{"type": "filter", "cutoff_hz": 5000}]).wait(30)
        assert len(s.assets()) == count
        assert not list((s.workdir / "requests").glob("*.json"))
        s.limits["decoded_bytes"] = 100
        with pytest.raises(CASSoundError):
            s.generate({"sample_rate": 8000}).wait(30)
        assert len(s.assets()) == count


def test_generated_calibration_profiles_persist_and_change_invalidates(tmp_path):
    with Session(tmp_path / "s") as s:
        gen = s.generate(
            {
                "kind": "sine",
                "frequency_hz": 1000,
                "sample_rate": 8000,
                "duration_seconds": 1,
                "amplitude_db": -20,
            }
        ).wait(30)
        r = Region(gen.revision_id, 0, 8000, (0,))
        chain = {
            "device": "A",
            "input_channel": 0,
            "sample_rate": 8000,
            "gain_db": 20,
            "microphone": "M",
            "interface": "I",
            "mode": "shared",
        }
        profile = s.calibrate(r, {"chain": chain, "reference_hz": 1000, "reference_spl_db": 94}).wait(30)
        result = s.analyze(
            r, AnalysisSpec("acoustics.calibrated", {"chain": chain, "profile": dict(profile)})
        ).wait(30)
        assert s.result_array(result.result_id, "spl")[0] == pytest.approx(94)
        s.save_project(tmp_path / "p.cassound", portable=True).wait(30)
    with Session(tmp_path / "second") as s:
        s.open_project(tmp_path / "p.cassound").wait(30)
        assert profile["calibration_id"] in s.project.calibrations


def test_audition_preparation_bounded_selection_and_gain_validation(tmp_path):
    from cassound.runtime.playback import Transport

    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        prepared = s.prepare_playback(Region(r.revision_id, 200, 1200, (0,))).wait(30)
        np.testing.assert_allclose(np.load(prepared["path"])[:, 0], x[200:1200], atol=1e-8)
        with pytest.raises(ValidationError):
            Transport(prepared, gain_db=1, sd=object())


def test_native_vst3_scan_latency_state_tail_and_crash_isolation(tmp_path):
    host = os.environ.get("CASSOUND_VST3_HOST")
    fixture = os.environ.get("CASSOUND_TEST_VST3")
    if not host or not fixture:
        pytest.skip("Build native host and test fixture; set CASSOUND_VST3_HOST/CASSOUND_TEST_VST3")
    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        scanned = s.scan_plugin(fixture).wait(30)
        cl = scanned["classes"][0]
        assert cl["parameters"] and cl["buses"]
        effect = {
            "type": "vst3",
            "plugin": fixture,
            "plugin_digest": scanned["digest"],
            "class_id": cl["class_id"],
            "parameters": {"1": 0.25},
            "tail_seconds": 0.01,
        }
        result = s.edit(r, [effect]).wait(30)
        y = samples(s, result.revision_id)
        from scipy.signal import lfilter

        expected = lfilter([0.25], [1, -0.5], np.pad(x, (0, 80)))
        np.testing.assert_allclose(y[:, 0], expected, atol=3e-8)
        note = s.edit_node(result.revision_id)["notes"][0]
        assert (
            note["latency_samples"] == 7
            and note["latency_compensated"]
            and note["rendered_tail_samples"] == 80
        )
        # State is opaque plugin bytes; controller and processor receive the saved state before render.
        restored = s.edit(r, [{**effect, "parameters": {}, "state": list(note["state"])}]).wait(30)
        np.testing.assert_array_equal(samples(s, restored.revision_id), y)
        count = len(s.assets())
        with pytest.raises(CASSoundError, match="crashed"):
            s.edit(r, [{**effect, "parameters": {"2": 0.5}}]).wait(30)
        assert len(s.assets()) == count and s.plugin_inventory()["plugins"][scanned["digest"]]["quarantined"]
        with pytest.raises(CASSoundError, match="quarantined"):
            s.scan_plugin(fixture).wait(30)
        s.scan_plugin(fixture, retry=True).wait(30)
        assert not s.plugin_inventory()["plugins"][scanned["digest"]]["quarantined"]
        # GUI process/session remains usable after native crash.
        assert s.analyze(r, AnalysisSpec("time.metrics")).wait(30).revision_id == r.revision_id


def test_native_timeout_and_cancellation_do_not_publish(tmp_path):
    host = os.environ.get("CASSOUND_VST3_HOST")
    fixture = os.environ.get("CASSOUND_TEST_VST3")
    if not host or not fixture:
        pytest.skip("Native fixture not configured")
    from cassound.runtime.vst3 import request

    scanned = request({"plugin": fixture, "mode": "scan"}, tmp_path)
    x = np.zeros((800, 2), dtype="<f4")
    x.tofile(tmp_path / "input.f32")
    req = {
        "plugin": fixture,
        "mode": "process",
        "class_id": scanned["classes"][0]["class_id"],
        "channels": 2,
        "frames": 800,
        "sample_rate": 8000,
        "input": str(tmp_path / "input.f32"),
        "output": str(tmp_path / "output.f32"),
        "parameters": {"2": 1},
        "tail_seconds": 0,
    }
    with pytest.raises(CASSoundError, match="timed out"):
        request(req, tmp_path, timeout=0.5)
    assert not (tmp_path / "output.f32").exists()


def test_residual_null_and_original_findings_stay_on_original_revision(tmp_path):
    from cassound import QuerySpec

    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        found = s.detect(
            r,
            QuerySpec(
                {"metric": "rms", "op": ">", "value": 0.01},
                parameters={"frame_length": 256, "hop_length": 128},
            ),
        ).wait(30)
        before = s.findings(found.result_id)
        changed = s.edit(r, [{"type": "gain", "db": -6}]).wait(30)
        edited = Region(changed.revision_id, 0, len(x), (0,))
        residual = s.residual(r, edited).wait(30)
        np.testing.assert_allclose(
            samples(s, residual.revision_id)[:, 0], x * (1 - 10 ** (-6 / 20)), atol=1e-15
        )
        null = s.residual(r, r).wait(30)
        assert not samples(s, null.revision_id).any()
        assert s.findings(found.result_id) == before and before[0]["revision_id"] == r.revision_id


def test_cli_generator_edit_and_portable_timeline(tmp_path):
    recipe = tmp_path / "gen.json"
    recipe.write_text(
        json.dumps({"kind": "sine", "sample_rate": 8000, "duration_seconds": 0.1, "frequency_hz": 440})
    )
    command = [sys.executable, "-m", "cassound", "--json", "--workdir", str(tmp_path / "cli")]
    completed = subprocess.run(
        command
        + [
            "generate",
            str(tmp_path / "gen.wav"),
            "--recipe",
            str(recipe),
            "--project",
            str(tmp_path / "gen.cassound"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(completed.stdout)
    assert value["frames"] == 800
    effects = tmp_path / "fx.json"
    effects.write_text('[{"type":"gain","db":-6}]')
    completed = subprocess.run(
        command + ["edit", str(tmp_path / "gen.wav"), str(tmp_path / "edited.wav"), "--recipe", str(effects)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["frames"] == 800
    x, _ = sf.read(tmp_path / "gen.wav")
    y, _ = sf.read(tmp_path / "edited.wav")
    np.testing.assert_allclose(y, x * 10 ** (-6 / 20), atol=1e-15)


def test_native_job_cancellation_leaves_session_usable(tmp_path):
    import time

    fixture = os.environ.get("CASSOUND_TEST_VST3")
    if not fixture or not os.environ.get("CASSOUND_VST3_HOST"):
        pytest.skip("Native fixture not configured")
    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        record = s.scan_plugin(fixture).wait(30)
        effect = {
            "type": "vst3",
            "plugin": fixture,
            "plugin_digest": record["digest"],
            "class_id": record["classes"][0]["class_id"],
            "parameters": {"2": 1},
            "tail_seconds": 0,
        }
        job = s.edit(r, [effect])
        time.sleep(1)
        assert job.cancel()
        with pytest.raises(CASSoundError):
            job.wait(10)
        assert len(s.assets()) == 1
        assert s.analyze(r, AnalysisSpec("time.metrics")).wait(30).revision_id == r.revision_id


def test_mda_reference_effect_compatibility(tmp_path):
    fixture = os.environ.get("CASSOUND_MDA_VST3")
    if not fixture or not os.environ.get("CASSOUND_VST3_HOST"):
        pytest.skip("Optional official SDK mda reference bundle not configured")
    with Session(tmp_path / "s") as s:
        a, r, x = audio(s, tmp_path)
        # Stereo input is required by these reference effects.
        routed = s.edit(r, [{"type": "route", "matrix": [[1, 1]]}]).wait(30)
        stereo = Region(routed.revision_id, 0, 8000, (0, 1))
        inventory = s.scan_plugin(fixture).wait(30)
        assert len(inventory["classes"]) >= 20
        for name in ("mda Delay", "mda Dynamics", "mda Stereo Simulator"):
            cl = next(c for c in inventory["classes"] if c["name"] == name)
            effect = {
                "type": "vst3",
                "plugin": fixture,
                "plugin_digest": inventory["digest"],
                "class_id": cl["class_id"],
                "parameters": {},
                "tail_seconds": 0.1,
            }
            result = s.edit(stereo, [effect]).wait(30)
            y = samples(s, result.revision_id)
            assert y.shape == (8800, 2) and np.isfinite(y).all() and np.max(abs(y)) > 0
            again = s.edit(stereo, [effect]).wait(30)
            np.testing.assert_array_equal(samples(s, again.revision_id), y)
