# Extending CASSound

Keep algorithms independent of Qt and use shared services for desktop, CLI and Python workflows.

## Scientific tasks

Implement `(read, n, c, fs, parameters)` in `engine/`. `read(start,end)` reads half-open source frames; `n` is region length, `c` channel count, `fs` sample rate. Use bounded `common.dense` or streaming reads as appropriate. Return `common.output(arrays, summary, units, presentation)`. Define numerical conventions, axes, units, source coordinates, assumptions and resource limits.

Register the function, parameter allowlist and optional backend in `engine/registry.py`. Registry tasks appear in the science UI and `Session.analyze`. Add a meaningful known-value fixture and declare dependencies in the appropriate extra. Update the lock, feature ledger and notices.

## Installed Python plugins

Expose a `cassound.analysis` entry point in an installed distribution. The entry point loads a callable `(api_version, register)`; require version 1 and register functions through `register(task_name, function, parameters=(), kind='line')`. Task names must begin with the entry point name plus `.`. For example:

```toml
[project.entry-points."cassound.analysis"]
mytools = "mytools.cassound:register_tasks"
```

```python
def register_tasks(api_version, register):
    if api_version != 1:
        raise RuntimeError("Unsupported CASSound extension API")
    register("mytools.measurement", measurement, parameters={"threshold"})
```

List installed extensions with `cassound extensions`. Enable one explicitly with `cassound extension-enable mytools --workdir CACHE`. Restart to load it. This trusts installed Python with your account permissions; it is not a sandbox. Portable project data cannot enable extensions. Frozen applications need extension code/dependencies bundled at build time; ordinary Python environments support installed entry points.

## Effects, models and messages

Use immutable render recipes and source mappings in `engine/editing.py` and `runtime/editing.py`. Declare latency/tail, parameter and bus constraints. Validate staged output before publication; never replace originals or publish bypassed audio as successful processing after a plugin failure. New native plugin formats belong in isolated hosts with cancellation, quarantine and license records.

Model adapters belong in `engine/neural.py`, with a pinned catalog/pack schema under `resources/` and pack validation in `runtime/models.py`. Document preprocessing, labels, limitations and license. Extend the data-only allowlist deliberately; never add arbitrary repository Python, pickle loaders or automatic project downloads. Add real CPU execution evidence and source-disjoint benchmark records before claiming accuracy.

Version any incompatible message protocol. Preserve authenticated headers, unique nonces, bounded decompression, integer PCM semantics and private worker transport. Each new carrier needs binary/Unicode, tamper/wrong-key/capacity, synchronization and channel tests. Host embedding and signal synthesis are different operations. Do not infer inaudibility or codec survival from ideal round trips.

## Services, GUI and reporting

New cancellable operations use a versioned allowlisted command, isolated worker and Session facade. Secrets use the private stdin path, never ordinary command payloads or persistent trace files. Result receipts must not contain plaintext, credentials or recovered bytes. Source/derivative hashes identify bytes; they do not establish authenticity.

Put controls under `desktop/`; reuse Session methods. Keep Qt updates on the GUI thread. Every menu item must call real code and show missing-backend/error/cancelled states. Keep exact bounds `[start,end)`, revision changes and stale-result checks consistent. Test a whole user journey rather than duplicating UI internals.

Extend `runtime/reporting.py` through `result.export`; verify paths/hashes, preserve precision, escape HTML, protect spreadsheet cells and redact private fields. Make previews, array indices and downsampling explicit. Update USER_GUIDE, README, FEATURES and validation records when behavior changes.
