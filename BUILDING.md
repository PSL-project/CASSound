# Building CASSound

## Dependencies

Python 3.12+ and pip or uv are sufficient for the Python package and desktop. Install `.[full,dev]` or `uv sync --frozen --extra full --extra dev`. The base includes NumPy, SciPy, SoundFile and cryptography. Optional groups provide PySide6/PyQtGraph, sounddevice, librosa/ssqueezepy/scikit-learn/pyloudnorm, ONNX Runtime, Matplotlib and Arrow. `uv.lock` records exact resolutions.

Install FFmpeg and ffprobe on PATH for broad media decoding, stream extraction and codec sweeps. They are not redistributed in this source bundle; the selected build determines codec availability and license obligations. No Rust or JavaScript toolchain is required.

Native VST3 hosting additionally needs Git, CMake >=3.25, a C++17 compiler, and on Windows Visual Studio 2022 Build Tools with C++ desktop workload and Windows SDK. `tools/build_native.py` pins the MIT VST3 SDK and nlohmann/json sources.

## Python distributions

```powershell
python -m pip install ".[full,dev]"
python tools/build_release.py --target python
```

Wheel and source distribution appear under `dist/`. Install the wheel in a fresh environment to check headless use; optional dependencies are selected by extras.

## Native Windows application

Build on Windows x64:

```powershell
python tools/build_native.py --fetch
```

`native_test_environment.py` writes variables for GitHub Actions. Outside Actions, set `$env:CASSOUND_VST3_HOST` to the built `cassound-vst3.exe` path; keep its `licenses` directory beside it. Then:

```powershell
python tools/build_release.py --target desktop --require-windows
```

`dist/CASSound/` contains the GUI `CASSound.exe`, console `CASSoundCLI.exe`, workers and shared dependencies. Run `CASSoundCLI.exe doctor` and `CASSound.exe --gui-startup-check`. Put external FFmpeg on PATH. Install model packs through Models or `CASSoundCLI.exe model-install PACK.casp`.

Install Inno Setup 6, then run `python tools/build_release.py --target installer --require-windows`. The per-user installer appears in `dist/`. Do not rename a Linux binary to `.exe`; PyInstaller builds for its host OS.

The optional `tools/sign_windows.ps1` signs an explicitly chosen file with an already installed certificate thumbprint, then verifies the signature. No signing certificate or signing claim is included.

## Offline model packs

```powershell
python tools/fetch_models.py model-packs
python tools/model_smoke.py model-packs
```

This is an explicit network step. Fetches are revision/digest pinned and include notices. `.casp` installation validates exact members, expanded size, catalog identity and weight SHA-256. Runtime inference uses ONNX bytes on CPU and never loads upstream Python or pickle files. Weights remain separate from source distributions.

## Verification and CI

`pytest` exercises numerical/source/security/workflow checks. Optional native tests need the host and fixture environment variables. `tools/model_smoke.py` executes real weights, separately from the ordinary suite. `tools/gui_release_smoke.py` checks workspace startup/themes and writes a screenshot; `tools/verify_frozen.py` covers existing frozen-core paths. `.github/workflows/windows-build.yml` builds native Windows artifacts and runs them on a separate runner. These workflow definitions do not imply they already ran successfully.

The evidence and outstanding qualification are in `docs/RELEASE_VALIDATION.md`. Benchmark datasets, signing credentials, physical device tests and clean Windows execution cannot be replaced by synthetic unit checks.


## Native build diagnostics

The Windows native build uses the same static MSVC CRT (`/MT`, `/MTd` for Debug) in CASSound and the VST3 SDK. The host disables Windows `min`/`max` macros with `NOMINMAX` so standard-library calls compile normally.

CMake configure/build output is streamed to the console and saved in `build/native/logs/configure.log` and `build/native/logs/build.log`. A failure includes its first compiler/linker diagnostic in the traceback. Both workflows preserve these files and the CMake cache/configuration log as diagnostic artifacts even when later steps fail. Core checks keeps Linux and Windows matrix jobs independent, so one failure does not cancel the other.
