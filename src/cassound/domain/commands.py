from dataclasses import dataclass
import json
from .errors import ValidationError
from .types import freeze, thaw

COMMANDS = frozenset(
    {
        "media.probe",
        "lab.run",
        "edit.render",
        "audio.prepare",
        "audio.devices",
        "plugin.scan",
        "analysis.run",
        "detection.run",
        "result.export",
        "result.view",
        "media.import",
        "media.export",
        "media.extract",
        "media.envelope",
        "project.open",
        "project.save",
        "project.relink",
    }
)


@dataclass(frozen=True)
class Command:
    request_id: str
    project_id: str
    name: str
    payload: object
    protocol_version: int = 1

    def __post_init__(self):
        if self.name not in COMMANDS or self.protocol_version != 1:
            raise ValidationError("Unsupported command/protocol version.")
        raw = json.dumps(self.payload, allow_nan=False)
        if len(raw) > 65536:
            raise ValidationError("Command exceeds the control-message limit.")
        object.__setattr__(self, "payload", freeze(self.payload))

    def to_dict(self):
        return {
            "protocol_version": 1,
            "request_id": self.request_id,
            "project_id": self.project_id,
            "command": self.name,
            "payload": thaw(self.payload),
        }
