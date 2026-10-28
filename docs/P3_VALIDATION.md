# P3 verification record

28 October 2026 · CASSound 1.0.0.dev3 · Internal checkpoint toward one complete public release.

## Implemented surface

P3 covers CAS-037 through CAS-053: multitrack clips; immutable edit/render revisions and coordinate maps; gain/fades/routing/automation; filtering/EQ/dynamics; pitch/time/resampling; convolution/spectral editing/restoration; playback/capture and device inventory; generators/calibration/environment profiles; transfer/IR/room/distortion measurements; real isolated C++ VST3 effects hosting with parameter/state/latency/tail and failure handling. There are 39 shared scientific/measurement tasks after P3 integration.

The current dense editor bounds each audio array to 8,000,000 scalar samples. Tracks are accumulated into a running mix, not retained as an unbounded bank of full-sized buffers. This is separate from the existing streaming long-file analysis path. Native effects are float32, mono/stereo main-bus effects; vendor editors, instruments and sidechains are outside the documented host scope.

## Executed numerical and integration checks

The full suite passes 109 pytest cases, retaining P1/P2 regressions. Raw evidence is p3-tests.xml. It includes:

- Sample-value checks for gain, fades, automation, routing, cut/split/trim/silence/reverse, overlap/crossfade mixing and floating-point headroom.
- Butterworth cutoff gain, notch rejection, FIR delay and peaking-EQ response.
- Static compressor/expander/gate curves and explicit limiter sample ceiling.
- Polyphase sample-rate conversion and known-tone pitch/time changes.
- Convolution against direct reference, identity STFT reconstruction, selective spectral attenuation, noise-profile reduction and impulse replacement.
- Seed reproducibility and finite outputs for all supported generator families.
- Known FIR deconvolution/H1 transfer, delay, coherence and step/impulse responses. The deconvolution fixture includes response-tail capture; finite-record truncation is documented.
- Analytic exponential room-decay slopes, clarity values and rejection of low-validity/noise recordings.
- Known THD and selected two-tone IMD products; calibration-level round trip, chain mismatch and altered-profile digest rejection; ideal dry-air environment values.
- Repeat-render identities and samples, branching/undo/redo, portable project reopening with rendered sources and graph parents, derived-revision analysis/export, reference-minus-processed and null residuals, original finding coordinates, failed-render nonpublication and clipping refusal.
- Headless generator/edit CLI execution and exact numerical output.
- Device-free fake-driver tests for blocking transport selection, loop/seek/stop, underflows, finite capture length, overflow/disconnect failure and recovery. These verify contracts, not a real audio device.

The environment is Linux x86_64, Python 3.12.14, NumPy 2.5.4, SciPy 1.18.1, librosa 1.0.0, PySide6 6.12.0, PyQtGraph 0.14.0 and sounddevice 0.5.6. uv.lock records install resolutions. Librosa 1.0 emits two upstream deprecation warnings while executing its working time-stretch path; tests do not suppress them or reinterpret them as numerical failures.

## Native host evidence

The host and fixture compile with the pinned VST3 SDK v3.8.1_build_84 (MIT) and nlohmann/json v3.12.0 (MIT). The controlled effect exercises normalized gain, a seven-sample delay, known decaying tail and state. Tests verify compensated sample values, retained tail, state replay, intentional process abort, quarantine, explicit retry, hanging-process timeout, outer job cancellation and continued scientific analysis afterward. Failed plugin renders do not publish a dry output as processed audio.

The official SDK mda bundle was also built. Thirty non-instrument audio-effect classes scan successfully. mda Delay, mda Dynamics and mda Stereo Simulator pass stereo offline processing, finite output, explicit tail retention and exact repeat-render sample checks. This is the executed compatibility list; it does not imply every class scanned or every third-party effect has passed processing tests. Native Windows builds and plugins have not been executed in this Linux environment.

## Desktop and distribution checks

The real Qt GUI smoke imports a synthetic source, applies effects, selects the new revision, undoes/redoes, renders a layered timeline and requests an acoustic result. The running application is shown in images/workbench-p3.png. The GUI has an effects rack, timeline/clip controls, generators, explicit audio controls/device inventory, calibration/acoustic actions and a native effect generic-parameter/state interface. Heavy work goes through shared jobs; GUI errors fail the smoke.

Native Windows build workflows include native host/fixture construction, contract tests and a separate frozen-runtime runner. Physical device routing, WASAPI exclusive behavior, ASIO availability, disconnect/recovery on actual drivers, listening assessments and Windows clean-machine qualification remain unexecuted gates here.

## Limits retained in the product record

Scientific acoustic checks use controlled fixtures. No ISO 3382, true-peak limiter, hardware-distortion accuracy, universal restoration quality or vendor-plugin compatibility certification is claimed. The microphone/device/gain chain is user-identified and needs a valid reference calibration. Recognition/forensic/model qualification from later packages remains separate. Full GUI polish/accessibility and final Windows release packaging remain P5/P6 work.
