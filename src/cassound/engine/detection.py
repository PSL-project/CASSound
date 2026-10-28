"""Typed, three-valued feature queries and interval annotations."""

import numpy as np
from . import features
from .common import number, dense, output
from ..domain.errors import ValidationError

UNITS = {
    "rms": "FS",
    "peak": "FS",
    "dc": "FS",
    "mean_square": "FS²",
    "zcr": "ratio",
    "crest_factor": "ratio",
    "clipping_count": "count",
    "centroid": "Hz",
    "bandwidth": "Hz",
    "rolloff": "Hz",
    "flatness": "ratio",
    "entropy": "ratio",
    "flux": "ratio",
    "contrast": "dB",
    "dominant_frequency": "Hz",
    "tonal_prominence": "dB",
    "band_power": "FS²",
    "harmonic_count": "count",
    "frequency_drift": "Hz/s",
    "click_score": "ratio",
}


def validate_rule(rule, depth=0, counter=None):
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if not isinstance(rule, dict) or depth > 8 or counter[0] > 64:
        raise ValidationError("Query AST exceeds structure limits.")
    if len(rule) == 1 and next(iter(rule)) in {"all", "any", "not"}:
        key = next(iter(rule))
        children = rule[key]
        if key == "not":
            children = [children]
        if not isinstance(children, list) or not 1 <= len(children) <= 16:
            raise ValidationError("Boolean queries need bounded nonempty children.")
        for child in children:
            validate_rule(child, depth + 1, counter)
        return
    if set(rule) - {"metric", "op", "value", "unit", "hysteresis"} or not {"metric", "op", "value"} <= set(
        rule
    ):
        raise ValidationError("Predicate requires metric, op and value; code expressions are not supported.")
    metric = rule["metric"]
    op = rule["op"]
    value = rule["value"]
    if metric not in UNITS or rule.get("unit", UNITS.get(metric)) != UNITS[metric]:
        raise ValidationError("Unknown metric or mismatched units.")
    if op not in {">", ">=", "<", "<=", "==", "between"}:
        raise ValidationError("Unsupported comparison operator.")
    values = value if isinstance(value, list) else [value]
    if len(values) != (2 if op == "between" else 1) or any(
        type(v) not in (int, float) or not np.isfinite(v) for v in values
    ):
        raise ValidationError("Predicate values must be finite numeric bounds.")
    if op == "between" and values[0] > values[1]:
        raise ValidationError("Predicate bounds must be ordered.")
    if "hysteresis" in rule:
        number(rule, "hysteresis", 0, 0, 1e12)
        if op not in {">=", "<="}:
            raise ValidationError("Hysteresis is supported on >= and <= predicates.")


def evaluate(rule, bank):
    if len(rule) == 1:
        kind = next(iter(rule))
        children = rule[kind]
        if kind == "not":
            mask, valid = evaluate(children, bank)
            return ~mask & valid, valid
        values = [evaluate(child, bank) for child in children]
        masks = np.stack([v[0] for v in values])
        valids = np.stack([v[1] for v in values])
        valid = valids.all(axis=0)
        mask = masks.all(axis=0) if kind == "all" else masks.any(axis=0)
        if kind == "any":
            valid |= mask
        return mask & valid, valid
    values = bank[rule["metric"]]
    valid = np.isfinite(values)
    bound = rule["value"]
    op = rule["op"]
    if op == "between":
        mask = (values >= bound[0]) & (values <= bound[1])
    else:
        operations = {
            ">": np.greater,
            ">=": np.greater_equal,
            "<": np.less,
            "<=": np.less_equal,
            "==": np.equal,
        }
        mask = operations[op](values, bound)
        if rule.get("hysteresis", 0):
            active = np.zeros(values.shape[1], bool)
            new = np.zeros_like(mask)
            exit_bound = bound - rule["hysteresis"] if op == ">=" else bound + rule["hysteresis"]
            for i, row in enumerate(values):
                stay = operations[op](row, exit_bound)
                active = np.where(active, stay, mask[i]) & valid[i]
                new[i] = active
            mask = new
    return mask & valid, valid


def runs(mask):
    edges = np.diff(np.r_[False, np.asarray(mask, bool), False].astype("int8"))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def intervals(mask, hop, length, minimum, gap, fs, n):
    spans = []
    for a, b in runs(mask):
        start = int(a * hop)
        end = min(n, int((b - 1) * hop + length))
        if spans and start - spans[-1][1] <= round(gap * fs):
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return [(a, b) for a, b in spans if b - a >= round(minimum * fs)]


