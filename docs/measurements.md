# Scientific measurements and P2 method profiles

P2 uses a versioned task registry shared by the desktop, CLI and Python API. `cassound --json tasks` lists the installed implementations, accepted parameter names and optional backends. Every result carries the source SHA-256, stream/revision, exact source-frame region, parameters, algorithm contract, application version and numerical-library versions. Runtime tasks never modify original samples.

## Coordinates, units and arrays

Source regions are half-open `[start_frame, end_frame)`. Input arrays are `(frames, channels)` with explicit channel selection and float64 normalization from the exact native sample cache. No implicit downmix occurs. Result `time` arrays are relative to the selection; `summary.time_origin_seconds` maps them onto the source timeline. Finding frame bounds are already absolute within the source revision. Channel numbers in Python/JSON are zero-based; GUI labels are one-based.

ResultRef describes immutable, individually hashed NPY arrays. `session.result_array(id, name)` verifies a stored digest and returns a read-only memory map. Undefined numerical values remain NaN in arrays; JSON summaries use null. Undefined is different from zero. `session.result_view()` prepares bounded display summaries in a worker. Peak/minimum pooling is a display operation; measurements never use downsampled plot data. Spectrogram color limits use display quantiles; saturated colors do not imply the stored PSD values were clipped.

Dense transforms have explicit working/output limits and may require a smaller selection. Default aggregate analysis output is 256 MiB per result. Large arrays do not pass through JSON control messages. Global time statistics and Welch PSD stream source data; dense FFT/Hilbert, advanced transforms and loudness use declared bounded selections. Spectrogram requests are region-based, budgeted and reproducibly cached. A full-file spectrogram at every possible resolution is not precomputed.

## Digital measurements

`time.metrics` reports peak, RMS, DC mean, mean square, sample extrema, crest factor, sign-bit zero-crossing count and signal energy. Definitions:

- RMS = sqrt(sum(x²)/N).
- Mean square = sum(x²)/N.
- Signal energy = sum(x²)/sample_rate, in FS² s for normalized digital samples.
- Crest factor = peak/RMS; undefined for silence.

These are digital quantities. They are not acoustic pressure, power in watts or energy in joules. `time.histogram` uses explicit bin bounds and reports underflow/overflow; it does not silently clip samples outside [-1,1].

## Spectra, peaks and time-frequency analysis

`spectral.fft` applies a named periodic window and coherent-gain normalization. Interior positive-frequency amplitudes are doubled; DC and an even-FFT Nyquist bin are not. Amplitude is peak FS, phase is radians, and `phase_valid` suppresses interpretation of negligible components. Zero padding decreases bin spacing but does not improve the observation's resolving power.

`spectral.welch_psd` averages complete modified periodograms with explicit window, frame length, hop, detrending, nfft and density/spectrum scaling. Density units are FS²/Hz. Integrate linear PSD bins with df to obtain band power; do not add dB values. Tests compare streamed output against SciPy for multiple windows and detrending settings.

`spectral.stft` returns complete-frame PSD, center times, frequencies and a dominant ridge. It uses no boundary padding. A ridge is the strongest non-DC component per frame, not necessarily pitch. `spectral.peaks` reports local spectral prominence; `spectral.harmonics` uses a user-specified fundamental and tolerance. It does not claim to infer a unique fundamental from any mixture.

`transforms.cwt` implements a discrete L2-normalized complex Morlet profile, omega0=6, with explicit frequencies and an edge-validity mask. Coefficient power is not PSD. CQT, mel and reassignment use the installed librosa backend. Reassignment retains reassigned coordinates and bins squared STFT magnitudes onto an inspectable grid; that grid is not relabeled as calibrated PSD. Synchrosqueezing uses ssqueezepy's generalized-Morse CWT profile on bounded mono selections. Window/padding/normalization conventions differ between transforms and are recorded in result metadata. Dense views may sample transform times for display/storage; the stride is explicit.

Real cepstrum uses log magnitude with an explicit numerical floor. Bispectrum and squared bicoherence use an ensemble of complete windows. High bicoherence can arise from deterministic tones and finite samples; it is not a statistical significance or authenticity verdict.

## Feature tracks and queries

The shared feature bank provides RMS, peak, DC, mean square, ZCR, crest factor, clipping candidates, centroid, bandwidth, 85% roll-off, flatness, normalized spectral entropy, flux, contrast, dominant-frequency estimates, tonal prominence, band power, harmonic count, frequency drift and click score.

Definitions worth distinguishing:

- Flux is the positive difference between L2-normalized magnitude frames.
- Contrast is the global 90th/10th spectral-power quantile ratio, not librosa's octave-band contrast.
- Tonal prominence compares peak power with a median neighborhood 5–30 bins away.
- Frequency estimates use a bounded parabolic interpolation of log power around the peak.
- Harmonic-count queries require a declared fundamental.
- Click score is the frame's largest sample difference divided by its RMS.
- Clipping candidates use a declared magnitude threshold; they do not prove analog/ADC clipping.

The bank reports its actual frame grid and any tail shorter than a complete final window. There is no implicit tail padding. Zero-energy spectra have undefined frequency/shape features. Queries use validity-aware Boolean logic: NOT does not turn an undefined frequency into a positive match.

