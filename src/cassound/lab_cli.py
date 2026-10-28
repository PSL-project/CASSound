"""Shared isolated laboratory CLI and explicit extension trust controls."""

import argparse
import getpass
import json
from pathlib import Path
from .api import Session
from .domain.types import thaw


def main(argv):
    parser = argparse.ArgumentParser(prog="cassound " + argv[0])
    parser.add_argument("--workdir")
    name = argv[0]
    if name == "lab":
        parser.add_argument("operation")
        parser.add_argument("--parameters", default="{}")
        parser.add_argument("--password", action="store_true")
        parser.add_argument("--key-file")
    elif name == "model-install":
        parser.add_argument("path")
    elif name == "model-remove":
        parser.add_argument("model_id")
    elif name == "model-run":
        parser.add_argument("model_id")
        parser.add_argument("input")
        parser.add_argument("--output")
        parser.add_argument("--bits")
    elif name == "extension-enable":
        parser.add_argument("name")
    args = parser.parse_args(argv[1:])
    with Session(args.workdir) as session:
        if name == "extension-enable":
            value = session.enable_extension(args.name)
        elif name == "extensions":
            from .runtime.extensions import available

            value = {
                "installed": list(available()),
                "warning": "Enabling an extension trusts installed Python with your account permissions.",
            }
        else:
            credential = None
            if name == "lab":
                p = json.loads(args.parameters)
                op = args.operation
                if args.password:
                    credential = getpass.getpass("Message password: ")
                if args.key_file:
                    path = Path(args.key_file)
                    if path.stat().st_size != 32:
                        raise ValueError("Key must contain exactly 32 bytes.")
                    credential = path.read_bytes()
            else:
                op = {
                    "model-inventory": "model.inventory",
                    "model-install": "model.install",
                    "model-remove": "model.remove",
                    "model-run": "model.run",
                }[name]
                p = {k: v for k, v in vars(args).items() if k != "workdir"}
            value = session.laboratory(op, p, password=credential).wait()
        print(json.dumps(thaw(value), indent=2, ensure_ascii=False))
        return 0
