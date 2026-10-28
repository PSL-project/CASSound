# GUI specification — CASSound

## Visual direction

An original cinematic signal laboratory: Arcane-inspired painterly atmosphere and angular crafted surfaces, combined with Cyberpunk 2077/Edgerunners-inspired graphic contrast and selective neon. The scientific canvas remains precise and readable. Use original CASSound branding and assets; no franchise logos, characters or extracted game/show artwork are required.

The supplied concept is a static desktop layout with synthetic data, not a functioning UI or measured analysis. It establishes hierarchy and component geometry rather than a final asset library. Final plots must be real data-rendered components.

## Design tokens

| Token | Value | Use |
|---|---|---|
| Background | `#0B1019` | Window and outer gutters |
| Surface | `#111B2A` | Tool panels and plot surround |
| Raised surface | `#182538` | Active cards and controls |
| Boundary | `#2B3A50` | Dividers and input outlines |
| Primary text | `#E9F0F7` | Normal text on dark surfaces |
| Secondary text | `#A2B3C8` | Labels with contrast verified in implementation |
| Signal cyan | `#45E6D0` | Primary waveform/selection outlines |
| Feature magenta | `#EF72C6` | Secondary tracks/analysis identity |
| Annotation amber | `#F4C469` | Findings and attention markers |
| Error red | `#FF7D85` | Failures/errors, with icon and text |
| Focus | `#BCEFFF` | Clear keyboard focus outline |

Grid: 4 px base; common spacing 8/12/16/24 px. Panel corners 6–8 px with a restrained 45-degree notch on major headers. Use faint texture only in artwork/chrome; never place texture under data, numbers, tables or input fields. Avoid blur/glow around small text and waveform lines. Small animations 120–180 ms; reduced-motion disables them. The UI never animates measurements decoratively.

Typography: use a legible bundled font with a verified redistribution license, plus a monospaced number font; system fallbacks must work. Typical content 13–14 px, headers 16–20 px, important readouts 22–28 px at 100% scaling. Use tabular numerals and align decimals/units. Verify actual contrast and readability rather than treating token values as an accessibility guarantee.

Two themes: **Studio** (default cinematic dark) and **Precision** (minimal texture, high-contrast controls). Both retain identical layouts, plot semantics and keyboard behavior. A lighter accessible theme can be added if contrast testing warrants it, but the two specified modes must work in 1.0.

## Workspace geometry

Reference design: 1600×1000 logical pixels. Minimum supported usable window target: 1280×800 logical pixels with collapsible side panels. Higher DPI changes device pixels, not logical information density. No fixed pixel dimensions may force clipping at 150%/200% scaling.

- Title/command bar: project identity, save state, open/search and global actions.
- Workspace switcher: Analyze, Edit, Detect, Messages, Forensics, Acoustics, Reports.
- Left dock: sources/streams, tracks/revisions, recipes and asset state.
- Central canvas: linked time ruler, overview, detailed plots/timeline and findings.
- Right dock: selection/method inspector and contextual controls.
- Bottom strip: transport, loop/rate, current time/sample, audio device and background jobs.

Dock layouts are named and resettable. The overview stays available across time-based workspaces. Large tables scroll/virtualize. Plot legends, unit labels and colorbars are part of the plot, not hidden in a settings dialog.

## Workspace contents

| Workspace | Primary surface | Context panels |
|---|---|---|
| Analyze | Waveform, spectrogram, PSD and feature lanes | Measurements, transform/window settings, units/calibration |
| Edit | Multitrack timeline, clips, automation, spectral selection | Effects rack, output spec, render graph, undo/history |
| Detect | Linked plots, event lanes and findings table | Visual query builder, method parameters, review controls |
| Messages | Carrier waveform/spectrogram and bit/symbol tracks | Encode/extract controls, encryption state, capacity/quality, decode trace |
| Forensics | Original/derived/second-source comparison and residual | Source identity, provenance, candidate findings, interpretation notes |
| Acoustics | Generator/acquisition setup, measured responses and decay | Device chain, calibration, environment, measurement validity |
| Reports | Report outline and preview | Included artifacts, privacy choices, formulas/methods, export |

