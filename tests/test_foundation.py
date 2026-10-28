import time
import json
import shutil
import subprocess
import sys
import wave
import zipfile

import numpy as np
import pytest
import soundfile as sf

from cassound import Session, Region, ExportSpec
from cassound.domain.errors import CASSoundError, ValidationError, Cancelled
from cassound.runtime.cache import Cache


def make_audio(path, subtype="PCM_16", frames=17001):
    x = np.arange(frames, dtype=np.int64)
    if subtype == "PCM_32":
        data = np.column_stack(((x * 100003) % (2**32) - 2**31, -((x * 107) % (2**30)))).astype("int32")
    else:
        data = np.column_stack((np.sin(x * 0.1) * 0.7, np.cos(x * 0.03) * 0.2))
    sf.write(path, data, 48000, subtype=subtype)
    return data


def test_headless_import():
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            'import cassound,sys;print(any(x.startswith(("PySide6","pyqtgraph")) for x in sys.modules))',
        ]
    )
    assert out.strip() == b"False"


@pytest.mark.parametrize("subtype", ["PCM_U8", "PCM_16", "PCM_24", "PCM_32", "FLOAT", "DOUBLE"])
def test_exact_native_import_and_export(tmp_path, subtype):
    path = tmp_path / "source.wav"
    data = make_audio(path, subtype)
    original = path.read_bytes()
    with Session(tmp_path / "session") as session:
        r = session.import_media(path).wait(20)
        a = session.asset(r.asset_id)
        region = Region(a.original_revision_id, 0, a.frames, (0, 1))
        got = np.concatenate(list(session.read_samples(region)))
        if subtype == "PCM_32":
            assert np.array_equal(data, got)
        elif subtype in {"PCM_16", "PCM_24"}:
            target = sf.read(path, dtype="int32", always_2d=True)[0] >> (16 if subtype == "PCM_16" else 8)
            assert np.array_equal(got, target)
        elif subtype == "PCM_U8":
            with wave.open(str(path)) as f:
                target = np.frombuffer(f.readframes(f.getnframes()), dtype="uint8").reshape(-1, 2)
            assert np.array_equal(got, target)
        else:
            assert np.array_equal(got, sf.read(path, dtype="float64", always_2d=True)[0])
        out = tmp_path / "export.wav"
        session.render(a.original_revision_id, ExportSpec(str(out), subtype=subtype)).wait(20)
        assert np.array_equal(
            sf.read(path, dtype="float64", always_2d=True)[0],
            sf.read(out, dtype="float64", always_2d=True)[0],
        )
        assert path.read_bytes() == original
        with pytest.raises(ValidationError):
            session.render(a.original_revision_id, ExportSpec(str(path), overwrite=True))


def test_waveform_bounds_and_peak_preservation(tmp_path):
    p = tmp_path / "pulse.wav"
    data = np.zeros((100001, 1))
    data[12444] = 0.99
    sf.write(p, data, 48000, subtype="DOUBLE")
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(p).wait(20).asset_id)
        region = Region(a.original_revision_id, 0, a.frames, (0,))
        overview = s.waveform(region, pixels=80).wait(20)
        assert max(v[0] for v in overview["maximum"]) == 0.99
        detail = s.waveform(Region(a.original_revision_id, 12443, 12446, (0,))).wait(20)
        assert np.array_equal(np.array(detail["maximum"]).ravel(), [0, 0.99, 0])
        with pytest.raises(ValidationError):
            s.waveform(Region(a.original_revision_id, 0, a.frames + 1, (0,)))
        with pytest.raises(ValidationError):
            s.waveform(region, 0)


@pytest.mark.parametrize("portable", [False, True])
def test_project_roundtrip(tmp_path, portable):
    path = tmp_path / "تست source.wav"
    make_audio(path)
    project = tmp_path / "portable.cassound"
    with Session(tmp_path / "first") as s:
        a = s.asset(s.import_media(path).wait(20).asset_id)
        s.save_project(project, portable=portable).wait(20)
    if portable:
        path.unlink()
    with Session(tmp_path / "second") as s:
        report = s.open_project(project).wait(20)
        assert report["missing_sources"] == []
        assert s.assets()[0].asset_id == a.asset_id
        data = np.concatenate(list(s.read_samples(Region(a.original_revision_id, 0, a.frames, (0,)))))
        assert len(data) == a.frames