def detect(read, n, c, fs, query):
    validate_rule(query["rule"])
    p = query.get("parameters", {})

    def inspect(node):
        if "metric" in node:
            if node["metric"] == "harmonic_count" and "fundamental" not in p:
                raise ValidationError("Harmonic-count queries require an assumed fundamental parameter.")
            if node["metric"] in {"dominant_frequency", "centroid", "bandwidth", "rolloff"}:
                values = node["value"] if isinstance(node["value"], list) else [node["value"]]
                if any(v < 0 or v > fs / 2 for v in values):
                    raise ValidationError("Frequency-query bounds exceed the recorded Nyquist range.")
        else:
            kind = next(iter(node))
            children = [node[kind]] if kind == "not" else node[kind]
            for child in children:
                inspect(child)

    inspect(query["rule"])
    result = features.bank(read, n, c, fs, p)
    bank = result["arrays"]
    mask, valid = evaluate(query["rule"], bank)
    length = result["summary"]["frame_length"]
    hop = result["summary"]["hop_length"]
    mode = query.get("channels", "each")
    findings = []
    groups = (
        [([ch], mask[:, ch]) for ch in range(c)]
        if mode == "each"
        else [(list(range(c)), mask.any(axis=1) if mode == "any" else mask.all(axis=1))]
    )
    for channels, active in groups:
        for a, b in intervals(
            active, hop, length, query.get("min_duration", 0), query.get("merge_gap", 0), fs, n
        ):
            rows = (bank["time"] >= a / fs) & (bank["time"] <= b / fs)
            rms = float(np.nanmax(bank["rms"][rows][:, channels])) if rows.any() else None
            frequencies = bank["dominant_frequency"][rows][:, channels]
            finite = frequencies[np.isfinite(frequencies)]
            findings.append(
                {
                    "start_frame": a,
                    "end_frame": b,
                    "channels": channels,
                    "label": "Query match",
                    "measurements": {
                        "rms_max_fs": rms,
                        "dominant_frequency_hz": float(np.median(finite)) if len(finite) else None,
                    },
                    "score": 1.0,
                    "score_kind": "rule satisfied; not probability",
                }
            )
    return output(
        {"time": bank["time"], "match": mask, "valid": valid},
        {
            "findings": findings,
            "count": len(findings),
            "query": query,
            "frame_length": length,
            "hop_length": hop,
            "covered_frames": result["summary"]["covered_frames"],
            "unanalysed_tail_frames": result["summary"]["unanalysed_tail_frames"],
            "timing": "intervals are unions of full analysis-window support, not sample-exact onset estimates",
        },
        {"time": "s"},
        {"kind": "events", "x": "time", "y": "match"},
    )


DTMF = ["123A", "456B", "789C", "*0#D"]
ROWS = np.array([697, 770, 852, 941])
COLS = np.array([1209, 1336, 1477, 1633])


