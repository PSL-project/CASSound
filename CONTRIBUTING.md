# Contributing

CASSound welcomes measurable improvements: an algorithm with reference fixtures, a clearer waveform interaction, codec coverage, accessibility, hardware tests or documentation.

Install `python -m pip install -e ".[desktop,dev]"`, run `python -m pytest`, and run `ruff check src tests tools`. Keep the headless API independent of Qt. Native sample reads must not pass through float32. Keep media originals read-only and use typed errors, bounded work and job-based processing.

Every completed feature needs reproducible evidence and honest capability status. Add small lawful synthetic fixtures or generators; never commit private recordings, passwords, payload secrets or model weights without clear redistribution rights. Describe observed behavior and validation in pull requests. Changes to public contracts or project formats must include compatibility documentation.
