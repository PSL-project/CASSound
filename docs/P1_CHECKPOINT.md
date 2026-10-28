Historical P1 checkpoint record; current continuation is described in P2_CHECKPOINT.md.

# P1 source checkpoint

The media/project foundation is implemented and verified in the recorded Linux environment. P1 native Windows qualification remains pending; see validation.md and P1_ACCEPTANCE.csv. There is still one planned full CASSound 1.0 release. P2–P6 are tracked in FEATURES.csv and ROADMAP.md.

The checkpoint archive includes this repository plus Python wheel/sdist files under artifacts/python. These are internal development distributions with version 1.0.0.dev1, not a claim of full 1.0 feature completion. No Windows EXE or installer is bundled in this checkpoint.

To continue development: install/sync the locked environment, read developer-guide.md, run pytest and the GUI smoke, then implement P2 against the immutable source/session contracts. Keep original media and source frame coordinates unchanged. Use the confident, evidence-based README positioning described in positioning.md.