def dtmf(read, n, c, fs, p):
    if c != 1 or fs < 4000:
        raise ValidationError("DTMF needs one channel and sample rate at least 4 kHz.")
    x = dense(read, n, c, maximum=4000000)[:, 0]
    length = max(32, int(0.04 * fs))
    hop = max(1, int(0.01 * fs))
    if n < length:
        raise ValidationError("DTMF selection must be at least 40 ms.")
    times = np.arange(length) / fs
    win = np.hanning(length)
    # ±1.5% tolerant frequency search with explicitly specified DTMF families.
    frequencies = np.r_[ROWS, COLS]
    templates = np.exp(
        -2j
        * np.pi
        * frequencies[:, None, None]
        * np.array([0.985, 1.0, 1.015])[None, :, None]
        * times[None, None, :]
    )
    labels = []
    strength = []
    for start in range(0, n - length + 1, hop):
        segment = x[start : start + length]
        amp = 2 * abs(np.einsum("fkn,n->fk", templates, segment * win)).max(axis=1) / win.sum()
        lo = int(np.argmax(amp[:4]))
        hi = int(np.argmax(amp[4:]))
        otherlo = np.sort(amp[:4])[-2]
        otherhi = np.sort(amp[4:])[-2]
        ratio = (amp[lo] ** 2 + amp[hi + 4] ** 2) / (2 * max(np.mean(segment**2), 1e-30))
        twist = 20 * np.log10(max(amp[lo], 1e-20) / max(amp[hi + 4], 1e-20))
        valid = (
            amp[lo] > 0.005
            and amp[hi + 4] > 0.005
            and amp[lo] > 2 * otherlo
            and amp[hi + 4] > 2 * otherhi
            and -0.0 - 8 <= twist <= 4
            and ratio > 0.65
        )
        labels.append(DTMF[lo][hi] if valid else "")
        strength.append(min(1.0, ratio) if valid else 0)
    events = []
    for character in sorted(set(labels) - {""}):
        for a, b in intervals(np.array(labels) == character, hop, length, 0.04, 0.02, fs, n):
            events.append(
                {
                    "start_frame": a,
                    "end_frame": b,
                    "channels": [0],
                    "label": character,
                    "score": None,
                    "score_kind": "DTMF profile candidate; not authentication",
                    "measurements": {},
                }
            )
    events.sort(key=lambda e: e["start_frame"])
    return output(
        {
            "time": (np.arange(len(labels)) * hop + length / 2) / fs,
            "symbol": np.asarray(labels),
            "strength": np.asarray(strength),
        },
        {
            "findings": events,
            "decoded_text": "".join(e["label"] for e in events),
            "profile": "DTMF nominal families ±1.5%; 40 ms Hann, 10 ms hop, twist -8 to +4 dB",
        },
        presentation={"kind": "events", "x": "time", "y": "strength"},
    )


MORSE = {
    ".-": "A",
    "-...": "B",
    "-.-.": "C",
    "-..": "D",
    ".": "E",
    "..-.": "F",
    "--.": "G",
    "....": "H",
    "..": "I",
    ".---": "J",
    "-.-": "K",
    ".-..": "L",
    "--": "M",
    "-.": "N",
    "---": "O",
    ".--.": "P",
    "--.-": "Q",
    ".-.": "R",
    "...": "S",
    "-": "T",
    "..-": "U",
    "...-": "V",
    ".--": "W",
    "-..-": "X",
    "-.--": "Y",
    "--..": "Z",
    "-----": "0",
    ".----": "1",
    "..---": "2",
    "...--": "3",
    "....-": "4",
    ".....": "5",
    "-....": "6",
    "--...": "7",
    "---..": "8",
    "----.": "9",
}


def morse(read, n, c, fs, p):
    if c != 1:
        raise ValidationError("Morse profile needs one selected channel.")
    x = dense(read, n, c, maximum=4000000)[:, 0]
    unit = number(p, "unit_seconds", 0.06, 0.015, 1)
    step = max(1, int(fs * unit / 10))
    count = len(x) // step
    if count < 2:
        raise ValidationError("Selection is too short for the Morse unit.")
    energy = np.sqrt(np.mean(x[: count * step].reshape(-1, step) ** 2, axis=1))
    threshold = max(number(p, "amplitude_floor", 0.005, 0, 10), float(energy.max()) * 0.4)
    active = energy >= threshold
    pulses = runs(active)
    events = []
    code = ""
    begin = None
    last = None

    def flush(end):
        nonlocal code, begin
        if code:
            events.append(
                {
                    "start_frame": begin * step,
                    "end_frame": min(n, end * step),
                    "channels": [0],
                    "label": MORSE.get(code, "?"),
                    "score": None,
                    "score_kind": "specified Morse timing profile",
                    "measurements": {"code": code},
                }
            )
        code = ""
        begin = None

    for a, b in pulses:
        if last is not None and (a - last) * step / fs >= 2 * unit:
            flush(last)
            if (a - last) * step / fs >= 5 * unit and events:
                events[-1]["word_gap_after"] = True
        duration = (b - a) * step / fs / unit
        if begin is None:
            begin = a
        code += "." if 0.4 <= duration < 2 else "-" if 2 <= duration <= 4.5 else "?"
        last = b
    if last is not None:
        flush(last)
    text = "".join(e["label"] + (" " if e.get("word_gap_after") else "") for e in events)
    return output(
        {"time": (np.arange(count) + 0.5) * step / fs, "envelope": energy, "active": active},
        {
            "findings": events,
            "decoded_text": text,
            "unit_seconds": unit,
            "profile": "on/off amplitude timing; supplied unit; unsupported timings decode as ?; not a universal signal decoder",
        },
        {"envelope": "FS RMS"},
        {"kind": "events", "x": "time", "y": "envelope"},
    )
