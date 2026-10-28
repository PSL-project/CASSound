"""Audited CPU adapters. Mono 16 kHz model preprocessing is explicit."""

import math
import numpy as np
from scipy import signal
from ..runtime.models import load, descriptor
from ..domain.errors import ValidationError


def run(x, fs, model_id, root, bits=None):
    x = np.asarray(x, np.float32)
    if x.ndim == 2:
        x = x.mean(axis=1)
    if x.ndim != 1 or len(x) > 8_000_000 or not np.all(np.isfinite(x)) or not 8000 <= fs <= 192000:
        raise ValidationError("Bounded finite audio at 8–192 kHz required.")
    g = math.gcd(fs, 16000)
    x = signal.resample_poly(x, 16000 // g, fs // g).astype(np.float32)
    if not 16000 <= len(x) <= 16000 * 600:
        raise ValidationError("Model input must be 1–600 seconds.")
    if model_id.startswith("audioseal") and len(x) > 16000 * 30:
        raise ValidationError("AudioSeal processing is bounded to 30 seconds per run.")
    model = load(model_id, root)
    meta = {
        "model": descriptor(model_id),
        "preprocessing": "Channel mean; polyphase resampling to mono 16000 Hz; CPU",
        "sample_rate": 16000,
        "input_frames": len(x),
    }
    if model_id == "gtcrn":
        window = np.sqrt(signal.windows.hann(512, sym=False)).astype(np.float32)
        padded = np.pad(x, (256, 512))
        y = np.zeros_like(padded)
        norm = np.zeros_like(padded)
        cache = {
            "conv_cache": np.zeros((2, 1, 16, 16, 33), np.float32),
            "tra_cache": np.zeros((2, 3, 1, 1, 16), np.float32),
            "inter_cache": np.zeros((2, 1, 33, 16), np.float32),
        }
        for offset in range(0, len(padded) - 511, 256):
            z = np.fft.rfft(padded[offset : offset + 512] * window)
            mix = np.stack([z.real, z.imag], axis=-1).astype(np.float32)[None, :, None, :]
            result = model.run(None, {"mix": mix, **cache})
            enh = result[0].reshape(257, 2)
            current = np.fft.irfft(enh[:, 0] + 1j * enh[:, 1], n=512).astype(np.float32)
            y[offset : offset + 512] += current * window
            norm[offset : offset + 512] += window**2
            cache = dict(zip(cache, result[1:]))
        return y[256 : 256 + len(x)] / np.maximum(norm[256 : 256 + len(x)], 1e-8), meta
    if model_id == "deepfake":
        scores = []
        for start in range(0, len(x), 128000):
            current = x[start : start + 128000]
            current = np.pad(current, (0, max(0, 16000 - len(current))))
            current = (current - current.mean()) / np.sqrt(current.var() + 1e-7)
            feed = {}
            for item in model.get_inputs():
                feed[item.name] = (
                    np.ones((1, len(current)), np.int32 if item.type == "tensor(int32)" else np.int64)
                    if "mask" in item.name
                    else current[None].astype(np.float32)
                )
            logits = model.run(None, feed)[0].reshape(-1)
            p = np.exp(logits - logits.max())
            p /= p.sum()
            scores.append(float(p[1]))
        return None, {
            **meta,
            "chunk_fake_scores": scores,
            "mean_fake_score": float(np.mean(scores)),
            "interpretation": "Uncalibrated model scores, not authenticity probabilities. Unseen-source field accuracy is not established.",
        }
    padded = np.pad(x, (0, -len(x) % 320))[None, None, :]
    if model_id == "audioseal_generator":
        if not isinstance(bits, str) or len(bits) != 16 or set(bits) - {"0", "1"}:
            raise ValidationError("Exactly 16 watermark bits required.")
        y = model.run(None, {"audio": padded, "message": np.array([[int(b) for b in bits]], np.int32)})[0]
        return y.reshape(-1)[: len(x)], {
            **meta,
            "message_bits": bits,
            "operation": "AudioSeal watermarked output, 16-bit known-family profile.",
        }
    result = model.run(None, {"audio": padded})
    probability = np.asarray(result[0])
    message = np.asarray(result[1])
    score = (
        float(np.mean(probability[:, 1]))
        if probability.ndim >= 2 and probability.shape[1] == 2
        else float(np.mean(probability))
    )
    decoded = (
        (message.reshape(-1) > 0).astype(int)
        if np.min(message) < 0 or np.max(message) > 1
        else (message.reshape(-1) > 0.5).astype(int)
    )
    return None, {
        **meta,
        "watermark_score": score,
        "candidate": score >= 0.5,
        "message_bits": "".join(map(str, decoded[:16])),
        "interpretation": "Known AudioSeal family; payload is meaningful only with detection. Not a universal detector or authenticity verdict.",
    }
