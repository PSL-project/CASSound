# Calls, contracts and integration flows

These are normative implementation specifications. The examples illustrate the API we will build; this planning archive does not implement `cassound`.

## Public Python interface

```python
from cassound import Session, Region, AnalysisSpec, QuerySpec, ExportSpec

with Session(workdir="./session-data") as session:
    imported = session.import_media("recording.mkv", stream_index=1).wait()
    asset = session.asset(imported.asset_id)
    region = Region(revision_id=asset.original_revision_id,
                    start_frame=0, end_frame=asset.frames, channels=(0,))
    psd = session.analyze(region, AnalysisSpec(
        task="spectral.welch_psd", parameters={
            "window": "hann", "nperseg": 4096, "noverlap": 2048,
            "scaling": "density"})).wait()
    query = QuerySpec.load("tone-search.json")
    findings = session.detect(region, query).wait()
    session.export_result(psd.result_id, "psd.csv").wait()
    session.save_project("investigation.cassound").wait()
```

`import_media` accepts an absolute container stream index, consistent with probe results; it is not an ordinal among audio streams. `asset.frames` is the selected decoded stream's sample-frame count after import. An audio stream choice is required when ambiguity exists. Import completion returns only after sample metadata is stable; progressive preview is exposed by events, not a guessed final frame count.

| Public method | Result and semantics |
|---|---|
| `Session.probe(path)` | Job yielding container/stream metadata; does not create a project asset |
| `import_media(path, stream_index=None)` | Job yielding AssetRef and original RevisionRef |
| `asset(asset_id)` / `revision(revision_id)` | Small immutable descriptors, no full-file read |
| `read_samples(region, dtype="native")` | Bounded iterator or array by explicit request; exact native path supported |
| `analyze(region, AnalysisSpec)` | Job yielding ResultRef with arrays/tables/units/provenance |
| `detect(region, QuerySpec)` | Job yielding FindingSetRef |
| `edit(revision_ids, EditSpec)` | Job yielding a new RevisionRef and mapping; originals unchanged |
| `render(revision_id, ExportSpec)` | Job yielding exported audio ArtifactRef |
| `generate(GeneratorSpec)` | Job yielding synthetic asset/revision and ground-truth metadata |
| `measure(AcousticSpec)` | Job yielding calibrated/raw acoustic ResultRef and setup metadata |
| `encode(region, PayloadRequest, CarrierSpec)` | Job yielding RevisionRef and nonsecret channel trace |
| `extract(region, ExtractSpec)` | Job yielding ExtractionResult; decrypted bytes are ephemeral unless explicitly saved |
| `compare(CompareSpec)` | Job yielding alignment, residuals and evidence-linked differences |
| `forensic(region, ForensicSpec)` | Job yielding observations/candidates and limitations |
| `run_model(region, ModelTaskSpec)` | Job yielding time-mapped output, ModelSpec digest and evaluation metadata |
| `robustness(RobustnessSpec)` | Job yielding transformation/recovery/quality table |
| `report(ReportSpec)` | Job yielding PDF/HTML/JSON report ArtifactRef |
| `save_project(path)` / `open_project(path)` | Jobs with transactional identity/migration/relink results |
| `capabilities()` | Fast registry snapshot including availability, version, requirements and reason |
| `subscribe(callback)` | Event subscription with unsubscribe handle |

`Job.wait(timeout=None)` is a blocking convenience for CLI/notebooks; the GUI must subscribe to progress/completion instead. `Job.cancel()` requests cancellation. `Session.close()` shuts down transport and workers, flushes valid state and invalidates temporary secret handles. Public errors are typed and include a stable error code plus a user-readable explanation.

## Core structures

Use frozen dataclasses or equivalent immutable objects. All IDs are opaque strings. Store integer sample frames; derive display seconds from sample rate and explicit time maps. Timestamp origins use rational timebases where needed. Use half-open intervals, validate positive rates and bounds, and reject implicit channel mixing.

`Region`: revision_id, start_frame, end_frame, channels.

`AnalysisSpec`: task, version, parameters, precision, optional calibration_id.

`ResultRef`: result_id, revision_id, task/version, region, artifacts, units, normalization, validity, warnings, provenance_digest.

`Finding`: finding_id, revision_id, region, optional frequency_bounds_hz, measurement references, method/version, score, score_kind, review_status, reviewer_note.

`ArrayRef`: cache object ID, dtype, shape, byte offset/length, layout and read-only policy. The receiver resolves IDs through its own store, never a project-supplied arbitrary path. Validate shape × itemsize and cache ownership before mapping. Artifact/table formats are versioned.

`EditSpec`: operation name/version, inputs, parameters, output spec, optional automation references.

`ExtractionResult`: outcome, carrier/version, detected_regions, corrected_errors, optional payload_handle, authentication_state, trace_artifacts and warnings. `authentication_state` is `not_applicable`, `not_checked`, `failed` or `verified`; it must never infer success from readable text.

## Command envelope

Every slow API/CLI/GUI action produces an envelope:

```json
{
  "protocol_version": 1,
  "request_id": "req-example-001",
  "project_id": "proj-example",
  "command": "analysis.run",
  "payload": {
    "region": {
      "revision_id": "rev-source-001",
      "start_frame": 0,
      "end_frame": 480000,
      "channels": [0]
    },
    "task": "spectral.welch_psd",
    "task_version": 1,
    "parameters": {"nperseg": 4096, "noverlap": 2048,
                   "window": "hann", "scaling": "density"}
  }
}
```

Validate the envelope, command payload and registered algorithm parameters before queueing. The bundled initial schema validates envelope shape, the sample analysis request and a bounded generic payload for other declared commands; per-task schemas must be completed before those tasks are accepted in implementation.

