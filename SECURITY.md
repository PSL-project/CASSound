# Security

Do not attach sensitive recordings or keys to public issues. Use a private maintainer channel when one is established; no reporting address is invented by this repository.

CASSound handles media and project files as untrusted input. Project archives reject unsafe paths, duplicate entries, symlinks and excessive expansion. Workers use bounded JSON controls, not project-supplied Python deserialization. Decoders run in separate processes; this is process isolation, not an operating-system security sandbox. Keep native backends updated.

Processing is local. Ordinary P1 logs/events contain diagnostic state, not media uploads. Media paths and metadata can still identify a recording: review diagnostic output before sharing it. Future encryption tools must keep keys/plaintext out of ordinary project/event logs.
