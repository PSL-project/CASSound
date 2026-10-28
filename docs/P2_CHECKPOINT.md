Historical P2 checkpoint record; current continuation is described in P3_CHECKPOINT.md.

# Continue from P2

This checkpoint includes the complete source through P1/P2 plus the Python wheel and source distribution under artifacts/python. Version: 1.0.0.dev2. There remains one planned full public release; internal packages are not partial public feature releases.

Start with README.md, measurements.md, P2_VALIDATION.md and FEATURES.csv. Scientific algorithms live in engine/, file/worker/result handling in runtime/analysis.py, shared entry points in api.py, and the real GUI panel in desktop/science.py.

P3 should extend immutable source revisions into a nondestructive edit/render graph, then implement editing, playback/recording, acoustics/calibration and the bounded native VST3 effects host. It must keep source-coordinate findings and numerical results tied to their original revision and introduce explicit mappings for derived audio. Do not put DSP into Qt callbacks or overwrite source files. Preserve the P1/P2 regression suite and decoder/algorithm provenance.

Native Windows qualification remains outstanding in this authoring environment. No Windows EXE or installer is bundled in this source checkpoint. A tested frozen Linux application was used for verification but is not shipped as a substitute Windows artifact.
