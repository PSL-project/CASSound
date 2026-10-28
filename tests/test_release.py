import json
import numpy as np
import pytest
from cassound.engine import messages as m
from cassound.domain.errors import IntegrityError, ResourceLimit
from cassound.engine.forensics import compare, enf, match_enf
from cassound.api import Session
from cassound import Region, AnalysisSpec


@pytest.mark.parametrize("profile", m.PROFILES)
def test_signal_profile(profile):
    payload = "CASSound سلام 🎵".encode()
    envelope = m.seal(payload, "credential")
    wave = m.synthesize(envelope, profile)
    decoded, trace = m.demodulate(np.r_[np.zeros(73), wave] * 0.5, profile, trace=True)
    assert m.unseal(decoded, "credential") == payload
    assert trace["start_frame"] == 73 + 1024


@pytest.mark.parametrize("dtype", ["uint8", "int16", "int32"])
def test_native_lsb(dtype):
    samples = np.zeros(10000, dtype=dtype)
    envelope = m.seal(bytes(range(256)), "credential")
    marked = m.embed_lsb(samples, envelope)
    assert np.max(abs(marked.astype(float) - samples.astype(float))) <= 1
    assert not np.any(samples)
    assert m.unseal(m.extract_lsb(marked), "credential") == bytes(range(256))


def test_authentication_and_bounds():
    for credential in ["password", b"k" * 32]:
        envelope = m.seal(b"x" * 100000, credential)
        assert m.unseal(envelope, credential) == b"x" * 100000
        with pytest.raises(IntegrityError):
            m.unseal(envelope, "wrong")
        with pytest.raises(IntegrityError):
            m.unseal(envelope[:-1] + bytes([envelope[-1] ^ 1]), credential)
    with pytest.raises(ResourceLimit):
        m.seal(b"x" * (m.MAX_PAYLOAD + 1), "password")
    envelope = m.seal(b"correction", "password")
    bits = m.frame(envelope)
    bits[::3] ^= 1
    assert m.unframe(bits) == envelope


@pytest.mark.parametrize("profile", ["spread", "phase", "transform", "echo"])
def test_real_host(profile):
    envelope = m.seal(b"host fixture", "password")
    n = 512
    source = np.random.default_rng(3).normal(0, 0.05, len(m.frame(envelope)) * n)
    marked, meta = m.embed_host(source, envelope, profile, n, strength=0.7 if profile == "echo" else 0.3)
    assert meta["modified_frames"] == len(source)
    assert m.unseal(m.recover_host(marked, profile, n), "password") == b"host fixture"


def test_forensic_known_values():
    x = np.random.default_rng(1).standard_normal(4000)
    r = compare(x, np.r_[np.zeros(73), x * 0.5], 48000)
    assert r["delay_frames"] == 73 and abs(r["gain"] - 0.5) < 1e-10 and r["residual_rms"] < 1e-10
    fs = 500
    x = np.sin(2 * np.pi * 50.1 * np.arange(fs * 30) / fs)[:, None]
    result = enf(lambda a, b: x[a:b], len(x), 1, fs, {"window_seconds": 5})["arrays"]
    assert np.max(abs(result["frequency"] - 50.1)) < 0.01
    ref = match_enf(result["time"], result["frequency"], result["time"], result["frequency"], 0)
    assert ref["rmse_hz"] < 1e-9


def test_integrated_private_workflow(tmp_path):
    payload = tmp_path / "payload.bin"
    payload.write_bytes("سلام workflow".encode())
    wave = tmp_path / "message.wav"
    recovered = tmp_path / "recovered.bin"
    with Session(tmp_path / "session") as session:
        config = {"input": str(payload), "output": str(wave), "carrier": "qpsk"}
        session.laboratory("message-encode", config, password="top-secret").wait()
        value = session.laboratory(
            "message-decode",
            {"input": str(wave), "output": str(recovered), "carrier": "qpsk"},
            password="top-secret",
        ).wait()
        assert value["outcome"] == "verified" and recovered.read_bytes() == payload.read_bytes()
        public = json.dumps(session.project.snapshot(), ensure_ascii=False)
        assert "top-secret" not in public and "سلام workflow" not in public
        source = session.import_media(wave).wait()
        revision = session.revision(source.revision_id)
        result = session.analyze(
            Region(revision.revision_id, 0, revision.frames, (0,)), AnalysisSpec("spectral.welch_psd")
        ).wait()
        for extension in ["json", "csv", "parquet", "png", "svg", "pdf", "html"]:
            output = tmp_path / ("report." + extension)
            session.export_result(result.result_id, output).wait()
            assert output.stat().st_size > 20
        project = tmp_path / "portable.cassound"
        session.save_project(project, portable=True).wait()
        assert session.jobs.jobs[-1]._private is None


def test_pack_path_rejection(tmp_path):
    import zipfile
    from cassound.runtime.models import install

    pack = tmp_path / "bad.casp"
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("../escape.onnx", b"bad")
    with pytest.raises(Exception):
        install(pack, tmp_path / "models")
    assert not (tmp_path / "escape.onnx").exists()
