# Editing, live I/O and acoustic measurements

## Revisions and coordinates

An edit renders a new float64 WAV and registers a new immutable revision. Its identity includes the canonical recipe, ordered parent revisions, scientific-library versions, output rate and rendered-file digest. Equal samples produced by different recipes retain different revision identities. Originals are never rewritten. Floating-point headroom is preserved; integer export rejects clipping instead of quietly saturating.

Portable projects embed both original and rendered audio, the edit graph, history, calibrations and environments. Scientific results/findings retain their own revision coordinates: they are not silently moved to an edited recording. Undo/redo changes the selected history revision; editing after undo branches the graph and discards the redo navigation branch, while previously rendered nodes remain available as sources.

Each clip mapping records parent revision, source channels, half-open source bounds, output bounds and track. Straight edits use affine timeline positions; reversed segments have descending source endpoints. Resampling/time stretching updates output coordinates; trim intersects source spans. Filters, convolution, spectral operations, pitch/time changes and plugins mix temporal support: their maps describe nominal position/provenance, not an invertible sample correspondence. Tails have no invented direct source mapping. Multitrack overlap is many-to-one. The recipe preserves routing matrices and exact processing order.

The current renderer deliberately bounds each dense input/intermediate/output to 8,000,000 scalar samples, 32 tracks, 256 clips and 64 effects per chain. This is distinct from P1/P2 streaming inspection of hour-long recordings. Select or split material for dense editing. A future streaming renderer must preserve state and these coordinate contracts before raising this limit.

## Timeline and effects

Use Selection → clip, add tracks/clips at explicit output frame positions, cut/split/paste unprocessed clips and render. The timeline JSON exposes per-track name, mute, solo, gain and effects; clips contain Region, output `at` frame and ordered effects. Overlapping faded clips form crossfades; linear and square-root/equal-power fade envelopes are available. Split/cut helpers require dry clips so they cannot silently misplace a stateful effect. Paste adds a clip without automatically shifting unrelated tracks.

Effects: gain and sample-frame dB automation, peak/RMS normalization, fades, reverse, trim, silence, channel matrices; causal/forward-backward FIR and Butterworth filtering, notch and peaking EQ; compressor, limiter, gate, downward expander; polyphase resampling, phase-vocoder pitch/stretch; convolution with retained/trimmed tail; STFT rectangle masks and noise-profile subtraction; harmonic hum notches and median impulse replacement. `editing.response` previews the actual filter design, poles/zeros, magnitude, phase and numerical group delay.

Dynamics use a linked sample-peak detector and one-pole attack/release smoothing in dB. The limiter includes an explicit sample ceiling; it is not a certified true-peak/lookahead limiter. Causal filters start at zero state and retain group delay. Forward-backward filtering is noncausal with boundary effects. STFT reconstruction checks NOLA, uses padded Hann frames and crops reconstructed output to the selection; a modified STFT need not correspond exactly to a realizable waveform. Restoration can remove wanted sound. Keep a reference selection and use Render residual for aligned reference-minus-processed audition and P2 measurements. Residual comparison refuses unequal rates, lengths or channel counts rather than inventing alignment.

## Generators and acquisition

Seeded sine/multitone, finite Fourier square/sawtooth, white/pink/brown noise, linear/log sweeps, AM/FM, impulse, step and silence create new sources. Generation never starts playback. Noise amplitude is normalized to the realized peak. Fourier waveforms can overshoot; periodic synthesis includes at most 4096 harmonics below Nyquist. FM uses a conservative Carson-band estimate, which does not imply mathematically finite sideband support. The generator records parameters and seed.

Install `cassound[audio]` for sounddevice/PortAudio. Device enumeration reports only actual host APIs; missing ASIO/WASAPI stays missing. This implementation does not provide explicit system-loopback capture. An OS-exposed recording endpoint can be chosen, but that is not advertised as a tested loopback backend. WASAPI exclusive mode is requested only for an actual WASAPI endpoint.

