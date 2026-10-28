# Developer guide

Use Python 3.12 x64 as the initial compatibility baseline. The `Session` facade owns a project, dedicated session directory and JobManager. Source identity is SHA-256 + absolute stream index; decode-cache identity additionally includes decoder version and sample-cache specification. Public arrays are `(frames, channels)`.

Layering: domain types have no Qt/media imports; application project state depends on domain; runtime adapters perform I/O/worker jobs; API composes these; desktop calls API only. The current command registry is deliberately limited to implemented P1 operations. Future packages extend it with validated payload schemas and real algorithms.

A job uses a bounded scheduling thread and dedicated child interpreter. Control/result/progress messages are JSON; decoded data remains in managed disk-backed NPY files with `allow_pickle=False`. Never call `Job.wait()` on the Qt thread. Subscriber callbacks run on the job/event-emitting thread and must be quick; marshal to Qt or poll futures. Do not call session shutdown/wait inside a subscriber callback.

Cancellation is accepted until result publication begins. Once publication begins, cancel returns false and the job finishes or fails; it never reports successful cancellation after committing a destination. File export publication can take time when staging crosses filesystems. Closing waits for in-progress publication. Normal worker cancellation is checked at 50 ms intervals in the parent. The worker tree is terminated on cancellation/timeout; cache entries already valid may remain as reusable data, while stage directories are removed.

The session directory is exclusively OS-locked. Cache writers serialize with another OS lock so independent workers do not over-admit disk usage. Cache storage defaults to 20 GiB, per-file decoded native cache to 8 GiB; both are configurable. Decode blocks use 65,536 frames. Envelope pyramids are built through disk-backed arrays. View payloads cap channel × point count. `Cache.prune` is explicit and must receive all active cache keys as pinned entries; active session data is never automatically evicted.

Projects are ZIP bundles with a versioned manifest and a simple SQLite document table, optional original sources and a database digest. Sources are verified before re-decoding. Missing or changed external sources load as unavailable assets and can be relinked to hash-matching media. Unsupported schema versions fail without changing active state; future migrations must operate on a copy. No fictitious older-format migration is supplied before such a format exists.

Session directories and decoded caches remain on disk after close to avoid unexpectedly deleting user data. Remove unused session directories or prune unpinned caches explicitly. Opening a portable project can materialize embedded sources in the managed session directory. An interrupted save does not replace the destination until publication; operating-system power-loss durability of a directory entry is not guaranteed across all filesystems.

Windows builds are native builds. This Linux implementation environment cannot execute a native Windows acceptance run. The CI workflow is supplied for that purpose; record its actual result before claiming success.

## Reproduce the tested development environment

With uv 0.13.4, run `uv sync --frozen --extra desktop --extra dev`, then `uv run --no-sync python -m pytest` and `uv run --no-sync ruff check src tests tools benchmarks`. The committed uv.lock records platform-specific dependency resolutions. Regular library installs use the declared supported version ranges; lock-based development/CI uses the tested set.

## P2 result and task extension

Install the features extra for optional backends. Engine functions accept a bounded source reader and return numerical arrays plus method metadata; they perform no Qt or project I/O. Register task names/parameter allowlists in engine/registry.py. Runtime/analysis.py validates source identity, runs algorithms, writes individually hashed NPY artifacts and publishes their metadata. It also prepares bounded display summaries outside the Qt thread. Add a meaningful numerical/reference test and a GUI/API integration check for a new task.

Queries persist as JSON ASTs with explicit units and validity semantics. Reviewed findings live in project state independently of disposable result arrays. Reopening a project elsewhere restores findings/recipes, not every large cached transform; rerun a recipe when arrays are absent.

Grouped classifier training uses consistent per-frame features in train and predict; group identity derives from source hashes. Keep scaling/evaluation isolated by group and use safe JSON model parameters. Never add pickle-based model loading to a project path.


## P3 contracts

Read editing-and-acoustics.md and native/vst3_host/README.md. `Session.edit`, `render_timeline`, `generate`, `residual`, `calibrate`, `environment_profile`, `prepare_playback`, `play`, `record` and `scan_plugin` are the shared application surface. Never put DSP, file decoding or native plugin loading into Qt callbacks. Add effects to the allowlist with numerical evidence and explicit remapping/support semantics. Internal IEEE float64 RIFF files intentionally omit timestamped PEAK metadata so repeated samples/recipes yield stable identities. No integer quantization happens until explicit export.

Native test configuration uses CASSOUND_VST3_HOST, CASSOUND_TEST_VST3 and optional CASSOUND_MDA_VST3. Use tools/build_native.py --fetch --mda for pinned sources. Build scripts need CMake >=3.25 and a C++17 compiler; Windows requires x64 Visual Studio Build Tools/Windows SDK. Rust/Node are not dependencies. Audio hardware is opened only by explicit play/record calls; fake-driver tests do not count as hardware qualification.
