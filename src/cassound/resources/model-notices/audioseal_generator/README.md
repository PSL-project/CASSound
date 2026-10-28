---
license: mit
library_name: onnxruntime
tags:
  - audioseal
  - watermarking
  - audio
  - onnx
  - onnxruntime
---

# AudioSeal 16-bit — ONNX export

ONNX exports of Meta's [AudioSeal](https://github.com/facebookresearch/audioseal)
**16-bit** generator and detector, for inference with **onnxruntime**
(Node / web / Python) without a PyTorch dependency.

| File | Role |
|---|---|
| `audioseal_generator_16bits.onnx` | Embeds a 16-bit watermark into mono audio. |
| `audioseal_detector_16bits.onnx` | Detects the watermark and recovers the 16-bit payload. |

## Usage notes (important)

- Inputs are **float32 mono PCM in `[-1, 1]`**. Pad the audio length up to a
  multiple of **HOP = 320** before running each model, then trim the output back
  to the original length.
- **Generator**: inputs `audio [1, 1, T]` (float32) + `message [1, 16]` (int32
  bits); output is the watermarked audio.
- **Detector**: input `audio [1, 1, T]` (float32); outputs per-sample detection
  probabilities + the recovered 16-bit message.
- **Sample-rate-agnostic and length-dynamic** — one generator + one detector file
  covers any sample rate and clip length.

## Provenance

Exported from the `audioseal` PyPI package's PyTorch checkpoints via
`torch.onnx` (opset 17), with `NO_TORCH_COMPILE=1` and a host-side
pad-to-320 / trim convention. Bit-exact 16-bit payload parity and matching
detection probabilities versus the PyTorch reference were verified.

## License

**MIT** — the same license as upstream AudioSeal (code **and** model weights,
which Meta relicensed to MIT in April 2024). These ONNX files are a derivative of
those MIT-licensed weights, redistributed under MIT. © Meta Platforms, Inc. and
affiliates. See `LICENSE`.
