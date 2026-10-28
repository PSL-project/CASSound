"""Render the real Qt shell with a generated recording and exercise its job path."""

import argparse
from pathlib import Path
import tempfile
import time
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication
from cassound import Session
from cassound.desktop.window import MainWindow
from cassound.desktop.theme import STUDIO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/workbench.png")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        t = np.arange(48000 * 12) / 48000
        gate = np.where((t > 4.5) & (t < 5.8), 1, 0.28)
        data = np.column_stack(
            (0.55 * np.sin(2 * np.pi * 440 * t) * gate, 0.28 * np.sin(2 * np.pi * (180 * t + 20 * t * t)))
        )
        path = root / "CASSound synthetic fixture.wav"
        sf.write(path, data, 48000, subtype="PCM_24")
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STUDIO)
        window = MainWindow(Session(root / "session"))
        window.show()
        window.import_path(str(path))
        deadline = time.monotonic() + 30
        complete = None
        while time.monotonic() < deadline:
            app.processEvents()
            if window.session.assets() and not window.pending and window.plot.listDataItems():
                complete = complete or time.monotonic()
                if time.monotonic() - complete > 0.5:
                    break
            time.sleep(0.02)
        else:
            raise RuntimeError("GUI import/waveform workflow did not complete")
        assert window.session.assets()[0].frames == len(t)
        window.plot.setXRange(4.48, 4.52, padding=0)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
            if not window._view_dirty and not window.pending:
                break
        else:
            raise RuntimeError("Zoom refresh did not complete")
        window.grab().save(str(output))
        # Bypass the interactive unsaved-close prompt in this scripted smoke test.
        window.modified = False
        window.close()
        app.processEvents()
    print(f"Real Qt import and waveform smoke passed: {output}")


if __name__ == "__main__":
    main()