Playback is explicit, starts at reduced audition gain (−12 dB by default), and rejects a prepared source exceeding digital full scale. Looping is opt-in. A blocking I/O feeder thread reads prepared float32 audio; there is no file I/O or DSP in an audio callback. The cursor counts submitted frames and reports driver latency separately; it is not an exact DAC timestamp. Seeking aborts queued output and starts from a selection-relative frame. Recording writes a separate bounded floating-point WAV on a capture thread. Overflows/disconnects fail with an error, retaining any available recording as a labeled partial file; only successful captures are automatically imported. Reselect/restart after a device change. No hardware route is opened just by importing the package.

## Calibration and environment profiles

A calibration uses ≥0.5 s of unclipped mono reference tone with at least 95% Welch power near the stated frequency. The user supplies the calibrator SPL and identifies device, input channel, sample rate, gain, microphone, interface and capture mode. The profile stores Pa/FS, source digest and its own content digest. Changing any chain field or selecting a different channel invalidates calibrated-unit measurement. The app cannot independently verify that a user-entered hardware identity/gain is correct; traceability depends on the reference and acquisition record. SPL is unweighted (Z), referenced to 20 µPa. Do not infer calibration of an arbitrary imported recording.

Environment profiles save temperature, relative humidity and pressure with an explicit ideal dry-air model: `c = sqrt(1.4 × 287.05 × T_kelvin)` and `rho = p/(287.05 × T_kelvin)`. Humidity is recorded but not used in this model; no precision moist-air correction is claimed. Use the supplied speed/density in physical-unit models only with matching assumptions. Environment profiles are project-persistent and can be exported as JSON.

## Transfer, room decay and distortion

Select two synchronized channels in reference/output order for `acoustics.transfer`. H1 is Welch cross PSD divided by reference PSD with regularization, coherence and an excitation/reliability mask. Its IFFT is a periodic IR estimate. Single-record deconvolution uses zero-padded regularized spectral division, reports no invented coherence, and returns requested causal IR samples. Capture silence after excitation so the entire system tail is present. Circular/finite-record boundaries, drift, low excitation and nonlinearities can bias either estimate. Step response is the cumulative IR; estimated delay follows the documented reference-to-output convention.

`acoustics.room` treats the selected recording as an impulse response. It integrates squared IR backward (Schroeder curve), reports broadband EDT (0..−10 dB), T20 (−5..−25 dB), T30 (−5..−35 dB), C50/C80, D50 and center time. EDT is an extrapolated early-decay descriptor, not proof of a diffuse reverberant field. Fits require sufficient data, positive decay, R² threshold and peak-to-final-tail-noise margin; invalid estimates return a reason. Final 10% mean-square noise is a heuristic, with no noise subtraction. Band analysis can be performed by rendering explicitly specified filters first. These estimates are not ISO 3382 certification and do not duplicate REW's advanced truncation/noise compensation or Topt methods.

`acoustics.distortion` jointly fits known sine/cosine frequencies and DC. THD is RMS sum of fitted harmonics divided by fundamental; THD+residual adds unexplained RMS energy with the peak-to-RMS factor accounted for. The two-tone profile fits difference and third-order products, divided by RMS sum of primary amplitudes. It is labeled selected-product IMD rather than a standardized SMPTE/CCIF result. Drifting tones, unrelated content, leakage, acquisition distortion and clipping limit interpretation. A synthetic fixture is not a hardware analyzer accuracy certificate.

## Primary implementation references

- [SciPy STFT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.stft.html) and [iSTFT/NOLA](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.istft.html).
- [REW's definitions and room-decay caveats](https://www.roomeqwizard.com/help/help_en-GB/html/graph_rt60.html).
- [sounddevice streams](https://python-sounddevice.readthedocs.io/en/latest/api/streams.html).
- [Steinberg hosting helpers](https://steinbergmedia.github.io/vst3_doc/vstsdk/group__hostingBase.html), [VST3 communication](https://steinbergmedia.github.io/vst3_dev_portal/pages/Technical+Documentation/API+Documentation/Index.html) and [MIT license](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License).
