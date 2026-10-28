# CASSound user guide

Use Analyze for registry transforms/measurements, Edit for immutable render revisions and timeline/effects, Detect for saved rules/findings, Acoustics for calibration and system measurements, Messages for carrier experiments, Forensics for comparisons and reference matching, Reports for exports, and Models for offline CPU adapters. The shared waveform/source selection remains visible; docks can be moved, tabbed and saved. Ctrl+K opens the action palette. Exact frame bounds are half open.

## Messages

For encoding, Input is a binary payload; Reference is the host WAV for LSB or host mode. Output must be a new WAV. For decoding, Input is that WAV and Output is a new recovered data file. Enter a password or the path to a 32-byte raw key file. Key generation uses Output as a new key path. Keep keys separately; project history does not store credentials.

Select the same carrier, symbol size and host mode on extraction. Synthesized profiles use 48 kHz in the GUI. Echo host mode needs broadband mono input, normally N=512. The microscope shows native bit planes for LSB or pilot/coded-bit/symbol mappings for signals. Click to navigate the displayed sample region; only compare coordinates with the matching source. Use Clear private display after revealing UTF-8 text. Recovered files never execute automatically.

```powershell
cassound lab keygen --parameters '{"output":"private.key"}'
cassound message-encode payload.bin carrier.wav --carrier fsk --key-file private.key
cassound message-decode carrier.wav recovered.bin --carrier fsk --key-file private.key
cassound lab message.inspect --parameters '{"input":"carrier.wav","carrier":"fsk","symbol_frames":128}'
cassound lab message.robustness --parameters '{"carrier":"fsk","output":"channels.json"}'
```

Use shell quoting appropriate to your platform; JSON strings above are illustrative PowerShell forms. A channel sweep reports synchronized pre-correction BER only when available, recovery, distortion and runtime. Codec transforms need FFmpeg and may fail explicitly. It is not a measured real-world error guarantee.

## Forensics

Align/reference comparison accepts two files, resamples the reference explicitly when necessary, estimates delay and least-squares gain, and can save a residual WAV. Detailed results include source hashes, spectra and alignment parameters. The generic lab API also accepts `rate_scale` (.5..2) and `max_delay_seconds` (0..10).

ENF tracing is an Analyze task. Supplied-reference matching accepts a CSV with header and columns `time_seconds,frequency_hz`, increasing reference time values, and enough overlap. The match is a relative shift/RMSE against those values; flat or repeating traces can be ambiguous. Recorder profiles can be written to JSON and compared to another recording. Their content-sensitive distances do not identify a device by themselves.

## Models and reports

Use Models to install/remove `.casp` packs, verify inventory, and run a selected adapter. Audio processing requires an output WAV; score/detection runs may optionally save JSON. AudioSeal generator payload is exactly 16 bits. All neural adapters explicitly downmix/resample to mono 16 kHz. Import a processed output to analyze, audition or compare it with its original.

Reports exports the current scientific result in the chosen format. JSON retains method metadata; CSV and Parquet retain numerical values/dtypes/units. HTML previews are bounded; PNG/SVG/PDF plot up to four numeric arrays with explicit index axes where coordinates are not bound. Project provenance export contains a redacted project snapshot. Project portability and audio export remain separate controls.

See CASM.md, editing-and-acoustics.md, measurements.md, EXTENDING.md and RELEASE_VALIDATION.md for method contracts and limits.
