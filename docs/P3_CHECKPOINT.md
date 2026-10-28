# Continue from P3

This archive contains the complete source through P1/P2/P3, a Python wheel and a source distribution under artifacts/python. Version: 1.0.0.dev3. Internal checkpoints build toward one complete public CASSound 1.0 release.

Read README.md, editing-and-acoustics.md, P3_VALIDATION.md and FEATURES.csv. P3 adds engine/editing.py, generation.py and acoustics.py; runtime/editing.py, playback.py and vst3.py; desktop/editing.py; and a real native/vst3_host C++ project. Session remains the shared API; all heavy renders/analysis/spooling run through cancellable workers. Audio I/O uses explicit feeder threads and separate capture destinations. Rendering never overwrites originals.

The native host is built separately from pinned MIT dependencies using tools/build_native.py. The checkpoint does not include a Linux binary as a substitute for Windows. Source and Python distributions include the host sources, notices and build recipe. Windows/driver/device/clean-machine gates are still pending; controlled Linux tests are recorded individually.

P4 owns messages, cryptography/carriers, robustness sweeps, forensic comparisons/ENF, licensed model workflows, model packs and reports. Use rendered revisions and their explicit coordinate/provenance mappings, preserve native integer samples for LSB/bit-plane operations, keep secrets out of saved traces, and retain clear extraction outcomes. Do not treat a candidate, classifier score or hash as proof of authenticity. Read ROADMAP.md and the frozen protocol contracts before changing shared services.

Preserve P1–P3 tests, deterministic internal WAV rendering, immutable source identity, safe project archives, generic native effect parameter/state controls, crash quarantine and no-dry-fallback behavior. Scientific findings stay attached to the revision where they were computed. GUI workflows are functional; full Studio/Precision design, accessibility, layout and end-to-end polish remain P5 work.
