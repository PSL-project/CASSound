"""Real GUI import → nondestructive edit → undo/redo → multitrack mix → acoustic analysis."""

import argparse
import json
from pathlib import Path
import tempfile
import time
import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication, QMessageBox
from cassound import Session
from cassound.desktop.window import MainWindow
from cassound.desktop.theme import STUDIO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/images/workbench-p3.png")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(STUDIO)
    errors = []
    QMessageBox.warning = staticmethod(lambda p, t, m: errors.append(str(m)))
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        fs = 8000
        t = np.arange(fs * 2) / fs
        x = 0.2 * np.sin(2 * np.pi * (220 * t + 50 * t * t)) + 0.04 * np.sin(2 * np.pi * 1000 * t)
        sf.write(root / "Sweep study.wav", x, fs, subtype="DOUBLE")
        w = MainWindow(Session(root / "session"))
        w.resize(1660, 1120)
        w.show()
        w.import_path(str(root / "Sweep study.wav"))

        def wait():
            deadline = time.monotonic() + 60
            idle = None
            while time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
                if errors:
                    raise RuntimeError(errors[-1])
                if w.session.assets() and not w.pending and not w._view_dirty:
                    idle = idle or time.monotonic()
                    if time.monotonic() - idle > 0.3:
                        return
                else:
                    idle = None
            raise TimeoutError("P3 GUI job did not finish")

        wait()
        original = w.selection.revision_id
        w.editor.effects.setPlainText(
            json.dumps([{"type": "gain", "db": -6}, {"type": "fade", "in_frames": 1600, "out_frames": 1600}])
        )
        w.editor.apply_selection()
        wait()
        derived = w.selection.revision_id
        assert derived != original and len(w.session.assets()) == 2
        w.editor.history(False)
        wait()
        assert w.selection.revision_id == original
        w.editor.history(True)
        wait()
        assert w.selection.revision_id == derived
        w.editor.selection_clip()
        spec = json.loads(w.editor.timeline.toPlainText())
        clip = spec["tracks"][0]["clips"][0]
        spec["tracks"][0]["name"] = "Sweep / processed"
        spec["tracks"].append({"name": "Delayed layer", "gain_db": -8, "clips": [{**clip, "at": 4000}]})
        w.editor.timeline.setPlainText(json.dumps(spec, indent=2))
        w.editor.render_timeline()
        wait()
        rev = w.session.revision(w.selection.revision_id)
        assert rev.frames == 20000
        w.editor.tabs.setCurrentIndex(0)
        w.editor.parentWidget().raise_()
        app.processEvents()
        w.grab().save(str(output))
        assert output.is_file()
        w.editor.acoustic("acoustics.distortion", {"mode": "thd", "fundamental_hz": 1000, "harmonics": 3})
        wait()
        assert w.session.project.results
        w.modified = False
        w.close()
        app.processEvents()
    print("P3 GUI edit, undo/redo, layered timeline and acoustic result passed:", output)


if __name__ == "__main__":
    main()
