---
license: mit
tags:
  - speech-enhancement
  - denoise
  - onnx
  - streaming
---

# gtcrn-stream

Verbatim mirror of the upstream **GTCRN** streaming ONNX model from
[Xiaobin-Rong/gtcrn](https://github.com/Xiaobin-Rong/gtcrn)
(`stream/onnx_models/gtcrn_simple.onnx`, trained on DNS3).

Re-hosted here so it resolves through the same HF Hub cache / `HF_HUB_OFFLINE=1`
path as every other model in the uri/rei stack (`mic_capture` [denoise.model]).

- `gtcrn_simple.onnx` — simplified streaming graph, 16 kHz native, ~24 K params.
  Inputs: `mix` [1,257,1,2], `conv_cache` [2,1,16,16,33], `tra_cache`
  [2,3,1,1,16], `inter_cache` [2,1,33,16]. Outputs: `enh` + `*_out` caches.
  STFT: n_fft 512, hop 256, sqrt-Hann window.

License: MIT (upstream).
