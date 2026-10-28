import numpy as np
from scipy import signal
from .common import number, integer, optional, output
from ..domain.errors import ValidationError

VECTOR_NAMES = ("rms", "zcr", "centroid", "bandwidth", "rolloff", "flatness", "entropy", "flux")


def vectors(bank):
    result = np.column_stack([np.nanmean(bank[name], axis=1) for name in VECTOR_NAMES])
    return result


def cluster(bank, p):
    sklearn = optional("sklearn.cluster")
    x = vectors(bank)
    valid = np.isfinite(x).all(axis=1)
    k = integer(p, "clusters", 3, 2, 32)
    if valid.sum() < k:
        raise ValidationError("Not enough valid feature rows for clustering.")
    mean = x[valid].mean(axis=0)
    scale = np.maximum(x[valid].std(axis=0), 1e-12)
    model = sklearn.KMeans(n_clusters=k, random_state=integer(p, "seed", 0, 0, 2**31 - 1), n_init=10)
    labels = np.full(len(x), -1)
    labels[valid] = model.fit_predict((x[valid] - mean) / scale)
    return output(
        {
            "time": bank["time"],
            "labels": labels,
            "centers": model.cluster_centers_ * scale + mean,
            "feature_vectors": x,
            "scaler_mean": mean,
            "scaler_scale": scale,
        },
        {
            "features": list(VECTOR_NAMES),
            "invalid_label": -1,
            "algorithm": "standardized K-means; clusters are not semantic labels",
        },
        presentation={"kind": "line", "x": "time", "y": "labels"},
    )


def template_search(read, n, c, fs, p, template):
    if c != 1 or template.ndim != 2 or template.shape[1] != 1:
        raise ValidationError("Template search requires explicit mono selections.")
    t = template[:, 0]
    m = len(t)
    if m < 8 or m > 1000000 or m > n:
        raise ValidationError("Template must be 8–1,000,000 samples and no longer than search region.")
    t = t - t.mean()
    norm = np.dot(t, t)
    if norm < 1e-20:
        raise ValidationError("Constant template has no normalized correlation.")
    threshold = number(p, "threshold", 0.9, 0, 1)
    gap = max(1, int(number(p, "minimum_gap_seconds", m / (2 * fs), 0, n / fs) * fs))
    candidates = []
    block = 262144
    for start in range(0, n - m + 1, block):
        first = max(0, start - 1)
        end = min(n, start + block + m + 1)
        values = read(first, end)[:, 0]
        corr = signal.correlate(values, t, mode="valid", method="fft")
        sums = np.r_[0, np.cumsum(values)]
        sq = np.r_[0, np.cumsum(values**2)]
        variance = np.maximum(0, sq[m:] - sq[:-m] - (sums[m:] - sums[:-m]) ** 2 / m)
        scores = np.divide(
            corr, np.sqrt(variance * norm), out=np.zeros_like(corr), where=variance * norm > 1e-20
        )
        scores = np.clip(scores, -1, 1)
        if p.get("allow_inversion", False):
            scores = abs(scores)
        indices, _ = signal.find_peaks(np.r_[-np.inf, scores, -np.inf], height=threshold)
        for i in indices - 1:
            location = first + int(i)
            if start <= location < min(n - m + 1, start + block):
                candidates.append((float(scores[i]), location))
    accepted = []
    for score, start in sorted(candidates, reverse=True):
        if all(abs(start - other[1]) >= gap for other in accepted):
            accepted.append((score, start))
        if len(accepted) >= 10000:
            break
    accepted.sort(key=lambda item: item[1])
    events = [
        {
            "start_frame": start,
            "end_frame": start + m,
            "channels": [0],
            "label": "Template match",
            "score": score,
            "score_kind": "normalized cross-correlation; not probability",
            "measurements": {},
        }
        for score, start in accepted
    ]
    return output(
        {
            "time": np.asarray([start / fs for _, start in accepted]),
            "score": np.asarray([score for score, _ in accepted]),
        },
        {
            "findings": events,
            "template_frames": m,
            "polarity_invariant": bool(p.get("allow_inversion", False)),
        },
        presentation={"kind": "events", "x": "time", "y": "score"},
    )


