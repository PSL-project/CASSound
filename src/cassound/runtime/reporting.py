"""Safe scientific exports; arrays are hash-verified by the caller."""

import html
import json
from pathlib import Path
import numpy as np
from ..domain.errors import ValidationError, ResourceLimit, BackendUnavailable

SECRET_FIELDS = {
    "password",
    "key",
    "plaintext",
    "payload",
    "recovered",
    "locator",
    "path",
    "output_path",
    "review_note",
    "state",
    "component_state",
    "credential",
}


def redact(value):
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if str(k).lower() not in SECRET_FIELDS}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def write(destination, extension, manifest, arrays):
    metadata = redact(manifest)
    if sum(v.size for v in arrays.values()) > 8_000_000:
        raise ResourceLimit("Report exceeds 8M values.")
    if extension == ".html":
        sections = [
            '<!doctype html><meta charset="utf-8"><title>CASSound analysis report</title><h1>CASSound analysis report</h1><p>Preview: at most 128 values per array. CSV/Parquet contain full values.</p>'
        ]
        for name, value in arrays.items():
            sections.append(
                "<h2>"
                + html.escape(name)
                + "</h2><pre>"
                + html.escape(np.array2string(value.reshape(-1)[:128], threshold=128))
                + "</pre>"
            )
        sections.append(
            "<h2>Methods and provenance</h2><pre>"
            + html.escape(json.dumps(metadata, ensure_ascii=False, indent=2))
            + "</pre>"
        )
        Path(destination).write_text("".join(sections), encoding="utf8")
        return
    if extension == ".parquet":
        if sum(v.size for v in arrays.values()) > 250000:
            raise ResourceLimit("Parquet bounded to 250k values.")
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise BackendUnavailable("Install cassound[reports].") from exc
        rows = []
        for name, value in arrays.items():
            for index in np.ndindex(value.shape):
                rows.append(
                    {
                        "array": name,
                        "index": list(index),
                        "value": str(value[index]),
                        "dtype": str(value.dtype),
                        "unit": manifest["arrays"][name].get("unit", ""),
                    }
                )
        table = pa.Table.from_pylist(rows)
        table = table.replace_schema_metadata({b"cassound": json.dumps(metadata).encode()})
        pq.write_table(table, destination)
        return
    if extension not in {".png", ".svg", ".pdf"}:
        raise ValidationError("Unknown report format.")
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
    except ImportError as exc:
        raise BackendUnavailable("Install cassound[reports].") from exc
    figure = Figure(figsize=(11, 8), layout="constrained")
    FigureCanvasAgg(figure)
    names = [k for k, v in arrays.items() if v.size and v.dtype.kind in "biufc"][:4]
    for i, name in enumerate(names):
        ax = figure.add_subplot(max(1, len(names)), 1, i + 1)
        value = arrays[name]
        unit = manifest["arrays"][name].get("unit", "")
        value = abs(value) if np.iscomplexobj(value) else value
        if value.ndim == 2 and min(value.shape) > 1:
            if value.size > 2_000_000:
                raise ResourceLimit("Image exceeds 2M plotted cells.")
            image = ax.imshow(value, aspect="auto", origin="lower")
            figure.colorbar(image, ax=ax, label=unit)
            ax.set_xlabel("Column index")
            ax.set_ylabel("Row index")
        else:
            y = value.reshape(-1)
            if len(y) > 100000:
                raise ResourceLimit("Line exceeds 100k plotted points; select smaller region.")
            ax.plot(np.arange(len(y)), y, linewidth=0.7)
            ax.set_xlabel("Array index")
            ax.set_ylabel(unit)
        ax.set_title(name)
    figure.suptitle("CASSound · " + str(manifest.get("task", "Scientific result")))
    figure.savefig(destination, format=extension[1:])
