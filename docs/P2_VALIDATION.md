# P2 verification record

28 October 2026 · CASSound 1.0.0.dev2 · Internal checkpoint toward one complete public release.

## Implemented surface

34 registered tasks cover the 26 P2 feature families: numerical statistics, spectra/PSD/STFT, features and queries, speech/music profiles, wavelets/CQT/mel/reassignment/synchrosqueezing, cepstrum/bicoherence, channel relationships, filter response, physical-unit models, pattern search, clustering, classifiers and supported tone-code profiles. The desktop, CLI and Python API use the same task registry and workers.

`P2_ACCEPTANCE.csv` maps each feature to its implementation and evidence. Implemented profiles have explicit parameter, duration, channel and size limits; advanced speech/activity estimates are not presented as clinical or universal classification results.

## Executed checks

**69 pytest cases passed**, including P1 regressions; see `p2-tests.xml`.

- Analytic RMS/energy/DC/crest and block-boundary zero crossings.
- FFT coherent amplitude, DC/Nyquist and zero-padding behavior.
- Streamed Welch PSD against SciPy across windows and detrending modes; linear PSD integration and band power.
- Chirp ridge timing, spectral peaks and harmonic families.
- Feature-grid continuity, interpolated frequency, prominence, silent invalidity and Boolean/hysteresis rules.
- Known channel delays, perfect-channel coherence and Hilbert envelope/frequency.
- Missing-fundamental pitch; synthetic voiced pulse measures and LPC resonance candidates.
- Known Butterworth cutoff and FIR group delay; quadratic-coupling bicoherence.
- Wavelet tone localization, cepstrum and actual optional-backend execution.
- Loudness gain consistency, intersample-peak reconstruction and synthetic EBU Tech 3342 reference profiles for 5/10/15/20 LU range.
- Known DTMF and SOS Morse signals; unsupported/absent signals are not silently promoted to authenticated messages.
- Template repetitions including a processing-block boundary; cross-source template search.
- Group-disjoint classifier evaluation, consistent training/prediction features and safe JSON model validation.
- Result caching/digests, numeric display previews, comparison views, source-relative timing and bounds.
- Saved query round trips, reviewed finding/project persistence, current-review JSON export and full array CSV export.
- Bad result paths, unit mismatch, impossible frequency bounds and missing harmonic assumptions rejected.

No claim of broad real-world detector accuracy is derived from these controlled fixtures.

## Desktop checks

`tools/gui_p2_smoke.py` drives the actual Qt shell through import, a source-frequency query, overview flags, linked spectrogram rendering and a two-window PSD comparison. The screenshot at `images/workbench-p2.png` is the running application with synthetic input, not a generated concept. The script fails on GUI error dialogs instead of hiding them. P1's source/zoom GUI smoke remains available separately.

## Frozen-runtime checks

A freshly installed headless wheel passed isolated package import without Qt, native source decoding, worker FFT execution and hashed-array retrieval. Both wheel and source distribution were built for this checkpoint.

A native Linux PyInstaller build passed the isolated CLI/worker/project/native-PCM checks and additionally executed STFT, pitch, loudness, CQT, synchrosqueezing, music features and clustering. Optional scientific package data/source required for JIT and dependency metadata are included by the build specification when installed.

These checks exercise frozen reentry and dynamic scientific dependencies. They do not constitute native Windows verification. The Windows workflow builds on Windows and then checks the binary on a separate runner without installing CASSound's Python dependencies into that verification environment.

## One-hour file result

The generated fixture is a one-hour, 48 kHz, stereo PCM16 WAV, 172,800,000 frames and 691,200,044 bytes. On this Linux environment:

| Operation | Wall time | Worker peak RSS |
|---|---:|---:|
| Import/native cache and envelopes | 18.15 s | 749,961,216 bytes |
| Source summary measurements | 3.06 s | 803,651,584 bytes |
| Streamed Welch PSD | 4.83 s | 821,997,568 bytes |
| Waveform overview, including worker launch | 0.103 s | See fixture report |

Raw measurements: `p2-long-file-result.json`; generator: `benchmarks/long_files.py --science`. This sparse-silence fixture tests duration, storage and streaming paths. It is not a performance claim for every codec, advanced transform, detector or machine. RSS includes resident file-backed pages; it is not a measurement of private Python heap allocation.

## Environment and reproducibility

Python 3.12.14 on Linux x86_64. Tested scientific packages include NumPy 2.5.4, SciPy 1.18.1, librosa 1.0.0, ssqueezepy 0.6.6, scikit-learn 1.9.1 and pyloudnorm 0.2.0. PySide6 6.12.0/PyQtGraph 0.14.0 supply the desktop. Exact dependency resolutions are in uv.lock; per-result provenance records library and decoder/sample-cache identity.

Run `uv sync --frozen --extra desktop --extra features --extra dev`, then `uv run --no-sync python -m pytest` and `uv run --no-sync ruff check src tests tools benchmarks`. The headless wheel uses NumPy/SciPy/SoundFile and does not import Qt. Rich optional algorithms require the features extra.

## Outstanding qualification

- Execute the native Windows/frozen GUI/hardware qualification on actual target systems. No Windows EXE is claimed tested here.
- Evaluate recognition, speech estimates and activity candidates on appropriately licensed, labeled real recordings with source-disjoint controls. Synthetic test success is narrower evidence.
- Complete the later-package editing/acoustics, message/forensic/model workflows, full GUI/accessibility polish and release qualification.
- LRA/reference-profile tests and a 4x peak estimate do not establish full EBU-mode/Annex-2 certification.

P2's numerical implementations and usable workflows are present. Field/platform qualification remains visible in the ledger rather than being replaced by unearned accuracy or readiness claims.
