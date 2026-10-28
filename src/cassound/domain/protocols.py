from typing import Protocol


class JobProtocol(Protocol):
    request_id: str

    def wait(self, timeout: float | None = None): ...
    def cancel(self) -> bool: ...


class EventSink(Protocol):
    def __call__(self, event): ...