QuerySpec supports all/any/not, numeric predicates, explicit units, between bounds, >=/<= hysteresis, minimum duration, gap merging and each/any/all channel aggregation. Frequency bounds must be within recorded Nyquist. Findings cover unions of full window supports, so onset/offset uncertainty is related to the window/hop. They are not sample-exact edit boundaries. Save, reload and review annotations independently of numerical array caches.

## Channels, filters and analytical signals

Channel relationships provide mid/side, Lissajous samples, correlation and a delay estimate. Positive delay means the second selected channel lags the first. Correlation uses a global-energy, biased normalization. Coherence is a Welch ensemble estimate; a single window is flagged as trivially uninformative. Cross-spectrum units and lag convention are explicit.

The Hilbert task returns envelope, unwrapped phase and instantaneous frequency with an amplitude validity mask. Instantaneous frequency has useful physical interpretation chiefly for narrowband/monocomponent signals; broadband mixtures and low-envelope regions require care. Edge effects remain.

Filter response designs bounded Butterworth or FIR profiles and exposes coefficients, poles/zeros, impulse response, magnitude, phase and group delay. This is a causal filter-design inspection task; it does not edit audio in P2. Group delay is ill-conditioned near response zeros.

## Speech and music profiles

Pitch uses a fixed-support YIN difference function with cumulative normalization and parabolic lag interpolation. Confidence is an algorithmic score, not a calibrated probability. Tests include a strong second harmonic, noise/silence rejection conditions and known periodic signals. Window length and pitch bounds must support the requested periods.

Speech acoustics requires a predominantly voiced mono selection. Formants are preemphasized autocorrelation-LPC candidates with frequency/bandwidth checks. HNR is a period-lag autocorrelation estimate. Jitter and shimmer use waveform peak intervals/amplitudes; they are not glottal-closure estimates or clinical measurements. These profiles need evaluation on representative real voices before any clinical or speaker-identification claim.

Music tasks expose mel features, MFCCs, onset strength/times and beat/tempo estimates through librosa. The activity task is a transparent energy/ZCR/flatness rule returning candidates. It is not a general speech-language detector. Music/onset/tempo results depend on the material; success on a toy tone is an execution check, not a music benchmark.

## Loudness and physical quantities

Integrated loudness uses pyloudnorm's declared ITU-R BS.1770-4 implementation for explicitly selected mono/stereo. Momentary and short-term tracks are ungated K-weighted means over 400 ms and 3 s, sampled every 100 ms. LRA uses complete 3 s windows, -70 LUFS absolute gating, -20 LU relative gating and P95-P10. Synthetic EBU Tech 3342 profiles are tested, but no blanket EBU-mode certification is claimed. The 4x Kaiser-sinc true-peak estimate is tested independently against an intersample-peak signal; it is not labeled a certified Annex-2 meter.

`physics.measurements` separates:

- Wavelength: lambda=c/f from an explicit frequency and assumed propagation speed.
- Digital signal measurements: no physical calibration implied.
- Pressure/SPL: requires an identified recording-chain calibration and Pa-per-FS factor.
- Intensity from pressure: additionally requires an explicit progressive-plane-wave assumption and medium density/speed.

A generic file cannot establish emitted acoustic energy in joules, unknown recording gain or a spatial intensity field. Calibration input must correspond to the actual microphone, gain and processing chain.

## Pattern search and classification

Template search uses bounded-block, normalized cross-correlation with DC removal, variance normalization, optional polarity invariance and nonmaximum suppression. It requires explicit mono selections at the same sample rate. Full source coordinates and similarity scores are returned, not probabilities. A constant template is invalid.

Clustering standardizes the registered feature vector and runs deterministic K-means. Labels are cluster numbers, not semantic sound classes. Classification uses a safe JSON nearest-centroid model. Training and prediction use the same frame feature profile. Source hashes define groups for a group-disjoint evaluation split; the evaluation scaler is fitted only on the training partition. The final reusable model is refitted after evaluation. Duplicate recordings under different file encodings can still need manual grouping/deduplication; file grouping alone cannot guarantee independence.

No pickle or arbitrary model-code import is used by P2's centroid workflow. Full neural/model-backed processing belongs to P4.

## Reproducibility and project behavior

Analysis recipes, result descriptors and reviewed findings persist with projects. Large numerical caches are disposable and are not embedded in portable projects. After reopening elsewhere, reviewed annotations remain available; rerun the saved recipe to regenerate arrays. A matching recipe/source/backend identity produces the same result ID. Changes in relevant version metadata invalidate caches.

Result JSON exports contain metadata and current review overlays. Long-form CSV exports contain array name/index/value/unit; string values are escaped when needed to avoid spreadsheet formula interpretation. Original recordings cannot be overwritten by result export.

## Primary references

- SciPy Welch: https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html
- SciPy signal processing: https://docs.scipy.org/doc/scipy/tutorial/signal.html
- Librosa: https://librosa.org/doc/latest/index.html
- Synchrosqueezing implementation: https://github.com/OverLordGoldDragon/ssqueezepy
- Loudness implementation: https://github.com/csteinmetz1/pyloudnorm
- EBU Tech 3342: https://tech.ebu.ch/docs/tech/tech3342.pdf
- EBU Tech 3341: https://tech.ebu.ch/docs/tech/tech3341.pdf
- Group-aware evaluation: https://scikit-learn.org/stable/modules/cross_validation.html
