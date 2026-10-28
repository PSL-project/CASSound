---
license: apache-2.0
pipeline_tag: audio-classification
tags:
  - onnx
  - wav2vec2
  - deepfake-detection
  - audio-classification
  - speech
  - anti-spoofing
base_model: Gustking/wav2vec2-large-xlsr-deepfake-audio-classification
library_name: onnxruntime
---

# Wav2Vec2 Large XLSR - Deepfake Audio Classification (ONNX)

This is the **ONNX format** of [Gustking/wav2vec2-large-xlsr-deepfake-audio-classification](https://huggingface.co/Gustking/wav2vec2-large-xlsr-deepfake-audio-classification), a Wav2Vec2 model fine-tuned for binary classification of audio as **real (bonafide)** or **fake (spoof/deepfake)**.

The model was converted from PyTorch (safetensors) to ONNX for use with ONNX Runtime, enabling deployment without a PyTorch dependency. Three variants are provided to suit different size/accuracy tradeoffs.

## Available Variants

| Variant | File | Size | Reduction | Mean Relative Error |
|---|---|---|---|---|
| **FP32** (full precision) | `model_fp32.onnx` | 1204.8 MB | — | 0.00% |
| **FP16** (half precision) | `model_fp16.onnx` | 602.7 MB | 50.0% | 0.05% |
| **INT8** (quantized MatMul) | `model_int8.onnx` | 338.9 MB | 71.9% | 3.66% |

All three variants produce identical classification decisions across all test samples. Choose based on your deployment constraints:

- **FP32** — baseline, bit-for-bit closest to the original PyTorch model.
- **FP16** — best balance of size and accuracy. Weights stored as float16, cast to float32 at runtime. Virtually no accuracy loss (0.05% relative error).
- **INT8** — smallest model. Only MatMul (attention/FFN) weights are dynamically quantized to INT8; the feature extractor Conv layers are kept in full precision to preserve accuracy.

## Model Details

| Property | Value |
|---|---|
| Architecture | `Wav2Vec2ForSequenceClassification` |
| Base Model | `alefiury/wav2vec2-large-xlsr-53-gender-recognition-librispeech` |
| Parameters | ~315M |
| ONNX Opset | 17 |
| Sampling Rate | 16,000 Hz |
| Labels | `0: real`, `1: fake` |

## Original Model Performance

Results on [ASVspoof2019](https://www.kaggle.com/datasets/awsaf49/asvpoof-2019-dataset) evaluation subset:

| Metric | Score |
|---|---|
| Accuracy | 0.9286 |
| Precision | 0.9999 |
| Recall | 0.9205 |
| F1-Score | 0.9363 |
| Equal Error Rate (EER) | 0.0401 |

## Accuracy Validation (vs PyTorch, 20 samples)

All variants were validated against the original PyTorch model across 20 random audio samples of varying durations (1s–8s).

| Metric | FP32 | FP16 | INT8 |
|---|---|---|---|
| Max Absolute Error | 1.35e-05 | 1.02e-03 | 5.58e-02 |
| Mean Absolute Error | 5.27e-06 | 2.03e-04 | 2.03e-02 |
| Mean Relative Error | 0.00% | 0.05% | 3.66% |
| MSE | 3.57e-11 | 9.83e-08 | 5.41e-04 |
| RMSE | 5.98e-06 | 3.14e-04 | 2.33e-02 |
| Cosine Similarity (avg) | 1.00000000 | 1.00000000 | 0.99999982 |
| Prediction Match Rate | 20/20 | 20/20 | 20/20 |

### Probability Comparison (sample)

| Label | PyTorch | FP32 | FP16 | INT8 |
|---|---|---|---|---|
| real | 0.903019 | 0.903019 | 0.903020 | ~0.89 |
| fake | 0.096981 | 0.096981 | 0.096980 | ~0.11 |

## Usage

### With ONNX Runtime

```python
import numpy as np
import librosa
import onnxruntime as ort

# Load any variant — just swap the filename
# "model.onnx" (FP32) | "model_fp16.onnx" (FP16) | "model_quantized_matmul_only.onnx" (INT8)
session = ort.InferenceSession("model_fp16.onnx", providers=["CPUExecutionProvider"])

# Load and preprocess audio
audio, sr = librosa.load("audio.wav", sr=16000)

# Normalize (matching Wav2Vec2FeatureExtractor behavior)
audio = (audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)

# Run inference
input_values = audio.reshape(1, -1).astype(np.float32)
attention_mask = np.ones_like(input_values, dtype=np.int64)

logits = session.run(
    None,
    {
        "input_values": input_values,
        "attention_mask": attention_mask,
    },
)[0]

# Get probabilities
probs = np.exp(logits) / np.exp(logits).sum(axis=-1, keepdims=True)
real_prob, fake_prob = probs[0]

print(f"Real: {real_prob:.4f}, Fake: {fake_prob:.4f}")
print(f"Verdict: {'Fake/Spoof' if fake_prob > real_prob else 'Real/Bonafide'}")
```

### With Transformers + PyTorch (original model)

```python
import torch
import librosa
from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForSequenceClassification

feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
    "Gustking/wav2vec2-large-xlsr-deepfake-audio-classification"
)
model = Wav2Vec2ForSequenceClassification.from_pretrained(
    "Gustking/wav2vec2-large-xlsr-deepfake-audio-classification"
)
model.eval()

audio, sr = librosa.load("audio.wav", sr=16000)
inputs = feature_extractor(audio, sampling_rate=16000, return_tensors="pt", padding=True)

with torch.no_grad():
    logits = model(**inputs).logits
    probs = logits.softmax(dim=-1)[0]

print(f"Real: {probs[0]:.4f}, Fake: {probs[1]:.4f}")
```

## Inputs & Outputs

### Inputs

| Name | Shape | Type | Description |
|---|---|---|---|
| `input_values` | `(batch_size, sequence_length)` | float32 | Normalized audio waveform at 16kHz |
| `attention_mask` | `(batch_size, sequence_length)` | int64 | Mask indicating valid positions (1) vs padding (0) |

### Outputs

| Name | Shape | Type | Description |
|---|---|---|---|
| `logits` | `(batch_size, 2)` | float32 | Raw logits — index 0 = real, index 1 = fake. Apply softmax for probabilities. |

## Conversion Details

- **FP32**: Exported from PyTorch using the TorchScript ONNX exporter (opset 17) with dynamic axes for variable-length audio input.
- **FP16**: Weight initializers converted from float32 to float16 with Cast nodes inserted to upcast back to float32 at runtime. This keeps the compute graph in FP32 (full compatibility) while halving storage.
- **INT8**: Dynamic INT8 quantization applied selectively to MatMul operations only (attention and feed-forward layers). The feature extractor's Conv layers are kept in full precision, as quantizing them introduced significant accuracy degradation (~37% mean relative error vs ~3.66% with MatMul-only).

## License

Apache 2.0 (following the original model's license).
