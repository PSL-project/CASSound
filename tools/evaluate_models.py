"""Evaluate the pinned deepfake scorer on a user-supplied labeled CSV."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
from cassound.engine.neural import run
from cassound.runtime.laboratory import audio


def metrics(rows):
    labels = np.array([r["label"] for r in rows])
    scores = np.array([r["score"] for r in rows])
    pred = scores >= 0.5
    return {
        "count": len(rows),
        "tp": int(np.sum(pred & (labels == 1))),
        "fp": int(np.sum(pred & (labels == 0))),
        "tn": int(np.sum(~pred & (labels == 0))),
        "fn": int(np.sum(~pred & (labels == 1))),
        "roc_auc": float(roc_auc_score(labels, scores)) if len(set(labels)) == 2 else None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv", type=Path)
    p.add_argument("--workdir", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    rows = []
    seen = {}
    with a.csv.open(newline="", encoding="utf8") as handle:
        for item in csv.DictReader(handle):
            if len(rows) >= 5000:
                raise ValueError("Dataset exceeds 5000 records")
            path = Path(item["path"])
            label = int(item["label"])
            group = item["source_group"]
            if label not in (0, 1) or not group:
                raise ValueError("Label must be 0=bonafide / 1=fake; source_group is required")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:
                raise ValueError("Duplicate recording bytes in evaluation CSV")
            seen[digest] = group
            x, fs = audio(path)
            _, result = run(x, fs, "deepfake", a.workdir)
            rows.append(
                {"sha256": digest, "label": label, "source_group": group, "score": result["mean_fake_score"]}
            )
    if not rows:
        raise ValueError("Dataset is empty")
    result = {
        "overall": metrics(rows),
        "groups": {
            g: metrics([r for r in rows if r["source_group"] == g])
            for g in sorted({r["source_group"] for r in rows})
        },
        "rows": rows,
        "limits": "User labels/groups; threshold .5. No inference of training-set independence or calibrated probabilities. Supply unseen sources and document their licenses/provenance.",
    }
    with a.output.open("x", encoding="utf8") as handle:
        json.dump(result, handle, indent=2)


if __name__ == "__main__":
    main()
