"""CASM authenticated payloads and bounded carrier profiles; secrets stay in memory."""

import os
import struct
import zlib
import numpy as np
from scipy import signal
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.exceptions import InvalidTag
from ..domain.errors import ValidationError, IntegrityError, ResourceLimit

MAX_PAYLOAD = 1024 * 1024
HEADER = struct.Struct(">4sBBII16s12s")
PROFILES = ("fsk", "bpsk", "qpsk", "qam16", "chirp", "spread", "echo", "phase", "transform")


def _key(password, salt):
    if isinstance(password, bytes):
        if len(password) != 32:
            raise ValidationError("Key must contain 32 raw bytes.")
        return password
    if not isinstance(password, str) or not password or len(password.encode()) > 4096:
        raise ValidationError("A nonempty password of at most 4096 UTF-8 bytes is required.")
    return Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode())


def seal(payload, password):
    if not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD:
        raise ResourceLimit("Payload must be bytes, at most 1 MiB.")
    compressed = zlib.compress(payload)
    flag = int(len(compressed) < len(payload))
    body = compressed if flag else payload
    salt, nonce = os.urandom(16), os.urandom(12)
    header = HEADER.pack(
        b"CASM",
        1,
        flag | (2 if isinstance(password, bytes) else 0),
        len(payload),
        len(body) + 16,
        salt,
        nonce,
    )
    return header + AESGCM(_key(password, salt)).encrypt(nonce, body, header)


def unseal(envelope, password):
    if (
        not isinstance(envelope, bytes)
        or not HEADER.size + 16 <= len(envelope) <= MAX_PAYLOAD + HEADER.size + 16
    ):
        raise IntegrityError("Damaged message envelope.", code="message_damaged")
    magic, version, flags, size, body_size, salt, nonce = HEADER.unpack_from(envelope)
    if (
        magic != b"CASM"
        or version != 1
        or flags not in range(4)
        or size > MAX_PAYLOAD
        or body_size != len(envelope) - HEADER.size
    ):
        raise IntegrityError("Unsupported/damaged message header.", code="message_damaged")
    if bool(flags & 2) != isinstance(password, bytes):
        raise IntegrityError("Wrong credential type.", code="authentication_failed")
    try:
        body = AESGCM(_key(password, salt)).decrypt(nonce, envelope[HEADER.size :], envelope[: HEADER.size])
    except InvalidTag as exc:
        raise IntegrityError("Wrong credential or altered message.", code="authentication_failed") from exc
    if flags & 1:
        decoder = zlib.decompressobj()
        try:
            body = decoder.decompress(body, size + 1)
        except zlib.error as exc:
            raise IntegrityError("Invalid compressed body.") from exc
        if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise IntegrityError("Compressed body violates bounds.")
    if len(body) != size:
        raise IntegrityError("Authenticated length mismatch.")
    return body


def frame(envelope):
    if not isinstance(envelope, bytes) or len(envelope) > MAX_PAYLOAD + HEADER.size + 16:
        raise ResourceLimit("Envelope too large.")
    return np.repeat(
        np.unpackbits(
            np.frombuffer(
                struct.pack(">4sII", b"CASF", len(envelope), zlib.crc32(envelope)) + envelope, dtype=np.uint8
            )
        ),
        3,
    )


