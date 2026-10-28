from dataclasses import dataclass


@dataclass
class SelectionState:
    revision_id: str | None = None
    start_frame: int = 0
    end_frame: int = 0
    channels: tuple = (0,)
    viewport_generation: int = 0
