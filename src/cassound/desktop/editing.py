"""P3 working controls. All renders/analysis/spooling use the shared cancellable worker API."""

from dataclasses import asdict
import json
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QComboBox,
    QLabel,
    QInputDialog,
    QFileDialog,
    QMessageBox,
    QCheckBox,
    QDoubleSpinBox,
    QSpinBox,
    QFormLayout,
    QScrollArea,
)
import pyqtgraph as pg
from ..domain.types import Region, AnalysisSpec, thaw
from ..domain.errors import ValidationError
from ..engine.editing import single_clip, split_clip, cut_clip

PRESETS = {
    "Gain": {"type": "gain", "db": -6},
    "Fade": {"type": "fade", "in_frames": 480, "out_frames": 480},
    "Normalize": {"type": "normalize", "target_db": -1},
    "Reverse": {"type": "reverse"},
    "Low-pass": {"type": "filter", "kind": "butterworth", "mode": "lowpass", "cutoff_hz": 2000, "order": 4},
    "EQ": {"type": "filter", "mode": "peaking", "frequency_hz": 1000, "q": 1, "gain_db": 3},
    "Compressor": {
        "type": "dynamics",
        "mode": "compressor",
        "threshold_db": -18,
        "ratio": 4,
        "attack_ms": 5,
        "release_ms": 100,
    },
    "Limiter": {"type": "dynamics", "mode": "limiter", "threshold_db": -1},
    "Gate": {"type": "dynamics", "mode": "gate", "threshold_db": -45},
    "Expander": {"type": "dynamics", "mode": "expander", "threshold_db": -35, "ratio": 2},
    "Stretch": {"type": "stretch", "rate": 0.8},
    "Pitch": {"type": "pitch", "semitones": 2},
    "Resample": {"type": "resample", "sample_rate": 48000},
    "Convolution": {"type": "convolve", "impulse": [1, 0.2, 0.1], "tail": "keep"},
    "Spectral mask": {
        "type": "spectral_mask",
        "regions": [{"start_seconds": 0, "end_seconds": 1, "fmin": 900, "fmax": 1100, "gain_db": -30}],
    },
    "De-hum": {"type": "dehum", "frequency_hz": 50, "harmonics": 6, "q": 30},
    "De-noise": {"type": "denoise", "noise_start": 0, "noise_end": 2048, "strength": 1, "floor": 0.05},
    "De-click": {"type": "declick", "threshold": 8, "width": 5},
    "Automation": {"type": "gain", "automation": [[0, -12], [1000, 0]]},
    "Routing": {"type": "route", "matrix": [[1, 1]]},
}


class EditPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.session = window.session
        self.transport = None
        self.recorder = None
        self.record_imported = False
        self.plugin = None
        self.plugin_state = None
        self.parameter_boxes = {}
        self.dirty_parameters = set()
        self._closing = False
        self._playback_epoch = 0
        self.comparison_reference = None
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.status = QLabel("Nondestructive renders · original recordings remain available")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        timeline, tl = self.page("Timeline")
        row = QHBoxLayout()
        tl.addLayout(row)
        for text, fn in [
            ("Selection → clip", self.selection_clip),
            ("Add clip", self.add_clip),
            ("Split", self.split),
            ("Cut", self.cut),
            ("Paste copy", self.paste),
            ("Render timeline", self.render_timeline),
            ("Undo", lambda: self.history(False)),
            ("Redo", lambda: self.history(True)),
        ]:
            self.button(row, text, fn)
        self.timeline_plot = pg.PlotWidget(title="CLIPS / OUTPUT TIME")
        self.timeline_plot.setFixedHeight(150)
        self.timeline_plot.setLabel("bottom", "Time", units="s")
        tl.addWidget(self.timeline_plot)
        self.timeline = QPlainTextEdit()
        self.timeline.setPlaceholderText(
            "Add the current source selection to begin. Timeline JSON supports tracks, clip offsets, mute/solo, routing and effects."
        )
        tl.addWidget(self.timeline)
        self.timeline.textChanged.connect(self.draw_timeline)
        rack, rl = self.page("Effects")
        row = QHBoxLayout()
        rl.addLayout(row)
        self.presets = QComboBox()
        self.presets.addItems(PRESETS)
        row.addWidget(self.presets)
        self.button(row, "Add effect", self.add_effect)
        self.button(row, "Apply to selection", self.apply_selection)
        self.button(row, "Filter response", self.filter_response)
        self.button(row, "Set comparison reference", self.set_reference)
        self.button(row, "Render residual", self.residual)
        self.effects = QPlainTextEdit('[\n  {"type": "gain", "db": -6}\n]')
        rl.addWidget(self.effects)
        rl.addWidget(
            QLabel("Ordered effect chain · gain automation uses sample frames · float headroom is retained.")
        )
        gen, gl = self.page("Generator")
        self.generator = QPlainTextEdit(
            json.dumps(
                {
                    "kind": "log_sweep",
                    "sample_rate": 48000,
                    "duration_seconds": 2,
                    "frequency_hz": 30,
                    "end_frequency_hz": 20000,
                    "amplitude_db": -18,
                    "channels": 1,
                    "seed": 0,
                },
                indent=2,
            )
        )
        gl.addWidget(self.generator)
        row = QHBoxLayout()
        gl.addLayout(row)
        self.kinds = QComboBox()
        self.kinds.addItems(
            [
                "sine",
                "multitone",
                "white",
                "pink",
                "brown",
                "chirp",
                "log_sweep",
                "am",
                "fm",
                "impulse",
                "step",
                "silence",
                "square",
                "sawtooth",
            ]
        )
        row.addWidget(self.kinds)
        self.button(row, "Use type", self.generator_kind)
        self.button(row, "Generate source", self.generate)
        audio, al = self.page("Audio I/O")
        row = QHBoxLayout()
        al.addLayout(row)
        self.button(row, "Refresh devices", self.scan_devices)
        self.input_device = QComboBox()
        self.input_device.addItem("Default input", None)
        self.output_device = QComboBox()
        self.output_device.addItem("Default output", None)
        row.addWidget(self.input_device)
        row.addWidget(self.output_device)
        row = QHBoxLayout()
        al.addLayout(row)
        self.loop = QCheckBox("Loop selection")
        self.exclusive = QCheckBox("WASAPI exclusive")
        row.addWidget(self.loop)
        row.addWidget(self.exclusive)
        self.gain = QDoubleSpinBox()
        self.gain.setRange(-120, 0)
        self.gain.setValue(-12)
        self.gain.setSuffix(" dB audition")
        row.addWidget(self.gain)
        self.button(row, "Play selection", self.play)
        self.button(row, "Stop", self.stop_audio)
        row = QHBoxLayout()
        al.addLayout(row)
        self.seek_frame = QSpinBox()
        self.seek_frame.setRange(0, 2**31 - 1)
        row.addWidget(QLabel("Prepared selection frame"))
        row.addWidget(self.seek_frame)
        self.button(row, "Seek / scrub", self.seek)
        row = QHBoxLayout()
        al.addLayout(row)
        self.capture_seconds = QDoubleSpinBox()
        self.capture_seconds.setRange(0.1, 3600)
        self.capture_seconds.setValue(30)
        self.capture_seconds.setSuffix(" s capture")
        row.addWidget(self.capture_seconds)
        self.capture_rate = QSpinBox()
        self.capture_rate.setRange(8000, 384000)
        self.capture_rate.setValue(48000)
        row.addWidget(self.capture_rate)
        self.capture_channels = QSpinBox()
        self.capture_channels.setRange(1, 32)
        row.addWidget(self.capture_channels)
        self.button(row, "Record to new WAV…", self.record)
        self.device_info = QPlainTextEdit()
        self.device_info.setReadOnly(True)
        al.addWidget(self.device_info)
        ac, acl = self.page("Acoustics")
        row = QHBoxLayout()
        acl.addLayout(row)
        for text, task, params in [
            ("Transfer / IR", "acoustics.transfer", {"method": "h1"}),
            ("Room decay", "acoustics.room", {}),
            ("THD", "acoustics.distortion", {"mode": "thd", "fundamental_hz": 1000}),
            ("IMD", "acoustics.distortion", {"mode": "imd", "f1_hz": 19000, "f2_hz": 20000}),
        ]:
            self.button(row, text, lambda checked=False, t=task, p=params: self.acoustic(t, p))
        self.chain = QPlainTextEdit(
            json.dumps(
                {
                    "device": "identify recording device",
                    "input_channel": 0,
                    "sample_rate": 48000,
                    "gain_db": 0,
                    "microphone": "identify microphone",
                    "interface": "identify interface",
                    "mode": "shared",
                },
                indent=2,
            )
        )
        acl.addWidget(self.chain)
        row = QHBoxLayout()
        acl.addLayout(row)
        self.button(row, "Calibrate selected tone", self.calibrate)
        self.button(row, "Measure calibrated SPL", self.spl)
        self.button(row, "Save calibration…", self.save_calibration)
        self.button(row, "Load calibration…", self.load_calibration)
        self.button(row, "Environment profile…", self.environment_profile)
        pl, pll = self.page("VST3 effects")
        row = QHBoxLayout()
        pll.addLayout(row)
        self.button(row, "Scan effect…", self.scan_plugin)
        self.button(row, "Retry quarantined…", lambda: self.scan_plugin(True))
        self.classes = QComboBox()
        row.addWidget(self.classes)
        self.classes.currentIndexChanged.connect(self.show_parameters)
        self.param_widget = QWidget()
        self.param_layout = QFormLayout(self.param_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.param_widget)
        pll.addWidget(scroll)
        row = QHBoxLayout()
        pll.addLayout(row)
        self.button(row, "Add to rack", self.plugin_effect)
        self.button(row, "Save state…", self.save_plugin_state)
        self.button(row, "Load state…", self.load_plugin_state)
        self.tail = QDoubleSpinBox()
        self.tail.setRange(0, 10)
        self.tail.setValue(0.5)
        self.tail.setSuffix(" s retained tail")
        row.addWidget(self.tail)
        pll.addWidget(
            QLabel(
                "Isolated x64 effect host · mono/stereo main buses · normalized generic parameters · no vendor editor"
            )
        )
        self.profile = None
        self.copy = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_audio)
        self.timer.start(100)

    def page(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.tabs.addTab(widget, title)
        return widget, layout

    def button(self, row, title, fn):
        b = QPushButton(title)
        b.clicked.connect(lambda checked=False: self.guard(fn))
        row.addWidget(b)
        return b

    def guard(self, fn):
        try:
            return fn()
        except Exception as exc:
            QMessageBox.warning(self, "CASSound", str(exc))

    def region(self):
        s = self.window.selection
        if not s.revision_id:
            raise ValidationError("Select a source first.")
        r = self.session.revision(s.revision_id)
        return Region(
            s.revision_id, max(0, s.start_frame), min(r.frames, s.end_frame), tuple(range(r.channels))
        )

    def selection_clip(self):
        r = self.region()
        rev = self.session.revision(r.revision_id)
        self.copy = {"region": asdict(r), "at": 0, "effects": []}
        self.timeline.setPlainText(json.dumps(single_clip(r, rev.sample_rate, len(r.channels)), indent=2))

    def add_clip(self):
        if not self.timeline.toPlainText().strip():
            return self.selection_clip()
        spec = json.loads(self.timeline.toPlainText())
        r = self.region()
        track, ok = QInputDialog.getInt(
            self, "Add clip", "Track number (new last track allowed)", 1, 1, len(spec["tracks"]) + 1
        )
        if not ok:
            return
        at, ok = QInputDialog.getInt(self, "Clip position", "Output start frame", 0, 0, 2**31 - 1)
        if not ok:
            return
        if track > len(spec["tracks"]):
            spec["tracks"].append({"name": f"Track {track}", "clips": []})
        clip = {"region": asdict(r), "at": at, "effects": []}
        spec["tracks"][track - 1]["clips"].append(clip)
        self.copy = clip
        self.timeline.setPlainText(json.dumps(spec, indent=2))

    def clip_index(self, spec):
        track, ok = QInputDialog.getInt(self, "Choose track", "Track (1 based)", 1, 1, len(spec["tracks"]))
        if not ok:
            return None
        clips = spec["tracks"][track - 1]["clips"]
        if not clips:
            raise ValidationError("Track is empty.")
        clip, ok = QInputDialog.getInt(self, "Choose clip", "Clip (1 based)", 1, 1, len(clips))
        return (clips, clip - 1) if ok else None

    def split(self):
        spec = json.loads(self.timeline.toPlainText())
        where = self.clip_index(spec)
        if where is None:
            return
        clips, index = where
        r = clips[index]["region"]
        frame, ok = QInputDialog.getInt(
            self, "Split clip", "Source frame", r["start_frame"] + 1, r["start_frame"] + 1, r["end_frame"] - 1
        )
        if ok:
            clips[index : index + 1] = split_clip(clips[index], frame)
            self.timeline.setPlainText(json.dumps(spec, indent=2))

    def cut(self):
        spec = json.loads(self.timeline.toPlainText())
        where = self.clip_index(spec)
        if where is None:
            return
        clips, index = where
        r = clips[index]["region"]
        start, ok = QInputDialog.getInt(
            self, "Cut", "First source frame", r["start_frame"], r["start_frame"], r["end_frame"] - 1
        )
        if not ok:
            return
        end, ok = QInputDialog.getInt(
            self, "Cut", "End source frame (exclusive)", r["end_frame"], start + 1, r["end_frame"]
        )
        if ok:
            self.copy = {"region": {**r, "start_frame": start, "end_frame": end}, "at": 0, "effects": []}
            clips[index : index + 1] = cut_clip(clips[index], start, end)
            self.timeline.setPlainText(json.dumps(spec, indent=2))

    def paste(self):
        if self.copy is None:
            raise ValidationError("Copy a selection or cut an interval first.")
        spec = json.loads(self.timeline.toPlainText())
        at, ok = QInputDialog.getInt(self, "Paste clip", "Output frame on first track", 0, 0, 2**31 - 1)
        if ok:
            spec["tracks"][0]["clips"].append({**self.copy, "at": at})
            self.timeline.setPlainText(json.dumps(spec, indent=2))

    def draw_timeline(self):
        self.timeline_plot.clear()
        try:
            spec = json.loads(self.timeline.toPlainText())
            fs = spec["sample_rate"]
            self.timeline_plot.setYRange(0.5, len(spec["tracks"]) + 0.5, padding=0)
            for i, tr in enumerate(spec["tracks"]):
                for clip in tr["clips"]:
                    r = clip["region"]
                    a = clip.get("at", 0) / fs
                    b = a + (r["end_frame"] - r["start_frame"]) / fs
                    self.timeline_plot.plot(
                        [a, b],
                        [i + 1, i + 1],
                        pen=pg.mkPen(["#45E6D0", "#EF72C6", "#F4C469"][i % 3], width=15),
                    )
        except (ValueError, KeyError, TypeError):
            pass

    def rendered(self, result):
        self.window.selection.revision_id = result.revision_id
        self.window.populate()
        self.status.setText("Rendered revision ready · source preserved · Undo/Redo available")
        node = thaw(self.session.edit_node(result.revision_id))
        for note in node.get("notes", []):
            if (
                isinstance(note, dict)
                and "state" in note
                and self.plugin
                and note.get("plugin_digest") == self.plugin["digest"]
                and self.classes.currentData()
                and note.get("class_id") == self.classes.currentData()["class_id"]
            ):
                self.plugin_state = note["state"]
                self.dirty_parameters.clear()

    def render_timeline(self):
        self.window.track(
            self.session.render_timeline(json.loads(self.timeline.toPlainText())), self.rendered
        )

    def set_reference(self):
        self.comparison_reference = self.region()
        self.status.setText("Comparison reference set to the current selection.")

    def residual(self):
        if self.comparison_reference is None:
            raise ValidationError("Set a comparison reference first.")
        self.window.track(self.session.residual(self.comparison_reference, self.region()), self.rendered)

    def filter_response(self):
        filters = [p for p in json.loads(self.effects.toPlainText()) if p.get("type") == "filter"]
        if not filters:
            raise ValidationError("Add a filter to the rack first.")
        p = {k: v for k, v in filters[-1].items() if k != "type"}
        self.window.science.submit(self.session.analyze(self.region(), AnalysisSpec("editing.response", p)))

    def apply_selection(self):
        self.comparison_reference = self.region()
        self.window.track(
            self.session.edit(self.region(), json.loads(self.effects.toPlainText())), self.rendered
        )

    def add_effect(self):
        values = json.loads(self.effects.toPlainText())
        values.append(PRESETS[self.presets.currentText()])
        self.effects.setPlainText(json.dumps(values, indent=2))

    def history(self, redo):
        self.window.selection.revision_id = self.session.redo() if redo else self.session.undo()
        self.window.populate()

    def generator_kind(self):
        value = json.loads(self.generator.toPlainText())
        value["kind"] = self.kinds.currentText()
        self.generator.setPlainText(json.dumps(value, indent=2))

    def generate(self):
        self.window.track(self.session.generate(json.loads(self.generator.toPlainText())), self.rendered)

    def scan_devices(self):
        self.window.track(self.session.audio_devices(), self.devices_ready)

    def devices_ready(self, value):
        for combo, kind in [(self.input_device, "input_channels"), (self.output_device, "output_channels")]:
            combo.clear()
            combo.addItem("Default device", None)
            for d in value["devices"]:
                if d[kind]:
                    combo.addItem(d["name"] + " / " + d["host_api"], d["index"])
        self.device_info.setPlainText(json.dumps(value, indent=2))

    def play(self):
        self.stop_audio()
        epoch = self._playback_epoch
        self.window.track(
            self.session.prepare_playback(self.region()), lambda p: self.start_playback(p, epoch)
        )

    def start_playback(self, prepared, epoch=None):
        if self._closing or (epoch is not None and epoch != self._playback_epoch):
            return
        self.transport = self.session.play(
            prepared,
            device=self.output_device.currentData(),
            exclusive=self.exclusive.isChecked(),
            loop=self.loop.isChecked(),
            gain_db=self.gain.value(),
        )
        self.seek_frame.setMaximum(min(prepared["frames"] - 1, 2**31 - 1))

    def stop_audio(self):
        self._playback_epoch += 1
        if self.transport:
            self.transport.stop()
        if self.recorder:
            self.recorder.stop()

    def seek(self):
        if not self.transport:
            raise ValidationError("Prepare playback first.")
        self.transport.seek(self.seek_frame.value())

    def record(self):
        path, _ = QFileDialog.getSaveFileName(self, "Record to separate WAV", filter="WAV (*.wav)")
        if not path:
            return
        self.stop_audio()
        self.record_imported = False
        self.recorder = self.session.record(
            path,
            sample_rate=self.capture_rate.value(),
            channels=self.capture_channels.value(),
            duration_seconds=self.capture_seconds.value(),
            device=self.input_device.currentData(),
            exclusive=self.exclusive.isChecked(),
        )

    def poll_audio(self):
        if self.transport:
            self.status.setText(
                f"Audition: {self.transport.state} · submitted frame {self.transport.submitted_frame:,} · underflows {self.transport.underflows}"
                + (" · " + self.transport.error if self.transport.error else "")
            )
        if self.recorder and not self.record_imported:
            self.status.setText(
                f"Capture: {self.recorder.state} · {self.recorder.frames:,} frames · overflows {self.recorder.overflows}"
                + (" · " + self.recorder.error if self.recorder.error else "")
            )
            if self.recorder.state == "finished":
                self.record_imported = True
                self.window.track(
                    self.session.import_media(self.recorder.path), lambda _: self.window.populate()
                )

    def acoustic(self, task, params):
        r = self.region()
        if task == "acoustics.transfer":
            r = Region(r.revision_id, r.start_frame, r.end_frame, (0, 1))
        self.window.science.submit(self.session.analyze(r, AnalysisSpec(task, params)))

    def calibrate(self):
        r = self.region()
        r = Region(
            r.revision_id,
            r.start_frame,
            r.end_frame,
            (int(json.loads(self.chain.toPlainText())["input_channel"]),),
        )
        level, ok = QInputDialog.getDouble(
            self, "Calibration", "Calibrator level (dB SPL, 1 kHz)", 94, 0, 160, 2
        )
        if ok:
            self.window.track(
                self.session.calibrate(
                    r,
                    {
                        "chain": json.loads(self.chain.toPlainText()),
                        "reference_hz": 1000,
                        "reference_spl_db": level,
                    },
                ),
                self.calibrated,
            )

    def calibrated(self, p):
        self.profile = thaw(p)
        self.status.setText("Calibration profile saved in project: " + p["calibration_id"][:24])

    def spl(self):
        if not self.profile:
            raise ValidationError("Calibrate or load a profile first.")
        ch = json.loads(self.chain.toPlainText())
        r = self.region()
        r = Region(r.revision_id, r.start_frame, r.end_frame, (ch["input_channel"],))
        self.window.science.submit(
            self.session.analyze(
                r, AnalysisSpec("acoustics.calibrated", {"profile": self.profile, "chain": ch})
            )
        )

    def environment_profile(self):
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Environment",
            "Temperature / humidity / pressure JSON",
            '{"temperature_c":20,"relative_humidity_percent":50,"pressure_pa":101325}',
        )
        if ok:
            profile = self.session.environment_profile(json.loads(text))
            self.save_json("Save environment profile", thaw(profile))
            self.status.setText(
                f"Environment saved · dry-air c = {profile['speed_m_s']:.2f} m/s · density = {profile['density_kg_m3']:.4f} kg/m³"
            )

    def save_calibration(self):
        if not self.profile:
            raise ValidationError("No calibration profile.")
        self.save_json("Save calibration", self.profile)

    def load_calibration(self):
        p = self.load_json("Load calibration")
        if p:
            self.profile = thaw(self.session.load_calibration(p))
            self.chain.setPlainText(json.dumps(p["chain"], indent=2))

    def save_json(self, title, value):
        path, _ = QFileDialog.getSaveFileName(self, title, filter="JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(value, indent=2), encoding="utf8")

    def load_json(self, title):
        path, _ = QFileDialog.getOpenFileName(self, title, filter="JSON (*.json)")
        if path:
            if Path(path).stat().st_size > 16 * 1024**2:
                raise ValidationError("JSON file too large.")
            return json.loads(Path(path).read_text(encoding="utf8"))

    def scan_plugin(self, retry=False):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select VST3 module (or use a bundle directory below)", filter="VST3 (*.vst3 *.so)"
        )
        if not path:
            path = QFileDialog.getExistingDirectory(self, "Select .vst3 bundle")
        if path:
            self.window.track(self.session.scan_plugin(path, retry=retry), self.plugin_ready)

    def plugin_ready(self, record):
        self.plugin_state = None
        self.plugin = record
        self.classes.clear()
        for cl in record["classes"]:
            self.classes.addItem(cl["name"], cl)
        self.status.setText("Plugin scanned and digest pinned: " + record["digest"][:16])

    def show_parameters(self):
        while self.param_layout.rowCount():
            self.param_layout.removeRow(0)
        self.parameter_boxes = {}
        self.dirty_parameters = set()
        info = self.classes.currentData()
        if not info:
            return
        for p in info["parameters"]:
            box = QDoubleSpinBox()
            box.setDecimals(6)
            box.setRange(0, 1)
            box.setSingleStep(1 / p["steps"] if p["steps"] > 0 else 0.01)
            box.setValue(p["value"])
            box.setEnabled(not p["flags"] & 2)
            self.param_layout.addRow(p["name"] + " [" + str(p["id"]) + "]", box)
            self.parameter_boxes[str(p["id"])] = box
            box.valueChanged.connect(lambda value, key=str(p["id"]): self.dirty_parameters.add(key))

    def plugin_effect(self):
        info = self.classes.currentData()
        if not self.plugin or not info:
            raise ValidationError("Scan a plugin first.")
        effect = {
            "type": "vst3",
            "plugin": self.plugin["path"],
            "plugin_digest": self.plugin["digest"],
            "class_id": info["class_id"],
            "parameters": {
                k: b.value()
                for k, b in self.parameter_boxes.items()
                if b.isEnabled() and (self.plugin_state is None or k in self.dirty_parameters)
            },
            "tail_seconds": self.tail.value(),
        }
        if self.plugin_state is not None:
            effect["state"] = self.plugin_state
        values = json.loads(self.effects.toPlainText())
        values.append(effect)
        self.effects.setPlainText(json.dumps(values, indent=2))
        self.tabs.setCurrentIndex(1)

    def save_plugin_state(self):
        if self.plugin_state is None:
            raise ValidationError("Render a plugin effect first to capture its state.")
        self.save_json(
            "Save plugin state",
            {
                "plugin_digest": self.plugin["digest"],
                "class_id": self.classes.currentData()["class_id"],
                "state": self.plugin_state,
            },
        )

    def load_plugin_state(self):
        value = self.load_json("Load plugin state")
        if value:
            if (
                not self.plugin
                or value["plugin_digest"] != self.plugin["digest"]
                or value["class_id"] != self.classes.currentData()["class_id"]
            ):
                raise ValidationError("State belongs to a different plugin build/class.")
            self.plugin_state = value["state"]
            self.dirty_parameters.clear()

    def close_audio(self):
        self._closing = True
        self.timer.stop()
        self.stop_audio()
