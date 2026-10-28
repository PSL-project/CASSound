"""Bounded live Qt checks with persistent diagnostics and no blocking error dialogs."""

import argparse
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.io import wavfile
from PySide6.QtWidgets import QApplication, QMessageBox
from cassound.api import Session
from cassound.desktop.window import MainWindow


def exercise(output):
    app = QApplication([])
    warnings = []

    def warning(parent, title, message, *args, **kwargs):
        warnings.append(str(message))
        return QMessageBox.Ok

    with tempfile.TemporaryDirectory() as directory, patch.object(QMessageBox, "warning", warning):
        root = Path(directory)
        source = root / "synthetic-reference.wav"
        fs = 48000
        t = np.arange(fs * 4) / fs
        wavfile.write(
            source,
            fs,
            (0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 1800 * t))
            * (0.7 + 0.3 * np.sin(2 * np.pi * t)),
        )
        session = Session(root / "cache")
        window = MainWindow(session)

        def wait_for(predicate, stage):
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                app.processEvents()
                if warnings:
                    raise RuntimeError(f"{stage}: GUI worker/callback error: " + "\n".join(warnings))
                if predicate():
                    return
                time.sleep(0.01)
            pending = [(job.command.name, job.state) for job, _ in window.pending.values()]
            raise RuntimeError(f"{stage} timed out; pending jobs: {pending}; view_dirty={window._view_dirty}")

        try:
            window.resize(1280, 800)
            window.show()
            window.import_path(source)
            wait_for(
                lambda: (
                    session.assets()
                    and not window.pending
                    and not window._view_dirty
                    and window.plot.listDataItems()
                ),
                "Import and detailed waveform",
            )
            for name in [
                "Analyze",
                "Edit",
                "Detect",
                "Messages",
                "Forensics",
                "Acoustics",
                "Reports",
                "Models",
            ]:
                window.route_workspace(name)
                app.processEvents()
            window.route_workspace("Analyze")
            wait_for(lambda: not window.pending and not window._view_dirty, "Workspace view settlement")
            window.science.run()
            wait_for(lambda: window.science.current is not None and not window.pending, "Scientific result")
            window.precision_action.setChecked(False)
            app.processEvents()
            output.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(output)):
                raise RuntimeError("Could not save Studio capture")
            window.precision_action.setChecked(True)
            app.processEvents()
            window.grab().save(str(output.with_name("cassound-precision.png")))
            window.route_workspace("Messages")
            app.processEvents()
            window.grab().save(str(output.with_name("cassound-messages.png")))
            if warnings:
                raise RuntimeError("GUI error: " + "\n".join(warnings))
            assert window.size().width() >= 1000
        finally:
            window.modified = False
            window.close()
            app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/images/cassound-studio.png"))
    args = parser.parse_args()
    log = args.output.with_suffix(".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        exercise(args.output)
    except BaseException:
        log.write_text(traceback.format_exc(), encoding="utf8")
        raise
    message = (
        "GUI import, detailed waveform, scientific result, eight workspace actions and both themes passed"
    )
    log.write_text(message + "\n", encoding="utf8")
    print(message)


if __name__ == "__main__":
    main()
