# Isolated CASSound VST3 effects host

A real C++17 offline host using the MIT-licensed VST3 3.8.1 SDK. Python sends bounded JSON control files and interleaved float32 audio to a disposable executable. Plugins never load into the Qt/Python process. This is crash isolation, not an operating-system security sandbox.

## Build

Windows x64 needs Visual Studio 2022 Build Tools with Desktop development with C++, Windows SDK, Git and CMake >=3.25. Python remains the application/science language; Rust/Node/JavaScript are not required. On Linux, use a C++17 toolchain for verification.

From the repository root:

```console
python tools/build_native.py --fetch --mda
```

Pinned dependencies are VST3 SDK `3cdf9ca5d1f5b1b21e0a86832aa4abe55607bd96` (v3.8.1_build_84, with recorded submodule revisions) and nlohmann/json `55f93686c01528224f448c19128836e7df245f72` (v3.12.0). The helper fetches into .native-deps and builds into build/native. Fetching needs a network connection; repeat builds can omit --fetch. The SDK/JSON sources are external build dependencies and are not vendored in the source archive.

Set `CASSOUND_VST3_HOST` to the built `cassound-vst3.exe` (Windows, often under Release) or `cassound-vst3` (Linux). The build helper copies dependency license texts into `licenses` beside the build. When freezing, keep that directory beside the host executable and set this environment variable so the packaging specification includes it. The fixture is a test plugin, not an audio-production effect.

## Contract

- Explicit file/bundle selection, SHA-256 content inventory, class enumeration, per-class generic normalized parameters. Instruments are excluded.
- Float32 offline processing, mono/stereo main input/output. Additional buses are disabled; no sidechain/MIDI/instrument or vendor editor hosting.
- Processor/controller initialization and connection via PlugProvider; component-state restoration and synchronization to controller; parameter queues at block offset zero; state capture after rendering.
- Static plugin-reported latency is removed after feeding trailing zeros. Tail retention is explicit in seconds (0–10); metadata records reported/rendered tail and truncation. Without an explicit tail setting, finite reported tails up to ten seconds are accepted; longer/infinite tails require a choice.
- Plugins with changing latency, unsupported bus layouts, failed setup/state, nonfinite output, crashes or hangs fail the render. There is no dry-output success fallback.
- Each scan/render uses a new child process. Scan failure and render failure quarantine that exact plugin digest until an explicit scan retry. Outer worker cancellation terminates its process tree; plugin timeout prevents indefinite waiting.
- Plugins can execute arbitrary native code with the user's permissions. Isolation does not establish trust, codec safety or compatibility with untested effects.

`request.json` and `response.json` protocol version is 1. Native results are local worker data, never instructions. Audio spools live in the worker staging directory and are removed after completion/cancellation.

## Verification

`cassound-testfx` supplies known gain, a seven-sample delay, a decaying tail, component state and intentional crash/hang modes. Native tests verify waveform values, compensated latency, state replay, finite tails, scan quarantine, timeout and session recovery. Optional `--mda` builds the official SDK mda bundle for additional effect compatibility checks. See ../../docs/P3_VALIDATION.md for executed results and platform limits.

VST is a trademark of Steinberg Media Technologies GmbH. No compatibility logo or endorsement is claimed. SDK and nlohmann/json attribution must accompany distributed native builds.
