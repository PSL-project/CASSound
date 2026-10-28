# CASM v1 and CASSound carrier profiles

All integer fields below are big endian. Envelope header is `>4sBBII16s12s` (42 bytes): magic `CASM`, version 1, flags, original plaintext length, encrypted body length including tag, salt (16 bytes), nonce (12 bytes). Flag bit 0 means zlib compression; bit 1 selects a raw 32-byte key rather than a UTF-8 password. Other bits are rejected. Password KDF is scrypt N=16384/r=8/p=1, 32-byte output. AES-256-GCM authenticates the entire header as AAD; its 16-byte tag is included in body length. Salt/nonce are freshly random per encoding. Plaintext limit is 1 MiB; decompression follows authentication and must match the declared bounded length exactly.

Frame bytes are `CASF`, envelope length uint32, envelope CRC32 uint32, envelope. Each bit (MSB first) repeats three times. Majority decoding corrects one error per group; CRC detects remaining damage, and AES-GCM provides authentication. CRC is not a security primitive.

Native LSB uses one coded bit per flattened native integer sample. Mono/stereo native sample order is preserved. WAV 24-bit reads through SciPy are represented as left-justified int32 and exported as int32; this is a new 32-bit carrier, not a claim of preserving original container bit depth. Original files are never replaced. Floating-point samples are rejected for LSB.

Signal profiles have explicit symbol size N=64..4096 and 8N synchronization samples. The public pilot is NumPy default_rng(718235), choices [-1,+1]. FFT correlation detects the pilot and estimates signed gain; correlation must exceed .65. There is no automatic unknown sample-rate search. The body contains one/two/four bits per symbol depending on constellation. Profiles are version-specific controlled laboratory signals, not a general acoustic modem guarantee.

- FSK: cosine at 8/16 cycles per symbol.
- BPSK/phase synthesis: positive/negative cosine at 8 cycles per symbol.
- QPSK: quadrature carrier at 8 cycles, phases kπ/2.
- 16-QAM: I/Q levels {-3,-1,1,3}; common peak normalization.
- Chirp: cos(2π(4t+12t²)) or cos(2π(28t−12t²)), t=j/N.
- Spread: signed deterministic PN sequence, RNG seed 20261028.
- Echo synthesis: deterministic Gaussian excitation plus .6 circularly delayed copy at 8 or 16 samples.
- Transform synthesis: signed DCT-II basis index 8.

Host embedding is separate: spread adds PN to actual samples, phase sets signed FFT coefficient 8, transform sets signed orthonormal DCT-II coefficient 8, and echo adds a delayed actual-host copy. Echo requires broadband host flatness and uses real-cepstrum decisions. All prepend the pilot; whole-output gain may be reduced to retain .95 FS peak. No transparency claim is made. Use N=512 and a broadband host for echo. Stronger embedding trades audio change for recovery.

Inspection maps coded bit i to symbol floor(i/bits_per_symbol), sample region starting at `start_frame + symbol*N`, frame byte floor(i/24), and envelope byte floor(i/24)−12. Majority correction positions refer to decoded bit groups, not plaintext. Traces show bounded ciphertext previews; recovered plaintext is revealed only from the explicitly saved authenticated output.

Outcomes include supported signal absent, damaged frame, authentication failed, and verified recovered output. A pilot match is a candidate, not verified data. Wrong keys and tampering never yield verified plaintext. A negative scan does not prove absence of hidden messages.