def train(vectors_, labels, groups, p):
    split = optional("sklearn.model_selection")
    metrics = optional("sklearn.metrics")
    x = np.asarray(vectors_, float)
    labels = np.asarray(labels, str)
    groups = np.asarray(groups, str)
    if x.ndim != 2 or x.shape[1] != len(VECTOR_NAMES) or not np.isfinite(x).all():
        raise ValidationError("Training features must be finite and match the registered vector profile.")
    classes = np.unique(labels)
    if len(classes) < 2 or len(classes) > 32 or len(np.unique(groups)) < 4:
        raise ValidationError("Provide at least two classes and four independent source groups.")
    seed = integer(p, "seed", 0, 0, 2**31 - 1)
    fraction = number(p, "test_fraction", 0.3, 0.1, 0.5)
    folds = split.GroupShuffleSplit(n_splits=64, test_size=fraction, random_state=seed)
    train_idx = test_idx = None
    for a, b in folds.split(x, labels, groups):
        if len(np.unique(labels[a])) == len(classes) and len(np.unique(labels[b])) == len(classes):
            train_idx, test_idx = a, b
            break
    if train_idx is None:
        raise ValidationError(
            "No group-disjoint split covers every class in train and test; add independent sources."
        )

    def fit(indices):
        mean = x[indices].mean(axis=0)
        scale = np.maximum(x[indices].std(axis=0), 1e-12)
        centers = np.asarray(
            [((x[indices][labels[indices] == label] - mean) / scale).mean(axis=0) for label in classes]
        )
        return {
            "profile_version": 1,
            "features": list(VECTOR_NAMES),
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "centers": centers.tolist(),
            "classes": classes.tolist(),
        }

    evaluation_model = fit(train_idx)
    pred = predict_vectors(x[test_idx], evaluation_model)[0]
    model = fit(np.arange(len(x)))
    return output(
        {
            "confusion_matrix": metrics.confusion_matrix(labels[test_idx], pred, labels=classes),
            "training_vectors": x,
            "train_indices": train_idx,
            "test_indices": test_idx,
        },
        {
            "model": model,
            "evaluation_accuracy": float(np.mean(pred == labels[test_idx])),
            "evaluation_groups_train": np.unique(groups[train_idx]).tolist(),
            "evaluation_groups_test": np.unique(groups[test_idx]).tolist(),
            "classes": classes.tolist(),
            "evaluation": "held-out source groups; scaler fitted on training partition only",
            "final_model": "centroids/scaler refit on all supplied examples after held-out evaluation",
        },
        presentation={"kind": "table", "y": "confusion_matrix"},
    )


def predict_vectors(x, model):
    if (
        not isinstance(model, dict)
        or set(model) != {"profile_version", "features", "mean", "scale", "centers", "classes"}
        or model["profile_version"] != 1
        or model["features"] != list(VECTOR_NAMES)
    ):
        raise ValidationError("Unsupported safe centroid model schema.")
    mean = np.asarray(model["mean"], float)
    scale = np.asarray(model["scale"], float)
    centers = np.asarray(model["centers"], float)
    classes = np.asarray(model["classes"], str)
    if (
        mean.shape != (len(VECTOR_NAMES),)
        or scale.shape != mean.shape
        or centers.shape != (len(classes), len(mean))
        or not 2 <= len(classes) <= 32
        or np.any(scale <= 0)
        or not all(np.isfinite(v).all() for v in [mean, scale, centers])
    ):
        raise ValidationError("Invalid classifier dimensions/scales.")
    distance = np.linalg.norm(((x - mean) / scale)[:, None, :] - centers[None, :, :], axis=-1)
    index = np.argmin(distance, axis=1)
    return classes[index], distance[np.arange(len(x)), index]


def predict(bank, p):
    x = vectors(bank)
    valid = np.isfinite(x).all(axis=1)
    labels = np.full(len(x), "", dtype="<U128")
    distance = np.full(len(x), np.nan)
    if valid.any():
        labels[valid], distance[valid] = predict_vectors(x[valid], p["model"])
    return output(
        {"time": bank["time"], "labels": labels, "distance": distance, "valid": valid},
        {
            "method": "safe JSON nearest-centroid model",
            "score_kind": "standardized feature distance, not probability",
        },
        presentation={"kind": "line", "x": "time", "y": "distance"},
    )
