from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QFileDialog,
    QMessageBox,
    QToolBar,
    QProgressBar,
    QPlainTextEdit,
    QInputDialog,
)
import pyqtgraph as pg
from ..api import Session
from ..domain.types import Region, ExportSpec
from .state import SelectionState


class MainWindow(QMainWindow):
    def __init__(self, session=None):
        super().__init__()
        self.session = session or Session()
        self.selection = SelectionState()
        self.pending = {}
        self.modified = False
        self.close_after_save = False
        self.setWindowTitle("CASSound — Signal Workbench")
        self.resize(1500, 940)
        self.setMinimumSize(1000, 650)
        self.setAcceptDrops(True)
        self._building_plot = False
        self._view_dirty = False
        toolbar = QToolBar("Project actions")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel("◈ CASSound")
        brand.setObjectName("brand")
        toolbar.addWidget(brand)
        for label, callback, shortcut in [
            ("Import media", self.import_dialog, "Ctrl+O"),
            ("Open project", self.open_dialog, "Ctrl+Shift+O"),
            ("Save project", self.save_dialog, "Ctrl+S"),
            ("Relink source", self.relink_dialog, None),
            ("Export audio", self.export_dialog, None),
            ("Extract stream", self.extract_dialog, None),
            ("Cancel jobs", self.cancel_jobs, "Escape"),
        ]:
            action = QAction(label, self)
            action.triggered.connect(callback)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            toolbar.addAction(action)
        container = QWidget()
        layout = QVBoxLayout(container)
        title = QLabel("ANALYZE   /   Source inspection")
        title.setObjectName("subtitle")
        layout.addWidget(title)
        self.description = QLabel(
            "Import audio or video to inspect its source waveform. Drop a file here to begin."
        )
        self.description.setWordWrap(True)
        self.description.setObjectName("subtitle")
        layout.addWidget(self.description)
        pg.setConfigOptions(antialias=False, background="#0B1019", foreground="#A2B3C8")
        self.overview = pg.PlotWidget(title="FILE OVERVIEW")
        self.plot = pg.PlotWidget(title="SOURCE WAVEFORM")
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.setLabel("left", "Digital amplitude", units="FS")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self.plot.addLegend(offset=(12, 12))
        self.overview.setMaximumHeight(180)
        self.overview.setMouseEnabled(x=False, y=False)
        for plot in (self.overview, self.plot):
            plot.showGrid(x=True, y=True, alpha=0.12)
            plot.setMenuEnabled(False)
        layout.addWidget(self.overview)
        layout.addWidget(self.plot, 1)
        self.range_item = pg.LinearRegionItem((0, 1), brush=pg.mkBrush(69, 230, 208, 30))
        self.overview.addItem(self.range_item)
        self.range_item.sigRegionChangeFinished.connect(self.overview_selected)
        self.plot.getViewBox().sigXRangeChanged.connect(self.viewport_changed)
        self.setCentralWidget(container)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Sources / streams"])
        self.sources.itemSelectionChanged.connect(self.source_selected)
        self.add_dock("PROJECT", self.sources, Qt.LeftDockWidgetArea)
        self.inspector = QPlainTextEdit()
        self.inspector.setReadOnly(True)
        self.inspector.setMaximumWidth(390)
        self.inspector.setPlainText(
            "SOURCE INSPECTOR\n\nSelect a recording to see its format, native sample representation, timestamps and SHA-256 identity."
        )
        self.add_dock("INSPECTOR", self.inspector, Qt.RightDockWidgetArea)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setMaximumWidth(250)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Local processing · source files preserved")
        from .science import SciencePanel

        self.science = SciencePanel(self)
        science_dock = QDockWidget("SIGNAL ANALYSIS", self)
        science_dock.setWidget(self.science)
        self.addDockWidget(Qt.BottomDockWidgetArea, science_dock)
        self.resizeDocks([science_dock], [360], Qt.Vertical)
        from .editing import EditPanel

        self.editor = EditPanel(self)
        edit_dock = QDockWidget("EDIT / ACQUIRE / ACOUSTICS", self)
        edit_dock.setWidget(self.editor)
        self.addDockWidget(Qt.BottomDockWidgetArea, edit_dock)
        self.tabifyDockWidget(science_dock, edit_dock)
        science_dock.raise_()
        from .workbench import install

        install(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_jobs)
        self.timer.start(60)
        self.view_timer = QTimer(self)
        self.view_timer.timeout.connect(self.refresh_view)
        self.view_timer.start(200)

    def add_dock(self, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setWidget(widget)
        dock.setMinimumWidth(220)
        self.addDockWidget(area, dock)

    def track(self, job, callback):
        self.pending[job.request_id] = (job, callback)
        return job

    def poll_jobs(self):
        active = 0
        for key, (job, callback) in list(self.pending.items()):
            if job.state in {"queued", "running"}:
                active += 1
                continue
            # Future may be about to publish after its state has changed.
            if not job._future.done():
                continue
            self.pending.pop(key, None)
            try:
                callback(job.wait())
            except Exception as exc:
                self.close_after_save = False
                if getattr(exc, "code", None) != "cancelled":
                    QMessageBox.warning(self, "CASSound", str(exc))
        self.progress.setRange(0, 0 if active else 100)
        if not active:
            self.progress.setValue(0)
        self.statusBar().showMessage(
            f"{active} background job(s)" if active else "Local processing · source files preserved"
        )

    def import_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import audio or video")
        if path:
            self.import_path(path)

    def import_path(self, path):
        def probed(info):
            streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
            if not streams:
                raise ValueError("This file contains no audio stream.")
            index = streams[0]["index"]
            if len(streams) > 1:
                choices = [
                    f"{s['index']}: {s.get('codec_name', 'audio')} / {s.get('channels', '?')} channels"
                    for s in streams
                ]
                choice, ok = QInputDialog.getItem(
                    self, "Choose audio stream", "Container stream", choices, 0, False
                )
                if not ok:
                    return
                index = int(choice.split(":")[0])
            self.track(self.session.import_media(path, index), lambda _: self.populate())

        self.track(self.session.probe(path), probed)

    def populate(self, modified=True):
        self.modified = modified
        selected_id = self.selection.revision_id
        self.sources.clear()
        for asset in self.session.assets():
            item = QTreeWidgetItem([(asset.name or Path(asset.locator).name)])
            item.setData(0, Qt.UserRole, asset.asset_id)
            self.sources.addTopLevelItem(item)
            if selected_id == asset.original_revision_id or self.sources.topLevelItemCount() == 1:
                self.sources.setCurrentItem(item)

    def source_selected(self):
        item = self.sources.currentItem()
        if not item:
            return
        asset = self.session.asset(item.data(0, Qt.UserRole))
        self.selection.revision_id = asset.original_revision_id
        self.selection.start_frame, self.selection.end_frame = 0, asset.frames
        self.description.setText(
            f"{(asset.name or Path(asset.locator).name)}   ·   {asset.sample_rate:,} Hz   ·   {asset.channels} channels   ·   {asset.frames / asset.sample_rate:.3f} seconds"
        )
        stream = next((v for v in asset.probe.get("streams", ()) if v.get("index") == asset.stream_index), {})
        self.inspector.setPlainText(
            f"SOURCE\n{(asset.name or Path(asset.locator).name)}\n\n"
            f"FORMAT\nCodec: {stream.get('codec_name', 'native audio')}\n"
            f"Sample rate: {asset.sample_rate:,} Hz\nChannels: {asset.channels}\n"
            f"Frames: {asset.frames:,}\nDuration: {asset.frames / asset.sample_rate:.6f} s\n\n"
            f"SAMPLE FIDELITY\n{asset.valid_bits}-bit {asset.sample_kind}\n"
            f"Native storage: {asset.native_dtype}\nOriginal source preserved\n\n"
            f"CONTAINER TIMING\nStream index: {asset.stream_index}\n"
            f"Origin: {asset.timestamp_origin} s\nTimebase: {asset.time_base}\n\n"
            f"SOURCE SHA-256\n{asset.sha256[:16]}\n{asset.sha256[16:32]}\n{asset.sha256[32:48]}\n{asset.sha256[48:]}\n\n"
            f"DECODER\n{asset.decoder}\n\nLOCATION\n{asset.locator}"
        )
        self._building_plot = True
        self.plot.setXRange(0, asset.frames / asset.sample_rate, padding=0)
        self.range_item.setBounds((0, asset.frames / asset.sample_rate))
        self.range_item.setRegion((0, asset.frames / asset.sample_rate))
        self._building_plot = False
        self.science.source_changed()
        try:
            region = Region(asset.original_revision_id, 0, asset.frames, tuple(range(min(asset.channels, 8))))
            revision_id = asset.original_revision_id
            self.track(self.session.waveform(region), lambda result: self.draw_overview(result, revision_id))
            self._view_dirty = True
        except Exception as exc:
            self.inspector.appendPlainText("\n" + str(exc))

    def draw_overview(self, result, revision_id):
        if self.selection.revision_id != revision_id:
            return
        for item in list(self.overview.listDataItems()):
            self.overview.removeItem(item)
        self.draw_envelope(self.overview, result)

    def draw_envelope(self, plot, result):
        t = np.asarray(result["time"])
        minimum, maximum = np.asarray(result["minimum"]), np.asarray(result["maximum"])
        colors = ["#45E6D0", "#EF72C6", "#F4C469", "#A7BDFF"]
        for channel in range(minimum.shape[1]):
            x = np.repeat(t, 2)
            y = np.column_stack((minimum[:, channel], maximum[:, channel])).ravel()
            kwargs = {"pen": pg.mkPen(colors[channel % len(colors)], width=1)}
            if plot is self.plot:
                kwargs["name"] = f"Channel {channel + 1}"
            if result["step_frames"] == 1:
                plot.plot(t, minimum[:, channel], **kwargs)
            else:
                plot.plot(x, y, connect="pairs", **kwargs)

    def overview_selected(self):
        if not self._building_plot:
            self.plot.setXRange(*self.range_item.getRegion(), padding=0)

    def viewport_changed(self, *_):
        if not self._building_plot:
            self._view_dirty = True
            self.selection.viewport_generation += 1

    def refresh_view(self):
        if not self._view_dirty or not self.selection.revision_id:
            return
        if any(
            j.command.name == "media.envelope" and j.state in {"queued", "running"}
            for j, _ in self.pending.values()
        ):
            return
        self._view_dirty = False
        asset = self.session.asset(self.session.revision(self.selection.revision_id).asset_id)
        left, right = self.plot.getViewBox().viewRange()[0]
        start = max(0, min(asset.frames - 1, int(round(left * asset.sample_rate))))
        end = max(start + 1, min(asset.frames, int(round(right * asset.sample_rate))))
        self.selection.start_frame, self.selection.end_frame = start, end
        self.selection.viewport_generation += 1
        generation = self.selection.viewport_generation
        revision = self.selection.revision_id

        def ready(result):
            if generation != self.selection.viewport_generation or revision != self.selection.revision_id:
                return
            self._building_plot = True
            self.plot.clear()
            self.draw_envelope(self.plot, result)
            self.range_item.setRegion((start / asset.sample_rate, end / asset.sample_rate))
            self._building_plot = False

        try:
            region = Region(revision, start, end, tuple(range(min(asset.channels, 8))))
            self.track(
                self.session.waveform(region, pixels=max(200, min(2000, self.plot.width() * 3))), ready
            )
        except Exception as exc:
            self.inspector.appendPlainText("\n" + str(exc))

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open project", filter="CASSound (*.cassound)")
        if path:
            self.track(self.session.open_project(path), lambda result: self.project_opened(result))

    def project_opened(self, result):
        self.populate(modified=False)
        if result["missing_sources"]:
            QMessageBox.information(
                self,
                "Relink media",
                "Some originals are missing or changed. Select a source and use Relink source.",
            )

    def save_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save project", filter="CASSound (*.cassound)")
        if path:
            if not path.lower().endswith(".cassound"):
                path += ".cassound"
            portable = (
                QMessageBox.question(
                    self,
                    "Portable project",
                    "Embed original media for a portable project?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                == QMessageBox.Yes
            )
            self.track(self.session.save_project(path, portable=portable), lambda _: self.saved())
        else:
            self.close_after_save = False

    def saved(self):
        self.modified = False
        if self.close_after_save:
            self.close_after_save = False
            self.close()

    def export_dialog(self):
        item = self.sources.currentItem()
        if not item:
            return
        asset = self.session.asset(item.data(0, Qt.UserRole))
        profile, ok = QInputDialog.getItem(
            self,
            "Audio export",
            "Format / sample representation",
            ["WAV / PCM_24", "WAV / PCM_16", "WAV / PCM_32", "WAV / FLOAT", "FLAC / PCM_24"],
            0,
            False,
        )
        if not ok:
            return
        fmt, subtype = profile.split(" / ")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export separate audio file", filter=f"{fmt} (*.{fmt.lower()})"
        )
        if path:
            if not Path(path).suffix:
                path += "." + fmt.lower()
            self.track(
                self.session.render(
                    asset.original_revision_id, ExportSpec(path, format=fmt, subtype=subtype)
                ),
                lambda _: None,
            )

    def extract_dialog(self):
        item = self.sources.currentItem()
        if not item:
            return
        asset = self.session.asset(item.data(0, Qt.UserRole))
        path, _ = QFileDialog.getSaveFileName(
            self, "Copy original encoded audio stream", filter="Audio container (*.mka *.m4a *.wav *.flac)"
        )
        if path:
            if not Path(path).suffix:
                path += ".mka"
            self.track(self.session.extract_stream(asset.locator, path, asset.stream_index), lambda _: None)

    def relink_dialog(self):
        item = self.sources.currentItem()
        if not item:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Relink matching original")
        if path:
            self.track(self.session.relink(item.data(0, Qt.UserRole), path), lambda _: self.populate())

    def cancel_jobs(self):
        for job, _ in tuple(self.pending.values()):
            job.cancel()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.import_path(url.toLocalFile())

    def closeEvent(self, event):
        if self.modified:
            answer = QMessageBox.question(
                self,
                "Unsaved session",
                "Save this session before closing?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Save:
                self.close_after_save = True
                self.save_dialog()
                event.ignore()
                return
        if hasattr(self, "workbench_settings"):
            self.workbench_settings.setValue("layout", self.saveState())
            self.workbench_settings.setValue("precision", self.precision_action.isChecked())
        self.editor.close_audio()
        self.timer.stop()
        self.view_timer.stop()
        self.session.close()
        event.accept()
