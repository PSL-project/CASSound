# P1 verification record

28 October 2026. This records executed checks for the P1 source checkpoint; P2–P6 are not claimed complete.

## Executed successfully

- **25 pytest cases** passed; raw test report: `p1-tests.xml`.
- Exact import/export of unsigned 8-bit, signed 8/16/24/32-bit PCM and float32/float64 fixtures; multichannel values preserved without source modification.
- Audio-stream selection and decoded frequency check in a generated multistream video; stream-copy extraction preserves the tested PCM content.
- Peak-preserving overview and sample-level detail; region/channel/pixel bounds rejected correctly.
- External and portable projects round-trip, including unicode filenames, hash-checked missing-source relinking, large metadata and source-identity retention.
- Unsafe archive paths and changed SQLite data rejected; failure leaves the active project usable.
- Cancellation before/during work, killed-worker recovery, session-directory ownership and pinned-cache pruning exercised.
- CLI JSON and headless imports exercised; no Qt import when importing the core.
- Real Qt shell imported a generated two-channel recording, rendered its waveform, zoomed the view and exported the screenshot at `images/workbench.png`. The fixture is synthetic; it is not a concept image.
- Python wheel and source distribution built. A fresh headless wheel environment installed and ran doctor outside the source tree with no PySide6 installed. A Linux PyInstaller bundle passed isolated CLI, dedicated child worker, portable project and exact PCM32 export checks. These are Linux results, not native Windows results.
- Ruff checks and Python compilation passed.

## Long-file result

`benchmarks/long_files.py` generated a one-hour 48 kHz stereo PCM16 WAV (172,800,000 frames, 691,200,044 bytes), imported it and requested an overview. Measured import: **17.18 s**, worker peak RSS: **749,002,752 bytes**, overview request including a child-worker startup: **0.118 s**, output: **2,637 envelope positions**. See `long-file-result.json`.

This fixture is sparse silence. It exercises duration, storage, chunking and envelope construction; it does not establish performance for compressed media, every channel count, detectors or ML. Results describe this Linux environment, not a Windows hardware promise.

## Environment

Linux x86_64, Python 3.12.14, NumPy 2.5.4, SoundFile 0.14.0, PySide6 6.12.0, PyQtGraph 0.14.0, pytest 9.2.0, PyInstaller 6.22.3. Third-party media tools were system-installed. Development dependencies are locked in uv.lock.

## Outstanding P1 platform acceptance

**Native Windows executable verification has not run in this environment.** The supplied workflow builds on Windows, then downloads the app into a separate runner job and verifies its native sample path, worker reentry, portable project and PCM export without installing CASSound dependencies or adding the source tree to imports. It restricts the child PATH to Windows system directories. Execute this workflow before marking the Windows gate verified.

Windows audio hardware, installer qualification, final visual/accessibility polish and the full 1.0 feature set have separate later-package checks. FFmpeg remains external in this checkpoint. Portable sources are stored losslessly; decoded sample caches are recreated when opening projects. Existing schema version 1 is supported; unknown future versions are rejected. A migration to a new schema will be implemented with that schema rather than inventing a fictitious old format.
