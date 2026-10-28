"""Application operations and their side effects are registered explicitly."""

WORKER_COMMANDS = frozenset(
    {
        "media.probe",
        "media.import",
        "media.export",
        "media.extract",
        "media.envelope",
        "project.open",
        "project.save",
        "project.relink",
    }
)