def unframe(bits):
    bits = np.asarray(bits, dtype=np.uint8).reshape(-1)
    if len(bits) < 288:
        raise IntegrityError("No complete frame.", code="message_absent")
    decoded = (bits[: len(bits) // 3 * 3].reshape(-1, 3).sum(axis=1) >= 2).astype(np.uint8)
    raw = np.packbits(decoded[: len(decoded) // 8 * 8]).tobytes()
    magic, size, crc = struct.unpack_from(">4sII", raw)
    if magic != b"CASF":
        raise IntegrityError("No supported frame.", code="message_absent")
    if size > MAX_PAYLOAD + HEADER.size + 16 or len(raw) < 12 + size:
        raise IntegrityError("Truncated/oversize frame.", code="message_damaged")
    envelope = raw[12 : 12 + size]
    if zlib.crc32(envelope) != crc:
        raise IntegrityError("Frame checksum failed.", code="message_damaged")
    return envelope


def _native(x):
    x = np.asarray(x)
    if x.dtype.kind not in "iu" or x.dtype.itemsize > 4:
        raise ValidationError("Native 8–32-bit integer samples required.")
    if x.size > 8_000_000:
        raise ResourceLimit("Native carrier exceeds 8M samples.")
    return x


def embed_lsb(samples, envelope):
    x = _native(samples)
    bits = frame(envelope)
    if len(bits) > x.size:
        raise ResourceLimit(f"Need {len(bits)} samples, have {x.size}.")
    y = x.copy(order="C")
    flat = y.reshape(-1)
    mask = np.array(-2 if x.dtype.kind == "i" else np.iinfo(x.dtype).max - 1, dtype=x.dtype)
    flat[: len(bits)] = (flat[: len(bits)] & mask) | bits.astype(x.dtype)
    return y


def extract_lsb(samples):
    x = _native(samples).reshape(-1)
    if len(x) < 288:
        raise IntegrityError("No frame.", code="message_absent")
    raw = np.packbits(((x[:288] & 1).reshape(-1, 3).sum(axis=1) >= 2).astype(np.uint8)).tobytes()
    magic, size, _ = struct.unpack(">4sII", raw)
    if magic != b"CASF":
        raise IntegrityError("No supported frame.", code="message_absent")
    if size > MAX_PAYLOAD + HEADER.size + 16:
        raise IntegrityError("Invalid frame length.")
    return unframe((x[: (12 + size) * 24] & 1).astype(np.uint8))


def inspect_bits(samples, offset=0, count=256):
    x = _native(samples)
    if type(offset) is not int or type(count) is not int or not 0 <= offset <= x.size or not 1 <= count <= 4096:
        raise ValidationError("Invalid microscope page.")
    values = x.reshape(-1)[offset : offset + count]
    return {
        "offset": offset,
        "dtype": str(x.dtype),
        "values": values.tolist(),
        "bitplanes": [((values >> bit) & 1).tolist() for bit in range(x.dtype.itemsize * 8)],
    }


def _basis(profile, n):
    t = np.arange(n) / n
    a = np.cos(2 * np.pi * 8 * t)
    b = np.sin(2 * np.pi * 8 * t)
    if profile == "fsk":
        basis = np.array([a, np.cos(2 * np.pi * 16 * t)])
    elif profile == "chirp":
        basis = np.array(
            [np.cos(2 * np.pi * (4 * t + 12 * t * t)), np.cos(2 * np.pi * (28 * t - 12 * t * t))]
        )
    elif profile == "qpsk":
        basis = np.array([np.cos(k * np.pi / 2) * a + np.sin(k * np.pi / 2) * b for k in range(4)])
    elif profile == "qam16":
        basis = np.array([(i * a + q * b) / 5 for i in (-3, -1, 1, 3) for q in (-3, -1, 1, 3)])
    elif profile == "echo":
        a = np.random.default_rng(20261028).standard_normal(n)
        basis = np.array([a + 0.6 * np.roll(a, k) for k in (8, 16)])
    else:
        if profile == "spread":
            a = np.random.default_rng(20261028).choice([-1.0, 1.0], n)
        if profile == "transform":
            a = np.cos(np.pi * 8 * (np.arange(n) + 0.5) / n)
        basis = np.array([a, -a])
    return basis / np.max(abs(basis))


def pilot(n):
    return np.random.default_rng(718235).choice([-1.0, 1.0], 8 * n)


def _profile(profile, n):
    if profile not in PROFILES or type(n) is not int or not 64 <= n <= 4096:
        raise ValidationError("Unknown profile or symbol size outside 64..4096.")


def synthesize(envelope, profile="fsk", symbol_frames=128, amplitude=0.25):
    _profile(profile, symbol_frames)
    if not np.isfinite(amplitude) or not 0 < amplitude <= 0.5:
        raise ValidationError("Amplitude must be (0,.5].")
    basis = _basis(profile, symbol_frames)
    width = int(np.log2(len(basis)))
    bits = frame(envelope)
    bits = np.pad(bits, (0, -len(bits) % width))
    symbols = bits.reshape(-1, width) @ (1 << np.arange(width - 1, -1, -1))
    if (len(symbols) + 8) * symbol_frames > 8_000_000:
        raise ResourceLimit("Carrier exceeds 8M sample budget.")
    return amplitude * np.r_[pilot(symbol_frames), basis[symbols].reshape(-1)]


def synchronize(samples, n):
    x = np.asarray(samples, float)
    if x.ndim != 1 or not 8 * n <= len(x) <= 8_000_000 or not np.all(np.isfinite(x)):
        raise IntegrityError("Absent/invalid synchronization signal.", code="message_absent")
    train = pilot(n)
    corr = signal.correlate(x, train, mode="valid", method="fft")
    energy = signal.convolve(x * x, np.ones(len(train)), mode="valid", method="fft")
    scores = abs(corr) / np.sqrt(np.maximum(energy, 1e-20) * len(train))
    index = int(np.argmax(scores))
    gain = float(corr[index] / len(train))
    if scores[index] < 0.65 or abs(gain) < 1e-12:
        raise IntegrityError("No supported synchronization pilot.", code="message_absent")
    return x[index + len(train) :] / gain, index + len(train), float(scores[index])


def raw_symbols(samples, profile="fsk", symbol_frames=128):
    _profile(profile, symbol_frames)
    x, start, score = synchronize(samples, symbol_frames)
    basis = _basis(profile, symbol_frames)
    blocks = x[: len(x) // symbol_frames * symbol_frames].reshape(-1, symbol_frames)
    distance = (
        np.sum(blocks * blocks, axis=1)[:, None]
        + np.sum(basis * basis, axis=1)[None, :]
        - 2 * blocks @ basis.T
    )
    symbols = np.argmin(distance, axis=1)
    width = int(np.log2(len(basis)))
    bits = ((symbols[:, None] >> np.arange(width - 1, -1, -1)) & 1).astype(np.uint8).reshape(-1)
    return bits, {
        "start_frame": start,
        "symbol_frames": symbol_frames,
        "bits_per_symbol": width,
        "pilot_correlation": score,
    }


def demodulate(samples, profile="fsk", symbol_frames=128, trace=False):
    bits, info = raw_symbols(samples, profile, symbol_frames)
    envelope = unframe(bits)
    needed = (12 + len(envelope)) * 24
    groups = bits[:needed].reshape(-1, 3)
    majority = (groups.sum(axis=1) >= 2).astype(np.uint8)
    corrections = np.flatnonzero(np.any(groups != majority[:, None], axis=1))
    info.update(
        {
            "coded_bits": needed,
            "corrected_bit_groups": len(corrections),
            "corrected_group_positions": corrections[:4096].tolist(),
            "ciphertext_bit_preview": bits[:256].tolist(),
        }
    )
    return (envelope, info) if trace else envelope


def embed_host(samples, envelope, profile="spread", symbol_frames=512, strength=0.3):
    from scipy.fft import dct, idct

    _profile(profile, symbol_frames)
    x = np.asarray(samples, float)
    if x.ndim != 1 or len(x) > 8_000_000 or not np.all(np.isfinite(x)) or np.max(abs(x), initial=0) > 0.95:
        raise ValidationError("Finite mono host with peak <=.95 FS required.")
    if profile not in {"spread", "echo", "phase", "transform"} or not 0 < strength <= 0.8:
        raise ValidationError("Unsupported host profile or strength.")
    bits = frame(envelope)
    needed = len(bits) * symbol_frames
    if len(x) < needed or len(x) + 8 * symbol_frames > 8_000_000:
        raise ResourceLimit("Host capacity insufficient/output exceeds budget.")
    y = x.copy()
    blocks = y[:needed].reshape(-1, symbol_frames)
    signs = 1 - 2 * bits.astype(float)
    if profile == "spread":
        blocks += strength * signs[:, None] * _basis("spread", symbol_frames)[0]
    elif profile == "phase":
        values = np.fft.rfft(blocks, axis=1)
        values[:, 8] = signs * np.maximum(abs(values[:, 8]), strength * symbol_frames / 2)
        blocks[:] = np.fft.irfft(values, n=symbol_frames, axis=1)
    elif profile == "transform":
        values = dct(blocks, norm="ortho", axis=1)
        values[:, 8] = signs * np.maximum(abs(values[:, 8]), strength * np.sqrt(symbol_frames))
        blocks[:] = idct(values, norm="ortho", axis=1)
    else:
        power = abs(np.fft.rfft(blocks, axis=1)) ** 2 + 1e-20
        if np.median(np.exp(np.mean(np.log(power), axis=1)) / np.mean(power, axis=1)) < 0.05:
            raise ValidationError("Echo requires a broadband host.")
        original = blocks.copy()
        for i, bit in enumerate(bits):
            blocks[i] += strength * np.roll(original[i], 8 if bit == 0 else 16)
    gain = min(1, 0.95 / max(np.max(abs(y)), 1e-20))
    y *= gain
    return np.r_[0.25 * pilot(symbol_frames), y], {
        "modified_frames": needed,
        "host_gain": gain,
        "prefix_frames": 8 * symbol_frames,
        "profile": profile,
    }


def recover_host(samples, profile="spread", symbol_frames=512, trace=False):
    from scipy.fft import dct

    _profile(profile, symbol_frames)
    x, start, score = synchronize(samples, symbol_frames)
    blocks = x[: len(x) // symbol_frames * symbol_frames].reshape(-1, symbol_frames)
    if profile == "spread":
        bits = blocks @ _basis("spread", symbol_frames)[0] < 0
    elif profile == "phase":
        bits = np.fft.rfft(blocks, axis=1)[:, 8].real < 0
    elif profile == "transform":
        bits = dct(blocks, norm="ortho", axis=1)[:, 8] < 0
    elif profile == "echo":
        cep = np.fft.irfft(
            np.log(np.maximum(abs(np.fft.rfft(blocks, axis=1)), 1e-12)), n=symbol_frames, axis=1
        )
        bits = cep[:, 16] > cep[:, 8]
    else:
        raise ValidationError("Unknown host profile.")
    envelope = unframe(bits.astype(np.uint8))
    info = {
        "start_frame": start,
        "symbol_frames": symbol_frames,
        "bits_per_symbol": 1,
        "pilot_correlation": score,
        "ciphertext_bit_preview": bits[:256].astype(int).tolist(),
    }
    return (envelope, info) if trace else envelope
