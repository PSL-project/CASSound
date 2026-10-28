"""Exercise the real analysis/detection GUI with synthetic, ground-truth audio."""

import argparse
import tempfile
import time
from pathlib import Path
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication, QMessageBox
from cassound import Session, Region, AnalysisSpec, QuerySpec
from cassound.desktop.window import MainWindow
from cassound.desktop.theme import STUDIO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/images/workbench-p2.png")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        fs = 8000
        t = np.arange(fs * 4) / fs
        tone = (t >= 1.1) & (t < 2.7)
        x = 0.025 * np.sin(2 * np.pi * (300 * t + 180 * t * t)) + 0.4 * np.sin(2 * np.pi * 1000 * t) * tone
        path = root / "Signal laboratory — synthetic.wav"
        sf.write(path, x, fs, subtype="DOUBLE")
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        app.setStyleSheet(STUDIO)
        errors = []
        QMessageBox.warning = staticmethod(lambda parent, title, message: errors.append(str(message)))
        window = MainWindow(Session(root / "session"))
        window.resize(1600, 1100)
        window.show()
        window.import_path(str(path))

        def wait():
            deadline = time.monotonic() + 40
            idle = None
            while time.monotonic() < deadline:
                app.processEvents()
                if errors:
                    raise RuntimeError("GUI error: " + errors[-1])
                time.sleep(0.02)
                if window.session.assets() and not window.pending and not window._view_dirty:
                    idle = idle or time.monotonic()
                    if time.monotonic() - idle > 0.3:
                        return
                else:
                    idle = None
            raise RuntimeError("GUI scientific job did not complete")

        wait()
        asset = window.session.assets()[0]
        region = Region(asset.original_revision_id, 0, asset.frames, (0,))
        query = QuerySpec(
            {
                "all": [
                    {"metric": "dominant_frequency", "op": "between", "value": [985, 1015]},
                    {"metric": "rms", "op": ">", "value": 0.1},
                ]
            },
            parameters={"frame_length": 512, "hop_length": 128},
            min_duration=0.2,
        )
        window.science.submit(window.session.detect(region, query))
        wait()
        assert len(window.science.findings_model.rows) == 1
        window.science.submit(
            window.session.analyze(
                region, AnalysisSpec("spectral.stft", {"frame_length": 512, "hop_length": 64})
            )
        )
        wait()
        assert window.science.tabs.currentWidget() is window.science.image
        assert window.science.flags is not None
        window.grab().save(str(output))
        window.science.submit(
            window.session.analyze(
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
        wait()
        assert len(window.science.curve.listDataItems()) == 2
        window.modified = False
        window.close()
        app.processEvents()
    print("P2 GUI: source import, query, flags, linked spectrogram and comparison passed:", output)


if __name__ == "__main__":
    main()
