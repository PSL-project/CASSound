"""P2 science workspace; every action calls the shared Session API."""

import json
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QPlainTextEdit,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QDoubleSpinBox,
    QLabel,
    QFileDialog,
    QMessageBox,
)
from .. import Region, AnalysisSpec, QuerySpec
from ..domain.types import thaw


class FindingsModel(QAbstractTableModel):
    headers = ["Start / s", "End / s", "Channels", "Finding", "Measurement", "Review"]

    def __init__(self, session):
        super().__init__()
        self.session = session
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 6

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        item = self.rows[index.row()]
        fs = self.session.revision(item["revision_id"]).sample_rate
        values = [
            f"{item['start_frame'] / fs:.6f}",
            f"{item['end_frame'] / fs:.6f}",
            ", ".join(str(c + 1) for c in item["channels"]),
            item["label"],
            json.dumps(thaw(item.get("measurements", {})), ensure_ascii=False),
            item["review_status"],
        ]
        return values[index.column()]

    def refresh(self):
        self.beginResetModel()
        self.rows = list(self.session.findings())
        self.endResetModel()


class SciencePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.session = window.session
        self.parameters = {}
        self.current = None
        self.template = None
        self.examples = []
        self.model = None
        self.active_query = None
        self.request_generation = 0
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.tasks = QComboBox()
        self.tasks.setMinimumWidth(220)
        for task in self.session.analysis_tasks():
            if task["task"] in {"patterns.template", "patterns.train"}:
                continue
            self.tasks.addItem(
                task["task"] + (" (install features)" if not task["available"] else ""), task["task"]
            )
        self.tasks.setCurrentIndex(self.tasks.findData("spectral.welch_psd"))
        row.addWidget(self.tasks)
        self.channels = QComboBox()
        self.channels.addItem("Channel 1", (0,))
        row.addWidget(self.channels)
        for title, fn in [
            ("Parameters", self.edit_parameters),
            ("Run selection", self.run),
            ("Query builder", self.query_dialog),
            ("Load query", self.load_query),
            ("Compare windows", self.compare),
        ]:
            button = QPushButton(title)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        for title, fn in [
            ("Capture template", self.capture_template),
            ("Search template", self.search),
            ("Classifier", self.classifier),
            ("Accept finding", lambda: self.review("accepted")),
            ("Reject finding", lambda: self.review("rejected")),
            ("Export result", self.export),
        ]:
            button = QPushButton(title)
            button.clicked.connect(fn)
            row.addWidget(button)
        layout.addLayout(row)
        self.scope = QLabel("Analysis uses the current waveform viewport and selected channels.")
        layout.addWidget(self.scope)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.curve = pg.PlotWidget()
        self.curve.addLegend(offset=(10, 10))
        self.curve.showGrid(x=True, y=True, alpha=0.12)
        self.image = pg.PlotWidget()
        self.image.showGrid(x=True, y=True, alpha=0.1)
        self.readouts = QPlainTextEdit()
        self.readouts.setReadOnly(True)
        self.table = QTableView()
        self.findings_model = FindingsModel(self.session)
        self.table.setModel(self.findings_model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.clicked.connect(self.select_finding)
        self.table.horizontalHeader().setStretchLastSection(True)
        for title, widget in [
            ("Spectrum / features", self.curve),
            ("Time–frequency", self.image),
            ("Findings", self.table),
            ("Measurements", self.readouts),
        ]:
            self.tabs.addTab(widget, title)
        self.flags = None
        self.colorbar = None

    def source_changed(self):
        region = self.region(quiet=True)
        if region is None:
            return
        revision = self.session.revision(region.revision_id)
        self.channels.clear()
        for ch in range(revision.channels):
            self.channels.addItem(f"Channel {ch + 1}", (ch,))
        if revision.channels > 1:
            self.channels.addItem("All channels", tuple(range(revision.channels)))
        self.request_generation += 1
        self.current = None
        self.refresh_findings()

    def region(self, quiet=False):
        state = self.window.selection
        if not state.revision_id:
            if not quiet:
                QMessageBox.information(self, "Select media", "Import or select a recording first.")
            return None
        channels = self.channels.currentData() if hasattr(self, "channels") else (0,)
        return Region(state.revision_id, state.start_frame, state.end_frame, tuple(channels or (0,)))

    def submit(self, job):
        self.request_generation += 1
        generation = self.request_generation

        def done(reference):
            if (
                generation != self.request_generation
                or reference.revision_id != self.window.selection.revision_id
            ):
                return
            self.current = reference
            self.window.modified = True
            if reference.task == "patterns.train":
                self.model = thaw(reference.summary["model"])
            self.refresh_findings()
            self.window.track(
                self.session.result_view(reference.result_id), lambda view: self.render(view, generation)
            )

        self.window.track(job, done)

    def edit_parameters(self):
        task = self.tasks.currentData()
        dialog = QDialog(self)
        dialog.setWindowTitle(task + " parameters")
        dialog.resize(620, 400)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(json.dumps(self.parameters.get(task, {}), indent=2))
        layout.addWidget(editor)
        allowed = next(v["parameters"] for v in self.session.analysis_tasks() if v["task"] == task)
        note = QLabel("Allowed parameters: " + ", ".join(allowed))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                value = json.loads(editor.toPlainText())
                AnalysisSpec(task, value)
                self.parameters[task] = value
            except Exception as exc:
                QMessageBox.warning(self, "Parameters", str(exc))

    def run(self):
        region = self.region()
        if not region:
            return
        task = self.tasks.currentData()
        parameters = dict(self.parameters.get(task, {}))
        if task == "patterns.predict" and self.model and "model" not in parameters:
            parameters["model"] = self.model
        try:
            self.submit(self.session.analyze(region, AnalysisSpec(task, parameters)))
        except Exception as exc:
            QMessageBox.warning(self, "Analysis", str(exc))

    def query_dialog(self):
        region = self.region()
        if not region:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Signal query builder")
        layout = QFormLayout(dialog)
        kind = QComboBox()
        kind.addItems(
            [
                "Tone near frequency",
                "RMS above threshold",
                "Clipping candidates",
                "Silence candidates",
                "Custom Boolean query",
            ]
        )
        layout.addRow("Condition", kind)
        controls = {}
        for key, title, value, maximum in [
            ("frequency", "Frequency / Hz", 1000, 384000),
            ("tolerance", "Tolerance / Hz", 20, 384000),
            ("prominence", "Tonal prominence / dB", 12, 200),
            ("rms", "RMS / FS", 0.1, 100),
            ("minimum", "Minimum duration / s", 0.1, 86400),
            ("gap", "Merge gap / s", 0.02, 3600),
        ]:
            control = QDoubleSpinBox()
            control.setDecimals(4)
            control.setRange(0, maximum)
            control.setValue(value)
            controls[key] = control
            layout.addRow(title, control)
        editor = QPlainTextEdit(
            json.dumps({"all": [{"metric": "rms", "op": ">", "value": 0.1, "unit": "FS"}]}, indent=2)
        )
        editor.setMaximumHeight(120)
        layout.addRow("Custom rule AST", editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec():
            try:
                values = {k: v.value() for k, v in controls.items()}
                rule = [
                    {
                        "all": [
                            {
                                "metric": "dominant_frequency",
                                "op": "between",
                                "value": [
                                    max(0, values["frequency"] - values["tolerance"]),
                                    values["frequency"] + values["tolerance"],
                                ],
                                "unit": "Hz",
                            },
                            {
                                "metric": "tonal_prominence",
                                "op": ">=",
                                "value": values["prominence"],
                                "unit": "dB",
                            },
                        ]
                    },
                    {"metric": "rms", "op": ">=", "value": values["rms"], "unit": "FS"},
                    {"metric": "clipping_count", "op": ">", "value": 0, "unit": "count"},
                    {"metric": "rms", "op": "<=", "value": values["rms"], "unit": "FS"},
                    None,
                ][kind.currentIndex()]
                if rule is None:
                    rule = json.loads(editor.toPlainText())
                self.active_query = QuerySpec(
                    rule,
                    parameters={"frame_length": 4096, "hop_length": 512},
                    min_duration=values["minimum"],
                    merge_gap=values["gap"],
                )
                self.submit(self.session.detect(region, self.active_query))
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save reusable query (optional)", filter="JSON (*.json)"
                )
                if path:
                    self.active_query.save(path)
            except Exception as exc:
                QMessageBox.warning(self, "Query", str(exc))

    def load_query(self):
        path, _ = QFileDialog.getOpenFileName(self, "Run query recipe", filter="JSON (*.json)")
        region = self.region()
        if path and region:
            try:
                self.submit(self.session.detect(region, QuerySpec.load(path)))
            except Exception as exc:
                QMessageBox.warning(self, "Query", str(exc))

    def compare(self):
        region = self.region()
        if region:
            self.submit(
                self.session.analyze(
                    region,
                    AnalysisSpec(
                        "analysis.compare",
                        {
                            "methods": [
                                {"task": "spectral.welch_psd", "parameters": {"window": "hann"}},
                                {"task": "spectral.welch_psd", "parameters": {"window": "boxcar"}},
                            ]
                        },
                    ),
                )
            )

    def capture_template(self):
        region = self.region()
        if region:
            self.template = region
            self.scope.setText(
                "Template captured. Select a search recording/viewport and use Search template."
            )

    def search(self):
        region = self.region()
        if region and self.template:
            try:
                self.submit(self.session.search(region, self.template))
            except Exception as exc:
                QMessageBox.warning(self, "Pattern search", str(exc))
        elif region:
            QMessageBox.information(self, "Template", "Select an example region and capture it first.")

    def classifier(self):
        action, ok = QInputDialog.getItem(
            self,
            "Pattern classifier",
            "Action",
            ["Capture labeled example", "Train and evaluate examples", "Predict with last model"],
            0,
            False,
        )
        if not ok:
            return
        try:
            region = self.region()
            if region is None:
                return
            if action.startswith("Capture"):
                label, accepted = QInputDialog.getText(self, "Example label", "Class name")
                if accepted and label:
                    self.examples.append((region, label))
                    self.scope.setText(
                        f"{len(self.examples)} labeled examples captured; use independent sources for evaluation."
                    )
            elif action.startswith("Train"):
                self.submit(self.session.train_classifier(self.examples))
            elif self.model:
                self.submit(
                    self.session.analyze(region, AnalysisSpec("patterns.predict", {"model": self.model}))
                )
            else:
                raise ValueError(
                    "Train a classifier or supply a safe JSON model in patterns.predict parameters."
                )
        except Exception as exc:
            QMessageBox.warning(self, "Classifier", str(exc))

    def refresh_findings(self):
        self.findings_model.refresh()
        if self.flags is not None:
            self.window.overview.removeItem(self.flags)
        current = [
            v for v in self.findings_model.rows if v["revision_id"] == self.window.selection.revision_id
        ]
        if current:
            fs = self.session.revision(self.window.selection.revision_id).sample_rate
            self.flags = pg.ScatterPlotItem(
                x=[(v["start_frame"] + v["end_frame"]) / (2 * fs) for v in current],
                y=np.full(len(current), 1.02),
                symbol="t",
                size=9,
                brush="#F4C469",
                pen=None,
            )
            self.window.overview.addItem(self.flags)
        self.table.resizeColumnsToContents()

    def select_finding(self, index):
        if not index.isValid():
            return
        item = self.findings_model.rows[index.row()]
        if item["revision_id"] != self.window.selection.revision_id:
            for row in range(self.window.sources.topLevelItemCount()):
                tree = self.window.sources.topLevelItem(row)
                asset = self.session.asset(tree.data(0, Qt.UserRole))
                if asset.original_revision_id == item["revision_id"]:
                    self.window.sources.setCurrentItem(tree)
                    break
        fs = self.session.revision(item["revision_id"]).sample_rate
        self.window.plot.setXRange(item["start_frame"] / fs, item["end_frame"] / fs, padding=0.05)
        self.scope.setText(
            f"{item['label']} · frames [{item['start_frame']}, {item['end_frame']}) · {item['review_status']}"
        )

    def review(self, status):
        index = self.table.currentIndex()
        if index.isValid():
            self.session.review_finding(self.findings_model.rows[index.row()]["finding_id"], status)
            self.window.modified = True
            self.refresh_findings()

    def render(self, view, generation):
        if generation != self.request_generation or view["revision_id"] != self.window.selection.revision_id:
            return
        readout = self.current.to_dict()
        if view.get("tables"):
            readout["display_values"] = view["tables"]
        self.readouts.setPlainText(json.dumps(thaw(readout), indent=2, ensure_ascii=False))
        kind = view["kind"]
        plot = view.get("presentation", {})
        origin = view["summary"].get("time_origin_seconds", 0)
        time_axis = plot.get("x", "").endswith("time")
        if kind == "compare" and view.get("series"):
            self.curve.clear()
            self.curve.setXLink(None)
            for i, series in enumerate(view["series"]):
                self.curve.plot(
                    series["x"],
                    np.asarray(series["y"], float),
                    pen=pg.mkPen(["#45E6D0", "#EF72C6", "#F4C469", "#A7BDFF"][i], width=1.3),
                    name=series["label"],
                )
            self.curve.setTitle("Method comparison · same selection")
            self.curve.setLabel("bottom", "Frequency / Hz")
            self.curve.setLabel("left", view["series"][0]["unit"] or "value")
            self.tabs.setCurrentWidget(self.curve)
        elif kind == "image" and "values" in view:
            self.image.clear()
            x = np.asarray(view["x"], float) + (origin if time_axis else 0)
            y = np.asarray(view["y"], float)
            z = np.asarray(view["values"], float)
            if len(x) < 2 or len(y) < 2:
                self.tabs.setCurrentWidget(self.readouts)
                return

            def edges(v):
                return np.r_[v[0] - (v[1] - v[0]) / 2, (v[1:] + v[:-1]) / 2, v[-1] + (v[-1] - v[-2]) / 2]

            xe, ye = np.meshgrid(edges(x), edges(y), indexing="ij")
            display = (
                (20 if plot.get("z") == "magnitude" else 10) * np.log10(np.maximum(z, 1e-20))
                if np.nanmin(z) >= 0
                else z
            )
            item = pg.PColorMeshItem(xe, ye, display, colorMap=pg.colormap.get("CET-L9"), edgecolors=None)
            self.image.addItem(item)
            lo, hi = -120.0, 0.0
            finite = display[np.isfinite(display)]
            if finite.size:
                lo, hi = np.percentile(finite, [5, 99.5])
                hi = max(hi, lo + 1)
                item.setLevels((lo, hi))
            if self.colorbar is None:
                self.colorbar = pg.ColorBarItem(
                    values=(lo, hi),
                    colorMap=pg.colormap.get("CET-L9"),
                    interactive=False,
                    label="dB re 1 transform unit",
                )
                self.colorbar.setImageItem(item, insert_in=self.image.getPlotItem())
            else:
                self.colorbar.setImageItem(item)
                self.colorbar.setLevels((lo, hi))
            self.image.setLabel("bottom", "Source time / s" if time_axis else plot.get("x", "x"))
            self.image.setLabel("left", "Frequency / Hz")
            self.image.setTitle(view["task"] + " · peak-pooled dB display")
            self.image.setXLink(self.window.plot if time_axis else None)
            self.tabs.setCurrentWidget(self.image)
        elif kind in {"line", "events", "histogram"} and "x" in view:
            self.curve.clear()
            x = np.asarray(view["x"]) + (origin if time_axis else 0)
            low = np.array(view["minimum"], float)
            high = np.array(view["maximum"], float)
            self.curve.plot(x, high, pen=pg.mkPen("#45E6D0", width=1.2))
            if not np.array_equal(low, high, equal_nan=True):
                self.curve.plot(x, low, pen=pg.mkPen("#EF72C6", width=1))
            self.curve.setLabel("bottom", "Source time / s" if time_axis else plot.get("x", "x"))
            self.curve.setLabel("left", plot.get("y", "value"))
            self.curve.setTitle(view["task"])
            self.curve.setXLink(self.window.plot if time_axis else None)
            self.tabs.setCurrentWidget(self.table if kind == "events" else self.curve)
        else:
            self.tabs.setCurrentWidget(self.readouts)
        self.scope.setText(
            view["task"] + " · source-resolution calculation; display pooling does not affect measurements"
        )

    def export(self):
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export scientific result",
            filter="Result metadata (*.json);;Array values (*.csv);;Parquet (*.parquet);;PNG (*.png);;SVG (*.svg);;PDF (*.pdf);;HTML (*.html)",
        )
        if path:
            self.window.track(self.session.export_result(self.current.result_id, path), lambda _: None)