def test_missing_relink_and_changed_hash(tmp_path):
    p = tmp_path / "a.wav"
    make_audio(p)
    backup = tmp_path / "b.wav"
    shutil.copyfile(p, backup)
    project = tmp_path / "project.cassound"
    with Session(tmp_path / "one") as s:
        a = s.asset(s.import_media(p).wait(20).asset_id)
        s.save_project(project).wait(20)
    p.unlink()
    with Session(tmp_path / "two") as s:
        report = s.open_project(project).wait(20)
        assert report["missing_sources"] == [a.asset_id]
        with pytest.raises(CASSoundError, match="Relink"):
            list(s.read_samples(Region(a.original_revision_id, 0, 10, (0,))))
        s.relink(a.asset_id, backup).wait(20)
        assert len(next(s.read_samples(Region(a.original_revision_id, 0, 10, (0,))))) == 10
        bad = tmp_path / "bad.wav"
        make_audio(bad, frames=16)
        with pytest.raises(CASSoundError, match="SHA-256"):
            s.relink(a.asset_id, bad).wait(20)
        assert s.asset(a.asset_id).locator == str(backup)


@pytest.mark.parametrize("extra", ["../escape", "/absolute", "C:/outside", "sources/../../escape"])
def test_reject_unsafe_archive(tmp_path, extra):
    file = tmp_path / "attack.cassound"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("manifest.json", "{}")
        z.writestr("state.sqlite", "")
        z.writestr(extra, "evil")
    with Session(tmp_path / "s") as s:
        with pytest.raises(CASSoundError, match="Unsafe"):
            s.open_project(file).wait(20)
    assert not (tmp_path / "escape").exists()


def test_cancel_then_recover(tmp_path):
    path = tmp_path / "tone.wav"
    make_audio(path)
    events = []
    with Session(tmp_path / "s", max_workers=1) as s:
        s.subscribe(events.append)
        job = s.import_media(path)
        assert job.cancel()
        with pytest.raises(Cancelled):
            job.wait(20)
        assert not s.assets()
        a = s.import_media(path).wait(20)
        assert s.asset(a.asset_id).frames == 17001
        assert [e.event for e in events if e.request_id == job.request_id][-1] == "job.cancelled"
        assert not list((tmp_path / "s" / "staging").iterdir())


def test_failure_then_recovery_and_resource_limit(tmp_path):
    p = tmp_path / "tone.wav"
    make_audio(p)
    with Session(tmp_path / "s", decoded_bytes=32) as s:
        with pytest.raises(CASSoundError, match="limit"):
            s.import_media(p).wait(20)
        assert not s.assets()
        assert s.probe(p).wait(20)["streams"]


def test_video_multistream_and_timestamp(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    p = tmp_path / "video.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=32x32:rate=5:duration=0.3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=0.3",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(p),
        ],
        check=True,
    )
    with Session(tmp_path / "s") as s:
        probe = s.probe(p).wait(20)
        streams = [v for v in probe["streams"] if v["codec_type"] == "audio"]
        with pytest.raises(CASSoundError, match="Multiple"):
            s.import_media(p).wait(20)
        a = s.asset(s.import_media(p, streams[1]["index"]).wait(20).asset_id)
        assert a.stream_index == streams[1]["index"] and a.time_base
        assert a.frames > 0 and a.channels == 1
        data = np.concatenate(
            list(s.read_samples(Region(a.original_revision_id, 0, a.frames, (0,)), dtype="float64"))
        ).ravel()
        peak = np.argmax(abs(np.fft.rfft(data))) * a.sample_rate / len(data)
        assert abs(peak - 880) < 10


def test_cli(tmp_path):
    p = tmp_path / "tone.wav"
    make_audio(p)
    out = subprocess.check_output(
        [sys.executable, "-m", "cassound", "--workdir", str(tmp_path / "s"), "--json", "probe", str(p)]
    )
    assert json.loads(out)["streams"]


def test_project_state_hash_tamper_and_no_active_replacement(tmp_path):
    p = tmp_path / "a.wav"
    make_audio(p)
    with Session(tmp_path / "s") as s:
        a = s.import_media(p).wait(20)
        saved = tmp_path / "ok.cassound"
        s.save_project(saved).wait(20)
        tampered = tmp_path / "tampered.cassound"
        with zipfile.ZipFile(saved) as z, zipfile.ZipFile(tampered, "w") as out:
            for entry in z.infolist():
                out.writestr(
                    entry.filename, b"bad" if entry.filename == "state.sqlite" else z.read(entry.filename)
                )
        with pytest.raises(CASSoundError, match="hash"):
            s.open_project(tampered).wait(20)
        assert s.asset(a.asset_id)