Pattern search, ML tools and model management are available as contextual tools within relevant workspaces. Keep all features discoverable via a searchable command palette and capability browser; do not add a tab for every algorithm.

## Interaction rules

One authoritative `SelectionState`: active revision, channels, time region, optional frequency region, cursor and selection origin. Views subscribe and render it; they do not fight by broadcasting recursive updates. A view can be unlinked explicitly and displays a visible unlinked state.

- Wheel zooms around the cursor; modifier gestures distinguish horizontal time zoom and vertical scale. Provide buttons and keyboard equivalents.
- Drag selects time, or time/frequency in spectrogram mode. Exact frame/time/frequency fields remain available.
- Clicking a finding selects and centers its region across linked views; next/previous navigates findings.
- Spectrum peaks and query bounds are draggable, with numerical entry and clear unit displays.
- Space plays/pauses when focus is not in a text editor. Ctrl+O imports, Ctrl+S saves, Ctrl+Z undoes an edit and Ctrl+Shift+Z redoes. Command palette uses Ctrl+K. Avoid destructive global shortcuts while typing.
- Audio audition supports original, processed, residual and selected band. Output level is explicit; signal generation starts conservatively and never auto-plays a newly configured loud signal.
- Hover inspector gives time/frame/frequency/value and method resolution. A tooltip supplements visible controls; it never holds the only essential instruction.

## Results and measurement inspector

Every value shows a unit, scope, method and validity. Physical estimates show assumed medium/calibration. A missing requirement offers a concrete action such as load calibration, select voiced region or install model. Do not show 0 for invalid/not-computed data.

Finding colors indicate type; review status also uses icon/text. Probability is shown only for calibrated outputs; otherwise display detector score and threshold. Every experimental method includes its supported conditions and a link to measured evaluation results.

## Complete state design

Must design and test: empty project, import progress, corrupt media, no audio stream, multiple streams, missing external source, missing/corrupt model, model license details, unavailable device, recording overrun, stopped plugin, cancelled analysis, stale analysis, insufficient memory/disk, wrong password, damaged payload, no supported carrier found, unsaved project and recovery after interruption.

Long tasks stay cancellable and show current stage; percentages appear only where progress can be estimated. Failures preserve inputs and useful diagnostics. Never present a successful-looking chart after backend failure.

## Accessibility and scientific color

Keyboard-accessible controls, visible focus, readable text at OS scaling, screen-reader names for controls/tables, reduced motion, and color-independent finding status are mandatory. Use perceptually ordered colormaps for magnitude/PSD; a rainbow palette may be a user option but not the default scientific scale. Keep cyan/magenta branding out of magnitude encodings unless the colorbar meaning remains unambiguous.

Plots must support numerical inspection and table export for users who cannot rely on fine visual detail. Verify system font fallback, unicode filenames, Persian/UTF-8 message content and bidirectional text entry without corrupting numerical coordinate fields.

## Technical implementation

PySide6 `QMainWindow` with dock widgets, custom widget painting and stylesheet tokens. PyQtGraph for linked plot items, tiled image layers and scientific interaction. `desktop/theme.py` owns design tokens; `plots.py` owns chart components; `state.py` owns immutable view state; `actions.py` maps UI actions to Session requests. Domain algorithms never run inside plot paint handlers or widgets.

A polished style is achieved through custom components, spacing and consistent states. It does not require changing to a web stack. Validate performance on the actual renderer; OpenGL paths are optional and require tested fallback behavior.

## GUI acceptance

Use real window captures on Windows with deterministic synthetic fixtures. Compare layout/geometry with design snapshots; manually inspect readability and interactions. Verify at 1280×800 and 1600×1000 logical sizes and at 100/150/200% DPI. No clipped controls, invisible focus, overlapping readouts or illegible axes. A static concept image does not satisfy these checks.
