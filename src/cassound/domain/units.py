"""P1 sample representation conventions; physical calibration belongs to P2/P3."""


def normalized(samples, kind, bits):
    if kind == "unsigned":
        return (samples.astype("float64") - 2 ** (bits - 1)) / 2 ** (bits - 1)
    if kind == "signed":
        return samples.astype("float64") / 2 ** (bits - 1)
    return samples.astype("float64")
