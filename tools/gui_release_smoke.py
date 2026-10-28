"""Bounded live Qt startup/workspace checks and a real application screenshot."""

import argparse
import tempfile
import time
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from PySide6.QtWidgets import QApplication
from cassound.api import Session
from cassound.desktop.window import MainWindow


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=Path("docs/images/cassound-studio.png"))
    a = p.parse_args()
    app = QApplication([])
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
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
        window.resize(1280, 800)
        window.show()
        window.import_path(source)
        deadline = time.monotonic() + 40
        while (window.pending or not session.assets()) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if not session.assets():
            raise RuntimeError("GUI import did not complete")
        for name in ["Analyze", "Edit", "Detect", "Messages", "Forensics", "Acoustics", "Reports", "Models"]:
            window.route_workspace(name)
            app.processEvents()
        window.route_workspace("Analyze")
        window.science.run()
        deadline = time.monotonic() + 40
        while (window.pending or not window.plot.listDataItems()) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if not window.plot.listDataItems():
            raise RuntimeError("Detailed source waveform did not arrive")
        window.precision_action.setChecked(False)
        for _ in range(30):
            app.processEvents()
            time.sleep(0.01)
        a.output.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(a.output))
        window.precision_action.setChecked(True)
        app.processEvents()
        window.grab().save(str(a.output.with_name("cassound-precision.png")))
        window.route_workspace("Messages")
        app.processEvents()
        window.grab().save(str(a.output.with_name("cassound-messages.png")))
        assert window.size().width() >= 1000
        window.modified = False
        window.close()
        app.processEvents()
    print("GUI import, eight workspace actions and both themes passed")


if __name__ == "__main__":
    main()
