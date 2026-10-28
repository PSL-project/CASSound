"""Bounded workspace routing, exact selections, palettes and persistent layouts."""

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QDockWidget, QToolBar, QInputDialog, QLineEdit, QLabel, QApplication
from PySide6.QtGui import QAction, QKeySequence
import pyqtgraph as pg
from .laboratory import LaboratoryPanel
from .theme import STUDIO
from ..domain.types import Region

PRECISION = """QWidget { background:#f4f7fb; color:#13243a; font-family:'Segoe UI'; font-size:13px; }
QLineEdit,QPlainTextEdit,QTreeWidget,QTableView,QComboBox,QSpinBox {background:white; color:#13243a; border:1px solid #718198;padding:4px;}
QPushButton,QToolButton {background:#dde7f3;color:#112944;border:1px solid #627c98;padding:6px;}
QPushButton:focus,QToolButton:focus,QLineEdit:focus {border:2px solid #006174;}
QDockWidget::title,QToolBar {background:#dae4ef;color:#172e47;padding:5px;}
QHeaderView::section {background:#dae4ef;color:#13243a;padding:4px;}
QLabel#brand {color:#005d71;font-size:22px;font-weight:bold;}
"""


def install(window):
    settings = QSettings("CASSound", "CASSound")
    window.workbench_settings = settings
    for i, dock in enumerate(window.findChildren(QDockWidget)):
        if not dock.objectName():
            dock.setObjectName("dock_" + str(i))
    for i, toolbar in enumerate(window.findChildren(QToolBar)):
        if not toolbar.objectName():
            toolbar.setObjectName("toolbar_" + str(i))
    science = next(d for d in window.findChildren(QDockWidget) if d.widget() is window.science)
    editor = next(d for d in window.findChildren(QDockWidget) if d.widget() is window.editor)
    panels = {}
    for kind, title in [
        ("messages", "MESSAGES"),
        ("forensics", "FORENSICS"),
        ("models", "MODELS"),
        ("reports", "REPORTS"),
    ]:
        panel = LaboratoryPanel(window, kind)
        dock = QDockWidget(title, window)
        dock.setObjectName("lab_" + kind)
        dock.setWidget(panel)
        window.addDockWidget(Qt.BottomDockWidgetArea, dock)
        window.tabifyDockWidget(science, dock)
        panels[kind] = (panel, dock)
    window.laboratory_panels = panels
    window.messages = panels["messages"][0]
    navigation = QToolBar("Workspaces")
    navigation.setObjectName("workspace_navigation")
    window.addToolBarBreak()
    window.addToolBar(navigation)

    def show(dock):
        dock.show()
        dock.raise_()

    def route(name):
        if name in {"Analyze", "Detect"}:
            show(science)
            if name == "Detect":
                window.science.tabs.setCurrentWidget(window.science.table)
        elif name in {"Edit", "Acoustics"}:
            show(editor)
            if name == "Acoustics":
                for i in range(window.editor.tabs.count()):
                    if "acoust" in window.editor.tabs.tabText(i).lower():
                        window.editor.tabs.setCurrentIndex(i)
                        break
        else:
            show(panels[name.lower()][1])

    window.route_workspace = route
    for name in ["Analyze", "Edit", "Detect", "Messages", "Forensics", "Acoustics", "Reports", "Models"]:
        action = navigation.addAction(name)
        action.triggered.connect(lambda checked=False, n=name: route(n))
    workspace_navigation = navigation
    navigation = QToolBar("Selection and layout")
    navigation.setObjectName("selection_toolbar")
    window.addToolBarBreak()
    window.addToolBar(navigation)
    start = QLineEdit("0")
    end = QLineEdit("0")
    start.setMaximumWidth(110)
    end.setMaximumWidth(110)
    navigation.addWidget(QLabel("Exact frames [start,end)"))
    navigation.addWidget(start)
    navigation.addWidget(end)
    window.exact_start = start
    window.exact_end = end

    def exact():
        try:
            revision = window.session.revision(window.selection.revision_id)
            a = int(start.text())
            b = int(end.text())
            Region(revision.revision_id, a, b, (0,))
            if b > revision.frames:
                raise ValueError("Selection exceeds source frames.")
            window.selection.start_frame = a
            window.selection.end_frame = b
            window.plot.setXRange(a / revision.sample_rate, b / revision.sample_rate, padding=0)
        except Exception as exc:
            window.statusBar().showMessage(str(exc), 5000)

    navigation.addAction("Select frames", exact)

    def theme(precision):
        QApplication.instance().setStyleSheet(PRECISION if precision else STUDIO)
        for plot in window.findChildren(pg.PlotWidget):
            plot.setBackground("#ffffff" if precision else "#0b1019")
            for axis in ["left", "bottom", "top", "right"]:
                plot.getAxis(axis).setPen("#20344e" if precision else "#a2b3c8")
                plot.getAxis(axis).setTextPen("#20344e" if precision else "#a2b3c8")
        window.precision_mode = precision

    precision = QAction("Precision", window)
    precision.setCheckable(True)
    precision.toggled.connect(theme)
    navigation.addAction(precision)
    window.precision_action = precision

    def palette():
        actions = [a for a in window.findChildren(QAction) if a.text() and a.isEnabled()]
        names = [a.text() for a in actions]
        chosen, ok = QInputDialog.getItem(window, "Command palette", "Action", names, 0, True)
        if ok and chosen in names:
            actions[names.index(chosen)].trigger()

    command = QAction("Command palette", window)
    command.setShortcut(QKeySequence("Ctrl+K"))
    command.triggered.connect(palette)
    window.addAction(command)
    navigation.addAction(command)

    def save_layout():
        name, ok = QInputDialog.getText(window, "Save layout", "Layout name")
        if ok and name and len(name) < 128:
            settings.setValue("layouts/" + name, window.saveState())

    def load_layout():
        settings.beginGroup("layouts")
        names = settings.childKeys()
        settings.endGroup()
        name, ok = QInputDialog.getItem(window, "Load layout", "Layout", names, 0, False)
        if ok:
            window.restoreState(settings.value("layouts/" + name))

    default = window.saveState()
    navigation.addAction("Save layout", save_layout)
    navigation.addAction("Load layout", load_layout)
    navigation.addAction("Reset layout", lambda: window.restoreState(default))
    state = settings.value("layout")
    if state:
        window.restoreState(state)
    window.addToolBar(Qt.TopToolBarArea, workspace_navigation)
    window.insertToolBarBreak(workspace_navigation)
    window.addToolBar(Qt.TopToolBarArea, navigation)
    window.insertToolBarBreak(navigation)
    precision.setChecked(settings.value("precision", False, type=bool))
    science.raise_()
    window.overview.setMaximumHeight(110)
    window.plot.setMinimumHeight(130)
    for panel, dock in panels.values():
        panel.setMinimumHeight(240)