Planned command namespace: `media.probe`, `media.import`, `project.open`, `project.save`, `analysis.run`, `detection.run`, `edit.apply`, `audio.render`, `audio.generate`, `acoustics.measure`, `message.encode`, `message.extract`, `forensics.run`, `model.run`, `robustness.run`, `report.export`, `job.cancel`. Only allowlisted commands and registered task IDs are executable. Never use dynamic `eval`, arbitrary module paths or shell command strings.

## Event envelope

Common fields: protocol_version, request_id, project_id, revision_id when relevant, event, sequence, payload. Events: `job.queued`, `job.started`, `job.progress`, `result.ready`, `findings.ready`, `revision.created`, `capabilities.changed`, `job.succeeded`, `job.failed`, `job.cancelled`, `transport.position`, `transport.device_lost`.

Sequence is monotonic per producer/request, not global wall-clock order. Rate-limit progress and transport events. Terminal events occur once. Consumers reject unrelated or stale request/revision events. Persist source/algorithm result provenance; do not treat transient UI progress events as forensic evidence.

## One complete detection call flow

1. GUI query builder creates a typed QuerySpec and obtains current Region.
2. `Session.detect` validates units/range/channel selection and creates `detection.run`.
3. Application services resolve source/revision and algorithm registry; job manager creates a bounded worker job.
4. Worker reads source-resolution blocks with necessary overlap, computes features, applies predicates and merges interval events.
5. Worker stages results with parameter/source identities; application atomically registers them after successful validation.
6. GUI receives `findings.ready`, verifies request/revision identity, updates the findings table and draws waveform flags plus spectrogram bounds.
7. Selecting a row dispatches a single selection-state update to all linked views. Audition requests transport playback; plots never implement playback themselves.

## Query representation

A versioned JSON AST with nodes `all`, `any`, `not`, `predicate`; no user code. Predicates declare supported measurement, comparison operator, bounds, units, method and tolerance. Event postprocessing declares minimum duration, gap merge, hysteresis and channel aggregation.

Tone example: local peak/ridge in 990–1010 Hz AND prominence ≥12 dB above a documented neighborhood noise estimator; duration ≥0.2 s; gap merge ≤0.05 s. Parameters include the window/hop and estimator settings. A truthy bin is not automatically an exact onset; event timing resolution is recorded.

Feature cache keys depend on all feature extraction parameters. Boolean combinations must define how different frame grids are aligned and interpolated; no implicit mixing of feature rows with mismatched timestamps.

## Editing flow

`edit.apply` validates DAG and parameter schema → creates new revision with TimeMap → updates undo history → invalidates affected viewport/results → optionally prerenders playback blocks → export renderer writes a separate destination. Results attached to earlier revisions remain accessible and are labeled accordingly. Measurement comparison never silently reuses stale arrays.

## Message wire format policy

Public container name: **CASM**, format version 1. The exact byte layout is frozen during P4 before implementation-dependent files are shared. Versioned specification covers magic, carrier profile ID, lengths, bounded public KDF parameters, random salt, nonce, authenticated header, ciphertext/tag and ECC/framing profile. Lengths, maximum payload, ECC blocks and byte ordering must have test vectors.

For the authenticated mode use a vetted AEAD; initial mandatory profile AES-256-GCM with a 12-byte unique nonce and 16-byte tag, Argon2id-derived key for passwords. A key-file mode uses a validated random key. Carrier/framing headers needed before decryption are public and authenticated as associated data where applicable. Reject unsupported profiles and resource-exhausting KDF/header requests before allocation. Do not reuse keys/nonces or invent custom cryptographic primitives.

Pipeline: bytes → optional bounded compression → authenticated encryption → versioned envelope → ECC/interleave → carrier. Reverse pipeline verifies before releasing plaintext. Compression is inside encryption; decompression occurs after authentication with a strict expanded-size limit.

Carrier profiles: exact native integer PCM LSB; FSK with preamble/framing/clock recovery; documented echo, phase, spread-spectrum and transform-domain profiles; PSK/QAM/chirp laboratory profiles. Each declares supported audio spec, capacity, synchronization, output restrictions and benchmarked transformations. Different methods may have very different operating ranges. "Supports MP3 input" does not imply a particular embedded message survives MP3 encoding.

Secrets travel through ephemeral local handles, not serialized public commands or project JSON. GUI trace displays plaintext only through an explicit reveal action. Logs and reports default to ciphertext/digests and nonsecret diagnostics. User can explicitly save recovered data; never execute it.

## VST3 helper protocol

Handshake advertises protocol version, helper build, SDK version, architecture and maximum block/bus sizes. Calls: `scan`, `open`, `configure`, `parameters`, `set_parameters`, `process`, `get_state`, `set_state`, `close`. Control JSON is bounded/framed; audio travels through managed buffers. Plugin paths are selected by the user/discovery and validated; plugin state is opaque bytes with size limits, not code supplied by projects.

`process` includes sample rate, block frames, channel/bus layouts, transport position and automation points; result includes latency, tail information, buffer validity and errors. Never assume block size is a power of two. Timeout/crash quarantines the instance; dependent render fails cleanly. Do not silently pass dry audio while claiming the requested plugin processed it.

## CLI mapping

Planned commands: `cassound gui`, `probe`, `import`, `analyze`, `detect`, `edit`, `render`, `generate`, `measure`, `encode`, `extract`, `compare`, `forensic`, `model`, `robustness`, `report`, `doctor`.

All accept structured JSON specs where parameter sets are complex. `--json` selects machine-readable output; diagnostics use stderr and exit codes distinguish invalid input, unavailable backend, processing failure, cancelled job and authentication failure. Passwords are prompted or read from a restricted input channel, never passed as a normal logged command-line argument. CLI resolves to the same Session methods, not duplicate algorithms.
