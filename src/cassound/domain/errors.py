class CASSoundError(Exception):
    code = "processing_failed"

    def __init__(self, message, *, code=None):
        super().__init__(message)
        if code:
            self.code = code


class ValidationError(CASSoundError):
    code = "invalid_input"


class BackendUnavailable(CASSoundError):
    code = "backend_unavailable"


class Cancelled(CASSoundError):
    code = "cancelled"


class IntegrityError(CASSoundError):
    code = "integrity_failed"


class ResourceLimit(CASSoundError):
    code = "resource_limit"
