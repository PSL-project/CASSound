"""Integrated laboratory controls backed by cancellable Session workers."""

import json
from pathlib import Path
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QComboBox,
    QPushButton,
    QLabel,
    QFileDialog,
    QPlainTextEdit,
    QCheckBox,
    QSpinBox,
)
from ..domain.types import thaw
from ..engine.messages import PROFILES


class LaboratoryPanel(QWidget):
    def __init__(self, window, kind):
        super().__init__()
        self.window = window
        self.kind = kind
        self.last = {}
        self.fields = {}
        self.current_job = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        heading = QLabel(
            {
                "messages": "SIGNALS & MESSAGES · authenticated data in sound",
                "forensics": "FORENSICS · measurements, candidates and provenance",
                "models": "CPU MODELS · explicit offline installation",
                "reports": "REPORTS · methods, values and evidence",
            }[kind]
        )
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.operation = QComboBox()
        layout.addWidget(self.operation)
        choices = {
            "messages": [
                ("Encode payload", "message-encode"),
                ("Decode to new file", "message-decode"),
                ("Bit / symbol microscope", "message.inspect"),
                ("Channel sweep", "message.robustness"),
                ("Generate private key file", "keygen"),
            ],
            "forensics": [
                ("Align / gain / null residual", "forensics.compare"),
                ("ENF supplied-reference match", "forensics.enf_reference"),
                ("Recorder feature profile", "forensics.profile"),
            ],
            "models": [
                ("Inventory / verify", "model.inventory"),
                ("Install offline pack", "model.install"),
                ("Remove model", "model.remove"),
                ("Run model", "model.run"),
            ],
            "reports": [
                ("Export selected scientific result", "result.export"),
                ("Project provenance JSON", "report.project"),
            ],
        }
        for title, op in choices[kind]:
            self.operation.addItem(title, op)
        form = QFormLayout()
        layout.addLayout(form)
        for key, title in [
            ("input", "Input file"),
            ("reference", "Reference / profile / ENF CSV"),
            ("output", "New output file"),
        ]:
            if kind == "reports" and key != "output":
                continue
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            field = QLineEdit()
            self.fields[key] = field
            line.addWidget(field)
            browse = QPushButton("…")
            browse.setAccessibleName("Browse " + title)
            browse.clicked.connect(lambda checked=False, k=key: self.browse(k))
            line.addWidget(browse)
            form.addRow(title, row)
        if kind == "messages":
            self.carrier = QComboBox()
            self.carrier.addItems(["lsb", *PROFILES])
            form.addRow("Carrier", self.carrier)
            self.host_mode = QCheckBox("Modify real host audio (spread / echo / phase / transform)")
            form.addRow(self.host_mode)
            self.symbol = QSpinBox()
            self.symbol.setRange(64, 4096)
            self.symbol.setValue(128)
            form.addRow("Samples / symbol", self.symbol)
            self.offset = QSpinBox()
            self.offset.setRange(0, 8_000_000)
            form.addRow("Microscope sample offset", self.offset)
            self.credential = QLineEdit()
            self.credential.setEchoMode(QLineEdit.Password)
            form.addRow("Password", self.credential)
            self.key_path = QLineEdit()
            form.addRow("Or raw 32-byte key file", self.key_path)
        if kind == "models":
            self.model = QComboBox()
            self.model.addItems(
                [
                    "gtcrn",
                    "audioseal_generator",
                    "audioseal_detector",
                    "deepfake",
                    "nmf_separation",
                    "linear_dereverberation",
                ]
            )
            form.addRow("Model", self.model)
            self.bits = QLineEdit("0101010101010101")
            form.addRow("AudioSeal 16-bit payload", self.bits)
        if kind == "reports":
            self.format = QComboBox()
            self.format.addItems(["json", "csv", "parquet", "png", "svg", "pdf", "html"])
            form.addRow("Result format", self.format)
        actions = QHBoxLayout()
        layout.addLayout(actions)
        for label, fn in [
            ("Use selected source", self.selected),
            ("Run", self.run),
            ("Cancel", self.cancel),
            ("Clear private display", self.clear),
        ]:
            button = QPushButton(label)
            button.clicked.connect(fn)
            actions.addWidget(button)
        if kind == "messages":
            reveal = QPushButton("Reveal recovered UTF-8 text")
            reveal.clicked.connect(self.reveal)
            actions.addWidget(reveal)
        self.status = QLabel("Ready. Outputs use new paths; original media stays unchanged.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(90)
        self.plot.setMaximumHeight(230)
        layout.addWidget(self.plot)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        layout.addWidget(self.details)
        self.plot.scene().sigMouseClicked.connect(self.navigate_sample)
        if kind == "messages":
            self.status.setText(
                "LSB: input is payload on encode, host is Reference WAV. Other profiles use 48 kHz mono. Host mode adds a synchronization prefix; choose 512 samples/symbol for echo. Hiding does not guarantee inaudibility or codec survival."
            )

    def navigate_sample(self, event):
        if not self.last or self.kind != "messages":
            return
        point = self.plot.getViewBox().mapSceneToView(event.scenePos())
        if "bitplanes" in self.last:
            scalar = max(0, int(point.x()) + self.last.get("offset", 0))
            frame = scalar // max(1, self.last.get("native_channels", 1))
            note = f"Native scalar sample {scalar}; frame {frame}; plane {max(0, int(point.y()))}"
        elif "start_frame" in self.last:
            frame = max(0, int(point.x()))
            index = max(
                0,
                int(
                    (frame - self.last["start_frame"])
                    * self.last["bits_per_symbol"]
                    / self.last["symbol_frames"]
                ),
            )
            note = f"Frame {frame}; coded bit {index}; frame byte {index // 24}; envelope byte {index // 24 - 12}"
        else:
            return
        self.status.setText(note)
        if self.window.selection.revision_id:
            selected = self.window.session.asset(
                self.window.session.revision(self.window.selection.revision_id).asset_id
            )
            if (
                "input" not in self.fields
                or Path(self.fields["input"].text()).resolve() != Path(selected.locator).resolve()
            ):
                return
            revision = self.window.session.revision(self.window.selection.revision_id)
            if frame < revision.frames:
                span = revision.sample_rate * 0.025
                self.window.plot.setXRange(
                    max(0, frame - span) / revision.sample_rate,
                    min(revision.frames, frame + span) / revision.sample_rate,
                    padding=0,
                )

    def browse(self, key):
        method = QFileDialog.getSaveFileName if key == "output" else QFileDialog.getOpenFileName
        path, _ = method(self, "Choose " + key)
        if path:
            self.fields[key].setText(path)

    def selected(self):
        rev = self.window.selection.revision_id
        if rev and "input" in self.fields:
            self.fields["input"].setText(
                self.window.session.asset(self.window.session.revision(rev).asset_id).locator
            )

    def cancel(self):
        if self.current_job:
            self.current_job.cancel()

    def clear(self):
        self.details.clear()
        self.plot.clear()
        self.last = {}
        if hasattr(self, "credential"):
            self.credential.clear()

    def reveal(self):
        try:
            if self.last.get("outcome") != "verified":
                raise ValueError("Decode and authenticate a message first.")
            path = Path(self.last["path"])
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Recovered file too large for text display.")
            self.details.setPlainText(path.read_bytes().decode("utf8"))
        except (OSError, ValueError) as exc:
            self.status.setText(str(exc))

    def run(self):
        try:
            op = self.operation.currentData()
            p = {k: v.text() for k, v in self.fields.items()}
            credential = None
            if self.kind == "messages":
                credential = self.credential.text()
                self.credential.clear()
                if self.key_path.text():
                    path = Path(self.key_path.text())
                    if path.stat().st_size != 32:
                        raise ValueError("Key file must contain exactly 32 raw bytes.")
                    credential = path.read_bytes()
                p.update(
                    carrier=self.carrier.currentText(),
                    symbol_frames=self.symbol.value(),
                    host=p.get("reference") or None,
                    host_mode=self.host_mode.isChecked(),
                    offset=self.offset.value(),
                )
            if self.kind == "models":
                p.update(model_id=self.model.currentText(), bits=self.bits.text(), path=p["input"])
            if op == "result.export":
                current = self.window.science.current
                if not current:
                    raise ValueError("Run an analysis first.")
                output = Path(p["output"])
                output = output.with_suffix("." + self.format.currentText())
                job = self.window.session.export_result(current.result_id, output)
            else:
                if op == "report.project":
                    p["snapshot"] = self.window.session.project.snapshot()
                job = self.window.session.laboratory(op, p, password=credential)
            self.current_job = job
            self.status.setText("Running in isolated worker…")
            self.window.track(job, self.complete)
        except Exception as exc:
            self.status.setText(str(exc))

    def complete(self, value):
        self.current_job = None
        value = thaw(value)
        self.last = value
        self.status.setText("Completed · " + str(value.get("outcome", value.get("operation", "export"))))
        self.details.setPlainText(json.dumps(value, indent=2, ensure_ascii=False)[:64000])
        self.plot.clear()
        if "bitplanes" in value:
            image = pg.ImageItem(np.asarray(value["bitplanes"]).T)
            self.plot.addItem(image)
            self.plot.setLabel("bottom", "Sample offset + " + str(value["offset"]))
            self.plot.setLabel("left", "Bit plane")
            self.plot.autoRange()
        elif "residual_preview" in value:
            self.plot.plot(
                np.arange(len(value["residual_preview"])) / value["sample_rate"],
                value["residual_preview"],
                pen="#45e6d0",
            )
            self.plot.setLabel("bottom", "Aligned time", units="s")
            self.plot.setLabel("left", "Residual", units="FS")
        elif "ciphertext_bit_preview" in value:
            bits = value["ciphertext_bit_preview"]
            start = value["start_frame"]
            n = value["symbol_frames"]
            width = value["bits_per_symbol"]
            self.plot.plot(start + np.arange(len(bits)) * n / width, bits, pen="#45e6d0")
            self.plot.setLabel("bottom", "Source frame")
            self.plot.setLabel("left", "Coded ciphertext bit")
        elif "rows" in value:
            rows = value["rows"]
            self.plot.plot(np.arange(len(rows)), [int(r.get("recovered", False)) for r in rows], symbol="o")
            self.plot.setLabel("bottom", "Channel row")
            self.plot.setLabel("left", "Recovered: 0 / 1")
        elif "chunk_fake_scores" in value:
            self.plot.plot(value["chunk_fake_scores"], symbol="o")
            self.plot.setLabel("left", "Uncalibrated fake score")
        if self.kind == "reports":
            self.details.setPlainText(
                json.dumps(thaw(self.window.session.project.snapshot()), indent=2, ensure_ascii=False)[:64000]
            )
