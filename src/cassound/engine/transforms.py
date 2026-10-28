import numpy as np
from scipy import signal, fft
from .common import dense, integer, number, optional, output
from ..domain.errors import ValidationError, ResourceLimit


def wavelet(read, n, c, fs, p):
    if c != 1:
        raise ValidationError("Select one channel for this CWT profile.")
    x = dense(read, n, c, maximum=262144)[:, 0]
    low = number(p, "fmin", max(10, fs * 8 / n), max(fs / n, 1e-6), fs / 3)
    high = number(p, "fmax", fs / 4, low, fs / 3)
    if low >= high:
        raise ValidationError("Wavelet frequency bounds must be ordered.")
    bands = integer(p, "bands", 48, 4, 128)
    stride = integer(p, "time_stride", max(1, n // 2048), 1, n)
    frequencies = np.geomspace(low, high, bands)
    coefs = np.empty((len(x[::stride]), bands), complex)
    valid = np.empty(coefs.shape, bool)
    positions = np.arange(0, n, stride)
    for i, f in enumerate(frequencies):
        scale = 6 * fs / (2 * np.pi * f)
        radius = int(np.ceil(5 * scale))
        if 2 * radius + 1 > 8 * n:
            raise ResourceLimit("Wavelet support exceeds the input budget. Raise fmin.")
        t = np.arange(-radius, radius + 1) / scale
        kernel = np.pi ** (-0.25) * (np.exp(6j * t) - np.exp(-18)) * np.exp(-t * t / 2) / np.sqrt(scale)
        coefs[:, i] = signal.fftconvolve(x, kernel[::-1].conj(), mode="same")[::stride]
        valid[:, i] = (positions >= radius) & (positions < n - radius)
    return output(
        {
            "time": positions / fs,
            "frequency": frequencies,
            "coefficient": coefs,
            "power": abs(coefs) ** 2,
            "edge_valid": valid,
        },
        {
            "wavelet": "complex Morlet omega0=6, discrete L2 normalization",
            "time_stride": stride,
            "boundary": "zero extension; edge_valid excludes five-scale support",
            "not_psd": True,
        },
        {"frequency": "Hz", "coefficient": "FS sqrt(samples)", "power": "coefficient²"},
        {"kind": "image", "x": "time", "y": "frequency", "z": "power"},
    )


def librosa_transform(task, read, n, c, fs, p):
    librosa = optional("librosa")
    if c != 1:
        raise ValidationError("Select one channel for this transform profile.")
    x = dense(read, n, c, maximum=1000000)[:, 0]
    hop = integer(p, "hop_length", 512, 1, min(n, 65536))
    length = integer(p, "frame_length", min(2048, n), 32, min(n, 65536))
    if task == "transforms.cqt":
        low = number(p, "fmin", 65.4064, 1, fs / 2)
        bins = integer(p, "bins", 60, 4, 120)
        octave = integer(p, "bins_per_octave", 12, 4, 48)
        if low * 2 ** ((bins - 1) / octave) >= fs / 2:
            raise ValidationError("CQT frequencies exceed Nyquist.")
        z = librosa.cqt(x, sr=fs, hop_length=hop, fmin=low, n_bins=bins, bins_per_octave=octave)
        frequency = librosa.cqt_frequencies(bins, fmin=low, bins_per_octave=octave)
        return output(
            {"time": np.arange(z.shape[1]) * hop / fs, "frequency": frequency, "magnitude": abs(z).T},
            {
                "method": "librosa multirate CQT",
                "padding": "centered constant extension; edge coefficients affected",
            },
            {"magnitude": "CQT coefficient magnitude", "frequency": "Hz"},
            {"kind": "image", "x": "time", "y": "frequency", "z": "magnitude"},
        )
    if length * (1 + n // hop) > 32000000:
        raise ResourceLimit("Transform output budget exceeded. Increase hop length.")
    if task == "transforms.reassigned":
        frequencies, times, magnitude = librosa.reassigned_spectrogram(
            x, sr=fs, n_fft=length, hop_length=hop, center=False, fill_nan=False, ref_power=1e-10
        )
        fc = fft.rfftfreq(length, 1 / fs)
        tc = (np.arange(magnitude.shape[1]) * hop + length / 2) / fs
        fb = np.r_[0, (fc[1:] + fc[:-1]) / 2, fs / 2]
        tb = np.r_[tc[0] - hop / (2 * fs), (tc[1:] + tc[:-1]) / 2, tc[-1] + hop / (2 * fs)]
        valid = np.isfinite(times) & np.isfinite(frequencies)
        image = np.histogram2d(
            times[valid], frequencies[valid], bins=(tb, fb), weights=magnitude[valid] ** 2
        )[0]
        return output(
            {
                "time": tc,
                "frequency": fc,
                "energy": image,
                "reassigned_time": times.T,
                "reassigned_frequency": frequencies.T,
            },
            {
                "method": "STFT time/frequency reassignment then weighted histogram",
                "padding": False,
                "not_psd": True,
            },
            {"energy": "accumulated raw STFT magnitude²", "reassigned_frequency": "Hz"},
            {"kind": "image", "x": "time", "y": "frequency", "z": "energy"},
        )
    z = abs(librosa.stft(x, n_fft=length, hop_length=hop, center=False, window=p.get("window", "hann")))
    mel = librosa.feature.melspectrogram(S=z * z, sr=fs, n_fft=length, n_mels=integer(p, "bands", 64, 4, 256))
    frequency = librosa.mel_frequencies(n_mels=mel.shape[0], fmin=0, fmax=fs / 2)
    return output(
        {"time": (np.arange(z.shape[1]) * hop + length / 2) / fs, "frequency": frequency, "mel_power": mel.T},
        {"method": "Slaney mel filters on unnormalized STFT power", "padding": False, "not_psd": True},
        {"mel_power": "mel-weighted raw STFT power"},
        {"kind": "image", "x": "time", "y": "frequency", "z": "mel_power"},
    )


def cepstrum(read, n, c, fs, p):
    x = dense(read, n, c, maximum=2000000)
    result = fft.irfft(np.log(np.maximum(abs(fft.rfft(x, axis=0)), 1e-15)), n=n, axis=0)
    maximum = number(p, "max_quefrency", min(0.1, n / fs), 0, n / fs)
    count = min(n, int(maximum * fs) + 1)
    return output(
        {"quefrency": np.arange(count) / fs, "cepstrum": result[:count]},
        {"method": "real cepstrum; log magnitude floor 1e-15"},
        {"quefrency": "s", "cepstrum": "log coefficient"},
        {"kind": "line", "x": "quefrency", "y": "cepstrum"},
    )


def synchrosqueezed(read, n, c, fs, p):
    if c != 1:
        raise ValidationError("Select one channel for synchrosqueezing.")
    x = dense(read, n, c, maximum=32768)[:, 0]
    ssq = optional("ssqueezepy")
    stride = integer(p, "time_stride", max(1, n // 1024), 1, n)
    tx, wx, f, scales = ssq.ssq_cwt(x, nv=integer(p, "voices", 16, 4, 32), fs=fs, astensor=False)
    tx = np.asarray(tx)
    f = np.asarray(f)
    order = np.argsort(f)
    return output(
        {
            "time": np.arange(0, n, stride) / fs,
            "frequency": f[order],
            "power": abs(tx[order, ::stride].T) ** 2,
        },
        {
            "method": "ssqueezepy generalized-Morse CWT synchrosqueezing",
            "boundary": "reflected padding; inspect edge effects",
            "time_stride": stride,
            "not_psd": True,
        },
        {"power": "synchrosqueezed coefficient²"},
        {"kind": "image", "x": "time", "y": "frequency", "z": "power"},
    )
