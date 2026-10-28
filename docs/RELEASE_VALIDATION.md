# CASSound 1.0.0rc1 validation

29 October 2026. This record distinguishes working implementation, executed checks and remaining qualification. Source packaging is not a claim of certified measurements or a tested Windows binary.

## Executed

- **129 pytest cases passed**, retaining P1–P3 and new release coverage. Raw evidence: `release-tests.xml`. Four upstream librosa deprecation warnings refer to its unused `hop_length`/`n_fft` parameters; they were not suppressed or counted as failures.
- Binary/Unicode and native integer LSB round trips; all nine synthesized signal profiles with pilot synchronization, gain and a prepended delay; actual spread/echo/phase/transform host modification; wrong credentials, altered ciphertext, raw keys, bounded parser/decompression and majority correction.
- Known delay/gain/null residual, ENF frequency and supplied-reference fixtures. These controlled checks do not establish edit-detector precision on real-world recordings.
- One shared-worker workflow: encrypted payload → WAV → authenticated recovery → media import → Welch analysis → JSON/CSV/Parquet/PNG/SVG/PDF/HTML export → portable project save. Credentials and message plaintext are absent from saved project history.
- Real CPU weights: GTCRN finite denoised output; AudioSeal generator/detector with exact 16-bit recovery; a non-detected unmarked synthetic control; actual INT8 wav2vec2 inference. See `model-evidence.json`, pinned catalog and bundled model notices. This is execution evidence, not unseen-source accuracy or listening-quality evidence.
- Live Qt import, completed detailed waveform, analysis, eight workspace actions, Studio/Precision modes and 1280×800 rendering. Screenshots under `images/` are actual application captures.
- Native Linux PyInstaller GUI/console build. Packaged CLI executed encrypted file recovery, report backends and installed CPU model packs outside the checkout with source import paths removed. Frozen GUI startup passed. Source wheel/sdist build and fresh headless install are recorded in the final artifact inventory.
- Ruff and archive integrity checks; dependency/artifact versions and SHA-256 inventory. No signing identity was used.

The main full suite was run once near integration completion. Subsequent bounded changes to exact GUI frame bounds, toolbar placement, statistical-model file outputs and model duration limits received focused checks; the suite was not repeatedly restarted after cosmetic or documentation updates.

## Operating limits

Dense audio arrays are limited to 8M scalar samples. Neural inputs are mono 16 kHz after explicit conversion; GTCRN/deepfake accept 1–600 seconds and AudioSeal accepts at most 30 seconds per run. Parquet export is bounded to 250k values, line plots to 100k points, image plots to 2M cells. HTML is explicitly a bounded numerical preview. Streaming analysis and envelope navigation have separate disk-backed limits.

CASM and carrier definitions are in CASM.md. Synthesis is separate from real host embedding. LSB is fragile under sample changes; host embedding alters audio and may add a synchronization prefix. Error correction and authentication are distinct. Sweeps report failed/absent/authentication outcomes without claiming successful recovery, and leave BER unset when synchronization is absent. A single unmarked control is not a false-positive-rate benchmark.

Forensic/profile/model outputs are candidates, distances or scores. ENF matching is to a supplied reference, with overlap and ambiguity limits; it cannot independently date a recording. Native/plugin/audio-device support depends on actual platform backends. Existing VST3 validation covers the controlled fixture and selected mda effects, not every third-party plugin.

## Remaining qualification

- Native Windows executable/installer production, clean-machine launch and worker/model/plugin validation. CI/build definitions are supplied; these have not been run in this Linux environment. No Windows `.exe` is included or implied by a Linux runtime check.
- Physical audio hardware, WASAPI/ASIO availability, exclusive/device-change behavior, real capture/loopback routing and listening assessments.
- Windows display scaling at 100/150/200 percent, screen-reader and broader accessibility checks. Offscreen 1280×800 checks do not replace them.
- Source-disjoint labeled deepfake/recognition and edit-candidate datasets, calibrated probabilities/false-positive estimates, broader codec/channel/host material and wider third-party effect compatibility. `tools/evaluate_models.py` evaluates a user-supplied labeled CSV; no benchmark result was fabricated.
- Signing with an owner-provided certificate. No signed or certified artifact claim is made.

These gates remain visible in FEATURES.csv. They must be resolved with real evidence before promoting this candidate to a fully qualified stable Windows release.

Packaging correction: the broad frozen check identified a missing dynamically imported loudness backend. The spec now explicitly includes the optional analysis backends; the corrected frozen check passed STFT, pitch, loudness, CQT, synchrosqueezing, music, clustering, native effect processing, exact PCM and portable graphs. Final frozen GUI startup exited successfully. A focused stereo microscope check confirmed scalar-sample to source-frame navigation.

## Windows runtime packaging correction — 29 October 2026

The supplied Windows verification log passed generation/editing, portable graphs,
VST3 scan/process, STFT, pitch, loudness, CQT, synchrosqueezing and music features.
Clustering then failed when scikit-learn tried to load
`sklearn/.libs/vcomp140.dll`. Inspection of the locked 1.9.1 Windows wheel confirmed
that `_distributor_init.py` explicitly loads both `vcomp140.dll` and `msvcp140.dll`
from that directory. This is a missing bundled runtime, not a removed task.

The local hook now collects package-contained libraries at their original paths,
plus sibling `scikit_learn.libs` libraries and load-order files when present.
Desktop builds audit installed scikit-learn's wheel manifest against the final
bundle before producing a successful build result. All scientific/model/plugin
verification commands remain enabled. Verification logs are retained on failure.

Executed for this correction:

- Six focused regressions passed: three packaging cases, plus the three existing
  Windows mapping/Unicode regressions. Packaging cases cover both DLL layouts,
  relocated/missing `vcomp140.dll` and relocated/missing `msvcp140.dll`.
- The real locked Windows wheel was downloaded and inspected without executing
  Windows binaries. The real PyInstaller collectors emitted both DLLs with
  destination `sklearn/.libs`.
- A fresh Linux PyInstaller desktop/CLI build passed all seven frozen scientific
  tasks (including `patterns.cluster`), generation/editing, portable projects,
  isolated workers and exact PCM export outside the checkout.
- The freshly built CLI passed encrypted QPSK payload recovery and SVG, PNG, PDF,
  HTML and Parquet exports. Frozen GUI startup returned exit 0.
- Python wheel/sdist builds succeeded; the source archive contains the hook and
  bundle auditor. Ruff and diff whitespace checks passed.

No feature was removed or disabled. This correction was not executed on a native
Windows runner locally. A new Windows workflow run on the corrected commit must
validate the rebuilt bundle; rerunning only verification against an old artifact
cannot pick up this source fix. CPU models and the native VST3 host were not
rebuilt or retested in this local packaging correction.
