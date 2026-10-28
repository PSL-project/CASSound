from dataclasses import dataclass, asdict
from types import MappingProxyType
from typing import Mapping, Any
from .errors import ValidationError


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class Region:
    revision_id: str
    start_frame: int
    end_frame: int
    channels: tuple[int, ...]

    def __post_init__(self):
        object.__setattr__(self, "channels", tuple(self.channels))
        if (
            type(self.start_frame) is not int
            or type(self.end_frame) is not int
            or self.start_frame < 0
            or self.end_frame <= self.start_frame
            or not self.revision_id
            or not self.channels
            or len(set(self.channels)) != len(self.channels)
            or any(type(c) is not int or c < 0 for c in self.channels)
        ):
            raise ValidationError("Region requires ordered frame bounds and unique nonnegative channels.")


@dataclass(frozen=True)
class ExportSpec:
    path: str
    format: str = "WAV"
    subtype: str = "PCM_24"
    start_frame: int = 0
    end_frame: int | None = None
    overwrite: bool = False

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Asset:
    asset_id: str
    sha256: str
    locator: str
    stream_index: int
    original_revision_id: str
    frames: int
    sample_rate: int
    channels: int
    native_dtype: str
    sample_kind: str
    valid_bits: int
    decoder: str
    timestamp_origin: str
    time_base: str
    probe: Mapping[str, Any]
    cache_key: str
    name: str = ""
    render_identity: str | None = None

    def __post_init__(self):
        expected = {
            ("unsigned", 8): "uint8",
            ("signed", 8): "int8",
            ("signed", 16): "int16",
            ("signed", 24): "int32",
            ("signed", 32): "int32",
            ("float", 32): "float32",
            ("float", 64): "float64",
        }
        if (
            type(self.frames) is not int
            or not 0 < self.frames < 2**63
            or type(self.sample_rate) is not int
            or not 0 < self.sample_rate <= 768000
            or type(self.channels) is not int
            or not 0 < self.channels <= 128
            or type(self.stream_index) is not int
            or self.stream_index < 0
            or expected.get((self.sample_kind, self.valid_bits)) != self.native_dtype
        ):
            raise ValidationError("Invalid asset sample specification.")
        if self.render_identity is not None and (
            len(self.render_identity) != 64
            or any(c not in "0123456789abcdef" for c in self.render_identity)
            or self.asset_id != "asset-render-" + self.render_identity
            or self.original_revision_id != "rev-render-" + self.render_identity
        ):
            raise ValidationError("Invalid rendered revision identity.")
        for value in (self.sha256, self.cache_key):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValidationError("Invalid asset digest/cache identity.")

    @classmethod
    def from_dict(cls, obj):
        return cls(**{**obj, "probe": freeze(obj["probe"])})

    def to_dict(self):
        return {name: thaw(getattr(self, name)) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class Revision:
    revision_id: str
    asset_id: str
    frames: int
    sample_rate: int
    channels: int
    parents: tuple = ()
    operation: str = "source"


@dataclass(frozen=True)
class ImportResult:
    asset_id: str
    revision_id: str


@dataclass(frozen=True)
class Event:
    request_id: str
    project_id: str
    event: str
    sequence: int
    payload: Mapping
    protocol_version: int = 1


@dataclass(frozen=True)
class AnalysisSpec:
    task: str
    parameters: Mapping = None
    version: int = 1

    def __post_init__(self):
        import json

        if not isinstance(self.task, str) or self.version != 1:
            raise ValidationError("Unsupported analysis task/version.")
        values = {} if self.parameters is None else thaw(self.parameters)
        if not isinstance(values, dict) or len(json.dumps(values, allow_nan=False)) > 32768:
            raise ValidationError("Analysis parameters must be a bounded JSON object.")
        object.__setattr__(self, "parameters", freeze(values))

    def to_dict(self):
        return {"task": self.task, "parameters": thaw(self.parameters), "version": self.version}


@dataclass(frozen=True)
class QuerySpec:
    rule: Mapping
    parameters: Mapping = None
    min_duration: float = 0.0
    merge_gap: float = 0.0
    channels: str = "each"
    version: int = 1

    def __post_init__(self):
        import json
        import math

        if (
            self.version != 1
            or self.channels not in {"each", "any", "all"}
            or type(self.min_duration) not in (int, float)
            or type(self.merge_gap) not in (int, float)
            or not math.isfinite(self.min_duration)
            or not 0 <= self.min_duration <= 86400
            or not math.isfinite(self.merge_gap)
            or not 0 <= self.merge_gap <= 3600
        ):
            raise ValidationError("Invalid query duration/channel/version settings.")
        for key in ("rule", "parameters"):
            value = thaw(getattr(self, key)) or {}
            if not isinstance(value, dict) or len(json.dumps(value, allow_nan=False)) > 32768:
                raise ValidationError("Query must be a bounded JSON object.")
            object.__setattr__(self, key, freeze(value))

    def to_dict(self):
        return {
            "rule": thaw(self.rule),
            "parameters": thaw(self.parameters),
            "min_duration": self.min_duration,
            "merge_gap": self.merge_gap,
            "channels": self.channels,
            "version": self.version,
        }

    @classmethod
    def load(cls, path):
        import json
        from pathlib import Path

        p = Path(path)
        if p.stat().st_size > 65536:
            raise ValidationError("Query recipe is too large.")
        return cls(**json.loads(p.read_text()))

    def save(self, path):
        import json
        from pathlib import Path

        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")


@dataclass(frozen=True)
class ResultRef:
    result_id: str
    revision_id: str
    task: str
    summary: Mapping
    arrays: Mapping
    region: Mapping
    provenance: Mapping

    @classmethod
    def from_dict(cls, obj):
        if not isinstance(obj, dict) or set(obj) != set(cls.__dataclass_fields__):
            raise ValidationError("Invalid scientific result descriptor fields.")
        identity = obj["result_id"]
        if (
            not isinstance(identity, str)
            or not identity.startswith("result-")
            or len(identity) != 71
            or any(c not in "0123456789abcdef" for c in identity[7:])
        ):
            raise ValidationError("Invalid scientific result identity.")
        if not all(isinstance(obj[k], dict) for k in ("summary", "arrays", "region", "provenance")):
            raise ValidationError("Scientific metadata must contain JSON objects.")
        try:
            region = Region(**obj["region"])
        except (TypeError, KeyError) as exc:
            raise ValidationError("Invalid result region.") from exc
        if region.revision_id != obj["revision_id"] or len(obj["arrays"]) > 128:
            raise ValidationError("Result revision/array inventory mismatch.")
        for name, value in obj["arrays"].items():
            if (
                not isinstance(name, str)
                or not name.replace("_", "").isalnum()
                or len(name) > 80
                or not isinstance(value, dict)
                or value.get("file") != name + ".npy"
            ):
                raise ValidationError("Unsafe result array descriptor.")
        return cls(**{k: freeze(v) for k, v in obj.items()})

    def to_dict(self):
        return {k: thaw(getattr(self, k)) for k in self.__dataclass_fields__}
