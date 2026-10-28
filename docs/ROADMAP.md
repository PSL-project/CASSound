# Roadmap — six internal packages, one public release

The purpose of six packages is to bound ownership and integration while retaining the entire 1.0 feature scope. They are substantial work units, not promises that each can be completed in a single response. Internal checkpoints preserve work; none is a reduced public release.

## Execution order

1. P1 establishes contracts, storage, worker execution, media and a thin desktop shell supplied by P5.
2. P2 implements analysis/detection against P1. P5 connects views as soon as results exist.
3. P3 implements editing, acoustics, playback integration and native plugin hosting using P1/P2.
4. P4 implements messages, forensics and model-backed methods using P1/P2/P3.
5. P5 completes every workspace and full user journey, including all cross-module connections.
6. P6 hardens and packages the integrated application; its build harness begins during P1.

This is dependency order, not a reason to leave the GUI and packaging until the end. P5's shell and P6's Windows smoke build start with P1. No additional agents are required by this plan.

## P1 — foundation, contracts and media runtime

Owned: `domain/`, `application/`, `runtime/`, `api.py`, `bootstrap.py`, `cli.py`, basic `__main__.py`, and foundational project/worker tests.

Build: immutable media and revision identities; source-frame/time maps; safe project bundle; typed commands; job lifecycle; cache; media probing/decoding/export; exact integer sample access; migrations; model/backend capability inventory; CLI bootstrap. All importing is local and read-only with respect to originals.

Integrate: P5 adds a real window with import, sources, selection and job feedback. P6 creates a minimal native Windows executable smoke build early, so frozen-worker problems are found immediately.

Exit evidence:

- Open audio and a video with multiple audio streams; choose the correct stream and retain timestamps.
- Exact PCM fixtures survive the exact-read path; DSP conversion is explicit.
- Save/reopen/relink a project without losing revision/sample identity.
- Cancel a decode, restart a worker and recover an interrupted save safely.
- CLI, Python and GUI use the same command/service entry points.
- Native Windows executable opens a fixture on a clean machine.

## P2 — scientific analysis, recognition and queries

Owned: `engine/dsp.py`, `engine/features.py`, `engine/detection.py`, `engine/patterns.py`, `engine/registry.py`, scientific fixtures and numerical tests.

Build: all time/frequency/time-frequency metrics; transforms; loudness/true peak; channel relationships; pitch/formants; spectral descriptors; higher-order statistics; classifiers/clustering/template search; a typed rule engine; source-resolution annotations; method comparison and uncertainty/units records.

Exit evidence:

- Known signals produce correct amplitudes, energies, PSD normalization and harmonic results.
- Chunked and whole-array calculations agree within documented tolerances.
- Synthetic and labeled real recordings exercise false positives and missed events.
- A saved query flags a tone family and moves every linked view to a result.
- Unsupported physical units are refused with a useful explanation.
- Experimental methods produce real results and evaluation artifacts.

## P3 — editing, live I/O, acoustics and plugin host

Owned: `engine/editing.py`, `engine/acoustics.py`, `engine/generation.py`, `runtime/playback.py`, `runtime/vst3.py`, `native/vst3_host/`, editing/acquisition tests.

Build: multitrack clips, nondestructive render graph, effects, automation, spectral masks, time/pitch transformations, restoration, recording/playback, generators, calibration, measured transfer/impulse responses, room metrics, distortion and isolated VST3 effects hosting.

Plugin scope: Windows x64 VST3 audio effects; enumerate parameters, save/load state, offline processing with latency/tail handling, bypass and generic parameter UI. Support documented bus layouts and a tested compatibility list. Arbitrary VST instruments, every historical plugin format and embedded vendor editors are not part of this bounded feature. The native host must not run inside the GUI process.

Exit evidence:

- Undo/redo, branching, save/reopen and repeat renders agree.
- Rendered revisions invalidate affected results and keep originals intact.
- Recording survives a device change with a clear error/recovery state.
- Calibration is attached to the correct chain; unsupported measurements remain unavailable.
- An intentionally crashing test plugin does not crash the GUI; known effects have validated latency/state/tail behavior.
- Edited and original coordinates remain explainable after trimming, resampling and time stretching.

## P4 — messages, forensics and model integrations

Owned: `engine/messages.py`, `engine/forensics.py`, `engine/models.py`, `engine/robustness.py`, `runtime/models.py`, message/forensic/model tests.

Build: versioned encrypted payloads; LSB, FSK and documented advanced embedding/modulation methods; error correction; visible decode stages; watermark adapters; robustness sweeps; hashes and provenance; alignment/residuals; ENF reference comparison; codec/device-profile experiments; edit candidates; deepfake and restoration/separation models; reports.

Exit evidence:

- Binary and multilingual text round trips work; tampering/wrong keys never yield a verified message.
- Every supported carrier has a profile and measured recoverability limits.
- Robustness sweeps report packet/bit errors and quality metrics without fabricating results for failed decodes.
- Forensic claims are evidence-linked; experimental scores remain scores unless calibration supports probabilities.
- Model weights have checksums, licenses, valid preprocessing, known test sets and offline installation.
- No message plaintext or secret enters the saved trace by default.

## P5 — polished desktop and complete workflows

Owned: `desktop/`, `resources/`, GUI tests and design snapshots. Starts with P1 and finishes after P4.

Build: custom visual system, dockable workspaces, linked plots, timeline/multitrack editor, query builder, measurement inspector, messages microscope, forensic comparison, acoustic setup, model manager, job feedback, reports and settings. Every capability comes from real registry/services, never a disconnected UI catalog.

Exit evidence:

- Full import → inspect → detect → edit → compare → encode/extract → report → save/reopen journey works.
- Drag/drop, keyboard navigation, zoom/selection and exact sample entry are consistent.
- 100%, 150% and 200% DPI, smaller displays and high-contrast mode are verified.
- Empty/error/cancelled/missing-model/missing-source states are designed and exercised.
- UI remains responsive during long jobs; results from old revisions cannot overwrite current views.
- Every feature has a discoverable workspace/action and real capability status.

## P6 — integration, verification and release bundle

Owned: `tools/`, `packaging/`, `.github/`, `docs/`, release verification and installer/build manifests. Build harness begins with P1; final acceptance follows P5.

Build: dependency locks, licenses/SBOM, CI, wheel/sdist, frozen app, portable bundle, installer, offline model packs, documentation, examples, release report, checksum generation and archive hygiene.

Exit evidence:

- Every row in `FEATURES.csv` has an evidence reference and passes its completion check.
- Clean Windows machine runs the packaged app without Python/compiler tools.
- Wheel installs and headless API runs without Qt/audio devices.
- Frozen workers, media tools, plugin host and models work from the packaged paths.
- Model pack and media/backend capabilities match the advertised release inventory.
- No credentials, personal recordings, caches or development-only paths appear in distributables.
- Sign if credentials are available; otherwise accurately label unsigned artifacts and document the optional signing path. Lack of a signing identity must not cause fake signing claims.

## Working discipline

At each package checkpoint commit source, update the feature ledger and save commands/results in a progress record. Integration is continuous. A failed acceptance check blocks the owning feature; never mark it complete because its source file exists. Do not replace original recordings or silently drop difficult scope. Any scope change must name the feature and reason.

No calendar estimate is asserted before P1 resolves packaging, backend, memory and model feasibility. The full scope is a substantial engineering project; the plan reduces coordination overhead, not the amount of implementation and validation.
