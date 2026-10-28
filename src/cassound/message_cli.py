"""Shared atomic file workflows. Credentials never enter command line arguments."""

import os
import json
import argparse
import getpass
import tempfile
from contextlib import contextmanager
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from .engine import messages
from .domain.errors import ValidationError


@contextmanager
def new_output(path):
    destination = Path(path).expanduser().resolve()
    if destination.exists():
        raise ValidationError("Output exists; choose a new file.")
    fd, temp = tempfile.mkstemp(prefix=".cassound-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w+b") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)


def normalized(samples):
    if samples.dtype.kind in "iu":
        return (samples.astype(float) - (128 if samples.dtype == np.uint8 else 0)) / max(
            abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max
        )
    return samples.astype(float)


def process(args, password, operation):
    for source in [args.input, getattr(args, "host", None)]:
        if source and Path(source).stat().st_size > 64 * 1024**2:
            raise ValidationError("Input exceeds 64 MiB file budget.")
    if operation == "message-encode":
        if Path(args.input).stat().st_size > messages.MAX_PAYLOAD:
            raise ValidationError("Payload exceeds 1 MiB.")
        envelope = messages.seal(Path(args.input).read_bytes(), password)
        if args.carrier == "lsb":
            if not args.host:
                raise ValidationError("LSB requires host WAV.")
            rate, x = wavfile.read(args.host)
            y = messages.embed_lsb(x, envelope)
        elif getattr(args, "host_mode", False):
            if not args.host:
                raise ValidationError("Host mode requires host WAV.")
            rate, x = wavfile.read(args.host)
            y, _ = messages.embed_host(normalized(x), envelope, args.carrier, args.symbol_frames)
        else:
            rate = args.rate
            if not 8000 <= rate <= 192000:
                raise ValidationError("Rate outside 8–192 kHz.")
            y = messages.synthesize(envelope, args.carrier, args.symbol_frames)
        with new_output(args.output) as handle:
            wavfile.write(handle, rate, y)
        return {
            "outcome": "encoded",
            "carrier": args.carrier,
            "path": str(args.output),
            "sample_rate": rate,
            "symbol_frames": args.symbol_frames,
        }
    rate, x = wavfile.read(args.input)
    if args.carrier == "lsb":
        envelope = messages.extract_lsb(x)
    else:
        decoder = messages.recover_host if getattr(args, "host_mode", False) else messages.demodulate
        envelope = decoder(normalized(x), args.carrier, args.symbol_frames)
    recovered = messages.unseal(envelope, password)
    with new_output(args.output) as handle:
        handle.write(recovered)
    return {"outcome": "verified", "bytes": len(recovered), "path": str(args.output)}


def main(argv):
    parser = argparse.ArgumentParser(prog="cassound " + argv[0])
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--carrier", choices=("lsb",) + messages.PROFILES, default="fsk")
    parser.add_argument("--host")
    parser.add_argument("--rate", type=int, default=48000)
    parser.add_argument("--symbol-frames", type=int, default=128)
    parser.add_argument("--host-mode", action="store_true")
    parser.add_argument("--key-file")
    args = parser.parse_args(argv[1:])
    credential = Path(args.key_file).read_bytes() if args.key_file else getpass.getpass("Message password: ")
    print(json.dumps(process(args, credential, argv[0]), ensure_ascii=False))
    return 0
