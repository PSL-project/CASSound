"""Stable P1 session facade. All heavy operations return cancellable jobs."""

import logging
import os
from pathlib import Path
import shutil
import threading
from uuid import uuid4
from .application.project import Project
from .bootstrap import default_workdir
from .domain.commands import Command
from .domain.errors import CASSoundError, ValidationError
from .domain.types import Region, ExportSpec, ImportResult, AnalysisSpec, QuerySpec, ResultRef, freeze, thaw

log = logging.getLogger(__name__)


class Session:
    def __init__(
        self,
        workdir=None,
        *,
        max_workers=2,
        cache_bytes=20 * 1024**3,
        decoded_bytes=8 * 1024**3,
        project_bytes=20 * 1024**3,
        timeout=1800,
        analysis_bytes=256 * 1024**2,
    ):
        from .runtime.jobs import JobManager

        self.workdir = Path(workdir).expanduser().resolve() if workdir else default_workdir()
        self.workdir.mkdir(parents=True, exist_ok=True)
        (self.workdir / "staging").mkdir(exist_ok=True)
        self.project = Project()
        self._subscribers = []
        self._events_lock = threading.RLock()
        self._closed = False
        self._audio_handles = []
        self.limits = {
            "cache_bytes": cache_bytes,
            "decoded_bytes": decoded_bytes,
            "project_bytes": project_bytes,
            "analysis_bytes": analysis_bytes,
        }
        if any(type(v) is not int or v <= 0 for v in self.limits.values()):
            raise ValidationError("Resource budgets must be positive integer byte counts.")
        from .runtime.locking import FileLock

        self._directory_lock = FileLock(self.workdir / "session.lock").acquire()
        try:
            from .runtime.extensions import load

            load(self.workdir)
            self.jobs = JobManager(self.workdir, self._emit, max_workers, timeout)
        except Exception:
            self._directory_lock.release()
            raise

    def _emit(self, event):
        with self._events_lock:
            callbacks = tuple(self._subscribers)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                log.exception("Event subscriber failed")

    def subscribe(self, callback):
        with self._events_lock:
            self._subscribers.append(callback)

        def unsubscribe():
            with self._events_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    def _submit(self, name, payload, commit=lambda value: value, private=None):
        if self._closed:
            raise CASSoundError("Session is closed.")
        command = Command("req-" + uuid4().hex, self.project.project_id, name, payload)
        return self.jobs.submit(command, commit, self.limits, private=private)

    def laboratory(self, operation, parameters=None, *, password=None):
        """Cancellable file/model/forensic lab. Secrets travel only over worker stdin."""
        import json

        parameters = dict(parameters or {})
        if any(k.lower() in {"password", "key", "plaintext", "payload"} for k in parameters):
            raise ValidationError("Secrets must use the private password argument or explicit input file.")
        private = json.dumps(
            {
                "credential": password.hex() if isinstance(password, bytes) else password,
                "raw_key": isinstance(password, bytes),
            },
            ensure_ascii=False,
        )
        if len(private) > 16384:
            raise ValidationError("Credential is too large.")
        generation = self.project.generation

        def commit(value):
            from .runtime.reporting import redact
            from .runtime.cache import cache_key

            receipt = redact(value)
            for key in [
                "values",
                "bitplanes",
                "residual_preview",
                "ciphertext_bit_preview",
                "corrected_group_positions",
                "receipt_sha256",
            ]:
                receipt.pop(key, None)
            receipt["receipt_sha256"] = cache_key(receipt)
            with self.project.lock:
                if generation != self.project.generation:
                    raise CASSoundError("Project changed during laboratory operation.", code="stale_result")
                self.project.laboratory.append(receipt)
            return freeze(value)

        return self._submit(
            "lab.run", {"operation": operation, "parameters": parameters}, commit, private=private
        )

    def enable_extension(self, name):
        from .runtime.extensions import enable

        return enable(name, self.workdir)

    def model_inventory(self):
        return self.laboratory("model.inventory")

    def install_model_pack(self, path):
        return self.laboratory("model.install", {"path": str(Path(path).resolve())})

    def run_model(self, model_id, path, output=None, *, bits=None):
        return self.laboratory(
            "model.run",
            {
                "model_id": model_id,
                "input": str(Path(path).resolve()),
                "output": str(Path(output).resolve()) if output else None,
                "bits": bits,
            },
        )

    def probe(self, path):
        return self._submit("media.probe", {"path": str(Path(path).expanduser().resolve())})

    def import_media(self, path, stream_index=None):
        generation = self.project.generation

        def commit(value):
            with self.project.lock:
                if generation != self.project.generation:
                    raise CASSoundError("Project changed while media was importing.", code="stale_result")
                asset = self.project.register(value)
                return ImportResult(asset.asset_id, asset.original_revision_id)

        return self._submit(
            "media.import",
            {"path": str(Path(path).expanduser().resolve()), "stream_index": stream_index},
            commit,
        )

    def asset(self, asset_id):
        with self.project.lock:
            try:
                return self.project.assets[asset_id]
            except KeyError as exc:
                raise ValidationError("Unknown asset identity.") from exc

    def assets(self):
        with self.project.lock:
            return tuple(self.project.assets.values())

    def revision(self, revision_id):
        with self.project.lock:
            try:
                return self.project.revisions[revision_id]
            except KeyError as exc:
                raise ValidationError("Unknown revision identity.") from exc

    def _region_asset(self, region):
        revision = self.revision(region.revision_id)
        asset = self.asset(revision.asset_id)
        if region.end_frame > revision.frames or max(region.channels) >= revision.channels:
            raise ValidationError("Region exceeds revision bounds/channels.")
        from .runtime.cache import Cache

        cache_path = Cache(self.workdir / "cache").path(asset.cache_key)
        if not (cache_path / "samples.npy").is_file():
            raise CASSoundError(
                "Source samples are unavailable. Relink the original media.", code="missing_source"
            )
        import json

        metadata = json.loads((cache_path / "asset.json").read_text())
        for field in (
            "sha256",
            "stream_index",
            "frames",
            "sample_rate",
            "channels",
            "native_dtype",
            "valid_bits",
            "sample_kind",
        ):
            if metadata.get(field) != getattr(asset, field):
                raise CASSoundError(
                    "Cache and source descriptor disagree; relink the original.", code="integrity_failed"
                )
        return asset

    def read_samples(self, region: Region, dtype="native", *, block_frames=65536):
        import numpy as np
        from .domain.units import normalized
        from .runtime.cache import Cache

        asset = self._region_asset(region)
        if dtype not in {"native", "float32", "float64"} or not 1 <= block_frames <= 1048576:
            raise ValidationError("Choose native/float32/float64 and a bounded block size.")
        raw = np.load(
            Cache(self.workdir / "cache").path(asset.cache_key) / "samples.npy",
            mmap_mode="r",
            allow_pickle=False,
        )

        def blocks():
            for start in range(region.start_frame, region.end_frame, block_frames):
                samples = raw[start : min(start + block_frames, region.end_frame), region.channels].copy()
                if dtype != "native":
                    samples = normalized(samples, asset.sample_kind, asset.valid_bits).astype(dtype)
                samples.flags.writeable = False
                yield samples

        return blocks()

    def waveform(self, region: Region, pixels=1600):
        asset = self._region_asset(region)
        if type(pixels) is not int or not 1 <= pixels <= 8192:
            raise ValidationError("Waveform pixel budget must be 1–8192.")
        return self._submit(
            "media.envelope",
            {
                "asset": {k: v for k, v in asset.to_dict().items() if k != "probe"},
                "revision_id": region.revision_id,
                "start": region.start_frame,
                "end": region.end_frame,
                "channels": list(region.channels),
                "pixels": pixels,
            },
        )

    def _publish(self, result):
        """Commit staged exports only after the cancellable worker has succeeded."""
        source, dest = Path(result.pop("staged_path")), Path(result["destination"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Sibling staging permits an atomic rename even across source/destination filesystems.
        temporary = dest.with_name(dest.name + "." + uuid4().hex + ".partial")
        try:
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if result.get("overwrite"):
                os.replace(temporary, dest)
            else:
                # Hard-link publication is atomic and refuses races with existing files.
                os.link(temporary, dest)
                temporary.unlink()
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def extract_stream(self, path, destination, stream_index=None, *, overwrite=False):
        destination = Path(destination).expanduser().resolve()
        if any(destination == Path(a.locator).resolve() for a in self.assets()):
            raise ValidationError("Extraction cannot overwrite a registered original source.")
        return self._submit(
            "media.extract",
            {
                "path": str(Path(path).expanduser().resolve()),
                "destination": str(destination),
                "stream_index": stream_index,
                "overwrite": overwrite,
            },
            self._publish,
        )

    def render(self, revision_id, spec: ExportSpec):
        rev = self.revision(revision_id)
        asset = self.asset(rev.asset_id)
        self._region_asset(Region(revision_id, 0, rev.frames, tuple(range(rev.channels))))
        destination = Path(spec.path).expanduser().resolve()
        if any(destination == Path(a.locator).resolve() for a in self.assets()):
            raise ValidationError("Exports cannot overwrite any original source in this session.")
        return self._submit(
            "media.export",
            {"asset": {k: v for k, v in asset.to_dict().items() if k != "probe"}, "spec": spec.to_dict()},
            self._publish,
        )

    def save_project(self, path, *, portable=False, overwrite=True):
        from .runtime.cache import atomic_json

        snapshot = self.project.snapshot()
        request_dir = self.workdir / "requests"
        request_dir.mkdir(exist_ok=True)
        token = uuid4().hex
        snapshot_file = request_dir / (token + ".json")
        atomic_json(snapshot_file, snapshot)
        try:
            return self._submit(
                "project.save",
                {
                    "path": str(Path(path).expanduser().resolve()),
                    "snapshot_token": token,
                    "portable": portable,
                    "overwrite": overwrite,
                },
                self._publish,
            )
        except Exception:
            snapshot_file.unlink(missing_ok=True)
            raise

    def open_project(self, path):
        generation = self.project.generation

        def commit(value):
            snapshot = value["snapshot"]
            # Fully validate a replacement before changing the active project.
            replacement = Project(project_id=snapshot["project_id"], title=snapshot["title"])
            for asset in snapshot["assets"]:
                replacement.register(asset)
            replacement.restore_edits(snapshot)
            replacement.restore_science(snapshot)
            with self.project.lock:
                if self.project.generation != generation:
                    raise CASSoundError("Another project open completed first.", code="stale_result")
                self.project.project_id = replacement.project_id
                self.project.title = replacement.title
                self.project.assets = replacement.assets
                self.project.revisions = replacement.revisions
                self.project.results = replacement.results
                self.project.findings = replacement.findings
                self.project.edit_nodes = replacement.edit_nodes
                self.project.edit_history = replacement.edit_history
                self.project.history_position = replacement.history_position
                self.project.calibrations = replacement.calibrations
                self.project.environments = replacement.environments
                self.project.generation += 1
            return {
                "project_id": replacement.project_id,
                "missing_sources": value["missing_sources"],
                "assets": len(replacement.assets),
            }

        return self._submit("project.open", {"path": str(Path(path).expanduser().resolve())}, commit)

    def relink(self, asset_id, path):
        asset = self.asset(asset_id)
        generation = self.project.generation

        def commit(value):
            with self.project.lock:
                if generation != self.project.generation:
                    raise CASSoundError("Project changed during relink.", code="stale_result")
                if asset.render_identity:
                    value.update(
                        render_identity=asset.render_identity,
                        asset_id=asset.asset_id,
                        original_revision_id=asset.original_revision_id,
                    )
                restored = self.project.register(value)
                return ImportResult(restored.asset_id, restored.original_revision_id)

        return self._submit(
            "project.relink",
            {
                "path": str(Path(path).expanduser().resolve()),
                "stream_index": asset.stream_index,
                "expected_digest": asset.sha256,
            },
            commit,
        )

    def analysis_tasks(self):
        from .engine.registry import describe

        return describe()

    def _science_payload(self, region, spec):
        from dataclasses import asdict

        asset = self._region_asset(region)
        return {
            "asset": {k: v for k, v in asset.to_dict().items() if k != "probe"},
            "region": asdict(region),
            "spec": spec.to_dict(),
        }

    def _science_commit(self, generation):
        def commit(value):
            import json
            from .runtime.analysis import result_directory

            reference = ResultRef.from_dict(value)
            directory = result_directory(self.workdir, reference.result_id)
            findings = json.loads((directory / "findings.json").read_text())
            with self.project.lock:
                if generation != self.project.generation:
                    raise CASSoundError("Project changed during analysis.", code="stale_result")
                self.project.results[reference.result_id] = reference.to_dict()
                for item in findings:
                    previous = self.project.findings.get(item["finding_id"])
                    if previous:
                        item["review_status"] = previous["review_status"]
                        item["review_note"] = previous.get("review_note", "")
                    self.project.findings[item["finding_id"]] = item
            return reference

        return commit

    def analyze(self, region: Region, spec: AnalysisSpec):
        from .engine.registry import validate

        validate(spec.task, thaw(spec.parameters))
        if spec.task in {"patterns.template", "patterns.train"}:
            raise ValidationError(
                "Use search() or train_classifier() for tasks requiring additional regions."
            )
        return self._submit(
            "analysis.run", self._science_payload(region, spec), self._science_commit(self.project.generation)
        )

    def detect(self, region: Region, spec: QuerySpec):
        from .engine.detection import validate_rule
        from .engine.registry import validate

        validate_rule(thaw(spec.rule))
        validate("features.spectral", thaw(spec.parameters))
        return self._submit(
            "detection.run",
            self._science_payload(region, spec),
            self._science_commit(self.project.generation),
        )

    def search(
        self,
        region: Region,
        template: Region,
        *,
        threshold=0.9,
        minimum_gap_seconds=0.0,
        allow_inversion=False,
    ):
        spec = AnalysisSpec(
            "patterns.template",
            {
                "threshold": threshold,
                "minimum_gap_seconds": minimum_gap_seconds,
                "allow_inversion": allow_inversion,
            },
        )
        payload = self._science_payload(region, spec)
        extra = self._science_payload(template, AnalysisSpec("time.metrics"))
        extra.pop("spec")
        payload["template"] = extra
        return self._submit("analysis.run", payload, self._science_commit(self.project.generation))

    def train_classifier(self, examples, parameters=None):
        if not isinstance(examples, list) or not 4 <= len(examples) <= 64:
            raise ValidationError("Provide four to 64 (Region, label) training examples.")
        spec = AnalysisSpec("patterns.train", parameters or {})
        payload = self._science_payload(examples[0][0], spec)
        payload["examples"] = []
        for region, label in examples:
            if not isinstance(label, str) or not 1 <= len(label) <= 128:
                raise ValidationError("Class labels must contain 1–128 characters.")
            item = self._science_payload(region, AnalysisSpec("time.metrics"))
            item.pop("spec")
            item["label"] = label
            payload["examples"].append(item)
        return self._submit("analysis.run", payload, self._science_commit(self.project.generation))

    def result(self, result_id):
        with self.project.lock:
            if result_id not in self.project.results:
                raise ValidationError("Unknown scientific result.")
            return ResultRef.from_dict(self.project.results[result_id])

    def result_array(self, result_id, name):
        import json
        import numpy as np
        from .runtime.analysis import result_directory
        from .runtime.cache import digest_file

        reference = self.result(result_id)
        directory = result_directory(self.workdir, result_id)
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["revision_id"] != reference.revision_id or manifest["region"] != thaw(reference.region):
            raise CASSoundError("Result identity mismatch.", code="integrity_failed")
        descriptor = manifest["arrays"].get(name)
        if descriptor is None or descriptor["file"] != name + ".npy" or not name.replace("_", "").isalnum():
            raise ValidationError("Unknown or invalid result-array name.")
        file = directory / descriptor["file"]
        if digest_file(file) != descriptor["sha256"]:
            raise CASSoundError("Result array hash mismatch.", code="integrity_failed")
        array = np.load(file, mmap_mode="r", allow_pickle=False)
        if list(array.shape) != descriptor["shape"] or str(array.dtype) != descriptor["dtype"]:
            raise CASSoundError("Result shape/dtype mismatch.", code="integrity_failed")
        return array

    def result_view(self, result_id, *, channel=0):
        self.result(result_id)
        if type(channel) is not int or channel < 0:
            raise ValidationError("Display channel must be nonnegative.")
        return self._submit("result.view", {"result_id": result_id, "channel": channel})

    def findings(self, result_id=None):
        with self.project.lock:
            return tuple(
                freeze(v)
                for v in self.project.findings.values()
                if result_id is None or v.get("result_id") == result_id
            )

    def review_finding(self, finding_id, status, note=""):
        if (
            status not in {"accepted", "rejected", "unreviewed"}
            or not isinstance(note, str)
            or len(note) > 4096
        ):
            raise ValidationError("Invalid finding review status/note.")
        with self.project.lock:
            if finding_id not in self.project.findings:
                raise ValidationError("Unknown finding.")
            self.project.findings[finding_id] = {
                **self.project.findings[finding_id],
                "review_status": status,
                "review_note": note,
            }

    def export_result(self, result_id, path, *, overwrite=False):
        self.result(result_id)
        destination = Path(path).expanduser().resolve()
        if any(destination == Path(a.locator).resolve() for a in self.assets()):
            raise ValidationError("Result export cannot overwrite an original source.")
        from .runtime.cache import atomic_json

        token = uuid4().hex
        folder = self.workdir / "requests"
        folder.mkdir(exist_ok=True)
        file = folder / (token + ".json")
        atomic_json(file, [thaw(v) for v in self.findings(result_id)])
        try:
            return self._submit(
                "result.export",
                {
                    "result_id": result_id,
                    "path": str(destination),
                    "overwrite": overwrite,
                    "review_token": token,
                },
                self._publish,
            )
        except Exception:
            file.unlink(missing_ok=True)
            raise

    def _render_request(self, recipe, mode, *, source=None):
        from .runtime.cache import atomic_json
        from dataclasses import asdict
        import json

        recipe = json.loads(json.dumps(recipe, allow_nan=False))
        generation = self.project.generation
        data = {"recipe": recipe, "assets": {}}
        if mode == "timeline":
            for track in recipe.get("tracks", []):
                for clip in track.get("clips", []):
                    region = Region(**clip["region"])
                    asset = self._region_asset(region)
                    data["assets"][region.revision_id] = asset.to_dict()
        elif mode == "calibrate":
            asset = self._region_asset(source)
            data["source"] = {"asset": asset.to_dict(), "region": asdict(source)}
        token = uuid4().hex
        directory = self.workdir / "requests"
        directory.mkdir(exist_ok=True)
        file = directory / (token + ".json")
        atomic_json(file, data)

        def commit(value):
            from .domain.types import Revision

            with self.project.lock:
                if generation != self.project.generation:
                    raise CASSoundError("Project changed during render.", code="stale_result")
                if mode == "calibrate":
                    self.project.calibrations[value["calibration_id"]] = value
                    return freeze(value)
                asset = self.project.register(value["asset"])
                node = value["node"]
                rev = asset.original_revision_id
                # Recipe identity distinguishes equal audio rendered by different operations.
                self.project.edit_nodes.setdefault(rev, node)
                r = self.project.revisions[rev]
                self.project.revisions[rev] = Revision(
                    rev, r.asset_id, r.frames, r.sample_rate, r.channels, tuple(node["parents"]), "render"
                )
                history = self.project.edit_history[: self.project.history_position + 1]
                if not history and node["parents"]:
                    history.append(node["parents"][0])
                if not history or history[-1] != rev:
                    history.append(rev)
                self.project.edit_history = history
                self.project.history_position = len(history) - 1
                return ImportResult(asset.asset_id, rev)

        try:
            return self._submit("edit.render", {"recipe_token": token, "mode": mode}, commit)
        except Exception:
            file.unlink(missing_ok=True)
            raise

    def edit(self, region: Region, effects):
        from .engine.editing import single_clip

        revision = self.revision(region.revision_id)
        recipe = single_clip(region, revision.sample_rate, len(region.channels))
        recipe["effects"] = effects
        return self.render_timeline(recipe)

    def render_timeline(self, recipe):
        return self._render_request(recipe, "timeline")

    def generate(self, parameters):
        return self._render_request(parameters, "generate")

    def calibrate(self, region: Region, parameters):
        return self._render_request(parameters, "calibrate", source=region)

    def edit_node(self, revision_id):
        with self.project.lock:
            node = self.project.edit_nodes.get(revision_id)
            if node is None:
                raise ValidationError("Revision has no materialized edit node.")
            return freeze(node)

    def undo(self):
        with self.project.lock:
            if self.project.history_position <= 0:
                raise ValidationError("Nothing to undo.")
            self.project.history_position -= 1
            return self.project.edit_history[self.project.history_position]

    def redo(self):
        with self.project.lock:
            if self.project.history_position + 1 >= len(self.project.edit_history):
                raise ValidationError("Nothing to redo.")
            self.project.history_position += 1
            return self.project.edit_history[self.project.history_position]

    def residual(self, reference: Region, processed: Region):
        from dataclasses import asdict

        a = self.revision(reference.revision_id)
        b = self.revision(processed.revision_id)
        if (
            a.sample_rate != b.sample_rate
            or len(reference.channels) != len(processed.channels)
            or reference.end_frame - reference.start_frame != processed.end_frame - processed.start_frame
        ):
            raise ValidationError(
                "Residual requires equal sample rate, selection length and channel count. Align explicitly before comparison."
            )
        count = len(reference.channels)
        negative = [[-1 if i == j else 0 for j in range(count)] for i in range(count)]
        recipe = {
            "version": 1,
            "name": "Reference minus processed residual",
            "sample_rate": a.sample_rate,
            "channels": count,
            "tracks": [
                {"name": "Reference", "clips": [{"region": asdict(reference), "at": 0}]},
                {
                    "name": "Subtract processed",
                    "clips": [
                        {
                            "region": asdict(processed),
                            "at": 0,
                            "effects": [{"type": "route", "matrix": negative}],
                        }
                    ],
                },
            ],
            "effects": [],
        }
        return self.render_timeline(recipe)

    def environment_profile(self, parameters):
        from .engine.acoustics import environment

        profile = environment(parameters)
        with self.project.lock:
            self.project.environments[profile["environment_id"]] = profile
        return freeze(profile)

    def load_calibration(self, profile):
        from .runtime.cache import cache_key

        value = thaw(profile)
        saved = dict(value)
        ident = saved.pop("calibration_id", None)
        from .engine.acoustics import chain

        chain(value.get("chain"))
        if ident != "cal-" + cache_key(saved):
            raise ValidationError("Invalid calibration profile digest.")
        with self.project.lock:
            self.project.calibrations[ident] = value
        return freeze(value)

    def prepare_playback(self, region: Region):
        payload = self._science_payload(region, AnalysisSpec("time.metrics"))
        payload.pop("spec")
        return self._submit("audio.prepare", payload)

    def play(self, prepared, **options):
        from .runtime.playback import Transport

        handle = Transport(prepared, **options)
        self._audio_handles.append(handle)
        return handle.start()

    def record(self, path, **options):
        from .runtime.playback import Recorder

        destination = Path(path).expanduser().resolve()
        if any(destination == Path(a.locator).resolve() for a in self.assets()):
            raise ValidationError("Recording cannot overwrite a registered source.")
        handle = Recorder(destination, **options)
        self._audio_handles.append(handle)
        return handle.start()

    def audio_devices(self):
        return self._submit("audio.devices", {})

    def scan_plugin(self, path, *, retry=False, timeout=15):
        if type(retry) is not bool or type(timeout) not in (int, float) or not 1 <= timeout <= 60:
            raise ValidationError("Plugin scan timeout must be 1–60 s; retry Boolean.")
        return self._submit(
            "plugin.scan",
            {"plugin": str(Path(path).expanduser().resolve()), "retry": retry, "timeout": timeout},
        )

    def plugin_inventory(self):
        from .runtime.vst3 import status

        return freeze(status(self.workdir))

    def capabilities(self):
        from .runtime.capabilities import capabilities

        return capabilities()

    def close(self):
        if not self._closed:
            self._closed = True
            for handle in self._audio_handles:
                handle.stop()
            try:
                self.jobs.close()
            finally:
                self._directory_lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