def test_stream_copy_extraction(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    p = tmp_path / "source.wav"
    make_audio(p, subtype="PCM_32")
    destination = tmp_path / "copied.mka"
    with Session(tmp_path / "s") as s:
        result = s.extract_stream(p, destination).wait(20)
        assert result["method"] == "stream-copy"
        # The packet/sample content is preserved while the container changes.
        a = s.asset(s.import_media(destination).wait(20).asset_id)
        got = np.concatenate(
            list(s.read_samples(Region(a.original_revision_id, 0, a.frames, (0, 1)), dtype="float64"))
        )
        assert np.array_equal(got, sf.read(p, dtype="float64", always_2d=True)[0])
        with pytest.raises(CASSoundError):
            s.extract_stream(p, p).wait(20)


def test_directory_ownership_and_cache_pruning(tmp_path):
    with Session(tmp_path / "s"):
        with pytest.raises(CASSoundError, match="in use"):
            Session(tmp_path / "s")
    with Session(tmp_path / "s"):
        pass
    cache = Cache(tmp_path / "cache")
    a = cache.path("a" * 64)
    a.mkdir()
    (a / "test").write_bytes(b"a" * 100)
    b = cache.path("b" * 64)
    b.mkdir()
    (b / "test").write_bytes(b"b" * 100)
    assert cache.prune(pinned={"a" * 64}, target_bytes=100) == 100
    assert a.is_dir() and not b.exists()


def test_running_cancel_and_worker_death(tmp_path):
    p = tmp_path / "long.wav"
    make_audio(p, frames=1_000_001)
    events = []
    with Session(tmp_path / "s") as s:
        s.subscribe(events.append)
        job = s.import_media(p)
        deadline = time.monotonic() + 10
        while not any(e.event == "job.progress" for e in events) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert job.state == "running"
        assert job.cancel()
        with pytest.raises(Cancelled):
            job.wait(20)
        assert not s.assets()
        killed = s.import_media(p)
        deadline = time.monotonic() + 10
        while killed._process is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert killed._process is not None
        killed._process.kill()
        with pytest.raises(CASSoundError, match="Worker exited"):
            killed.wait(20)
        recovered = s.import_media(p).wait(20)
        assert s.asset(recovered.asset_id).frames == 1_000_001


def test_signed_8_bit_aiff_and_concurrent_cache_reuse(tmp_path):
    path = tmp_path / "signed.aiff"
    make_audio(path, subtype="PCM_S8")
    expected = sf.read(path, dtype="int16", always_2d=True)[0] >> 8
    with Session(tmp_path / "s") as s:
        first = s.import_media(path)
        second = s.import_media(path)
        a = s.asset(first.wait(20).asset_id)
        assert second.wait(20).asset_id == a.asset_id
        assert len(s.assets()) == 1
        values = np.concatenate(list(s.read_samples(Region(a.original_revision_id, 0, a.frames, (0, 1)))))
        assert np.array_equal(values, expected)
        destination = tmp_path / "copy.aiff"
        s.render(a.original_revision_id, ExportSpec(str(destination), format="AIFF", subtype="PCM_S8")).wait(
            20
        )
        assert np.array_equal(sf.read(destination, dtype="int16", always_2d=True)[0] >> 8, expected)


def test_save_large_metadata_uses_bounded_control_reference(tmp_path):
    from dataclasses import replace
    from cassound.domain.types import freeze

    p = tmp_path / "a.wav"
    make_audio(p)
    with Session(tmp_path / "s") as s:
        a = s.asset(s.import_media(p).wait(20).asset_id)
        # A large recorder tag should not be sent inside every worker control message.
        changed = replace(a, probe=freeze({"note": "metadata" * 15000, **dict(a.probe)}))
        s.project.assets[a.asset_id] = changed
        project = tmp_path / "large-metadata.cassound"
        s.save_project(project).wait(20)
        assert not list((s.workdir / "requests").iterdir())
        s.waveform(Region(a.original_revision_id, 0, a.frames, (0,))).wait(20)
        assert project.is_file()
