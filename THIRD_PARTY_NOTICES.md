# Third-party components

This checkpoint distributes original source and Python package artifacts. Its declared runtime dependencies include NumPy (BSD-3-Clause), SoundFile (BSD-3-Clause) and its libsndfile backend (LGPL), and the optional PySide6/Qt desktop (LGPL/GPL/commercial terms, depending on component) with PyQtGraph (MIT). Preserve actual dependency notices and comply with the specific distributed versions.

FFmpeg/ffprobe are external runtime tools in this checkpoint, not redistributed binaries. Their actual build configuration determines applicable license obligations. Packaging scripts deliberately do not silently download or bundle a guessed FFmpeg build.

The desktop uses platform font fallbacks and original code-drawn components; no League of Legends, Arcane, Cyberpunk or Edgerunners artwork or branding is bundled. The visual references inform the original CASSound design direction only.

Before public binary distribution, P6 must produce a complete inventory and all required dependency/license/source materials for the actual bundle. This summary does not replace those materials.

P2 adds SciPy (BSD-3-Clause), librosa (ISC), scikit-learn (BSD-3-Clause), ssqueezepy (MIT) and pyloudnorm (MIT), plus their dependency chains. These are declared external Python dependencies, not vendored into the source archive. Frozen distribution must preserve notices for the exact installed components. P2 speech measurements are implemented with NumPy/SciPy; Praat/Parselmouth is not a runtime dependency.


## P3 audio/native dependencies

sounddevice (MIT) provides optional PortAudio stream bindings. PortAudio is MIT; Windows wheels may bundle audio runtime DLLs under the supplier's notices. No ASIO backend or SDK is asserted present without device/API inventory. Scientific edits continue to use NumPy/SciPy and optional librosa under their existing notices.

The separately built native host uses Steinberg VST3 SDK 3.8.1 (MIT, copyright 2025 Steinberg Media Technologies GmbH) and nlohmann/json 3.12.0 (MIT, Niels Lohmann). Full texts are in native/vst3_host/licenses and must accompany distributed native builds. VST is a trademark of Steinberg Media Technologies GmbH; no endorsement or compatibility logo is claimed. The test-only mda bundle is built from the official SDK for verification and is not shipped as a CASSound production effect pack. Preserve mda source notices if distributing that bundle separately.

## Message cryptography, reports and CPU models

Cryptography is distributed under Apache-2.0 OR BSD-3-Clause; Matplotlib uses its PSF-derived Matplotlib license; Apache Arrow/pyarrow uses Apache-2.0; ONNX Runtime uses MIT. The actual installed distributions retain their own bundled notices and native-library terms.

Offline model weights are not part of the source/wheel archive. AudioSeal ONNX exports derive from Meta's MIT-licensed AudioSeal weights. GTCRN DNS3 streaming weights are mirrored from Xiaobin-Rong/gtcrn under MIT. The INT8 wav2vec2 export follows the Apache-2.0 Gustking model. Exact repositories, revisions, weight hashes and model cards/licenses are included in resources/model-catalog.json and resources/model-notices/. Each offline .casp includes its own notices. Models can have domain limitations that licensing does not resolve.

The original waveform SVG and PNG/ICO renders are project artwork under the project license. Faceted dark shapes, cyan waveform and amber details use a general painterly science-fiction/neon aesthetic; no franchise characters, logos or game assets are included.
