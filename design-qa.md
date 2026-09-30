# Claude flow-diagram production integration QA (28-Jul-2026)

## Source visual truth

- User-supplied flow-diagram screenshot, decoded for analysis at
  `C:\Users\Legion\AppData\Local\Temp\codex_target_flow_994x604.png`
  (994×604 px).
- Exact source renderer:
  `simulator_preview/hmi/svg_mimic.py`,
  `simulator_preview/hmi/svg_viewer.py`, and
  `simulator_preview/hmi/tokens.py`.
- Existing source render:
  `generated_outputs/ui_preview_qa/rescale-01-chain-fit.png`.

## Implementation evidence

- Production renderer:
  `simulator/ui/flow_svg/svg_mimic.py`,
  `simulator/ui/flow_svg/svg_viewer.py`, and
  `simulator/ui/flow_svg/tokens.py`.
- Production integration:
  `simulator/ui/process_mimic.py`, Monitor → Stockyard Layout, CHAIN view,
  light document skin, initial state, fit zoom.
- The production CHAIN drawing geometry, symbols, tokens, painter order and
  label-LOD behavior come directly from the Claude renderer. Intentional
  functional refinements made afterward are limited to compact stockpile
  hover content, live active-conveyor coloring, selected-path highlighting,
  and red disabled-line state.
- Focused integration/configuration tests: 7 passed; Monitor workspace tests:
  6 passed.
- The 28-Jul-2026 Benguerir extension now binds six physical stockpiles,
  nine mobile machines, three mine hoppers and three washing-line cards to
  the same renderer. The first mine-ore pair is deliberately wider than the
  equivalent Louta lanes. Stockpile hover titles expose only ID, tonnage,
  mine-neutral grade and repère position.
- Production server health: HTTP 200 on its existing local Streamlit process.

Viewport target: 994×604 content crop, CSS density 1, light skin. The SVG
viewer is responsive inside a 660 px inspection frame, so the same viewBox and
fit/LOD state should be compared after capture.

## Full-view and focused comparison

Source-level comparison confirms identical geometry, painter order, base
colors, typography instructions, machine symbols and LOD classes. Hover and
operational-state differences are the explicit refinements listed above.
Focused code/output checks confirm:

- fit zoom shows structural/site/process/destination labels only;
- machine, pile, grade and repère labels appear at 1.25×;
- rail, conveyor, rate and evidence labels appear at 2×;
- the selected plan bucket reaches machine state, state lamp, rate, position
  and hover text;
- the Benguerir SVG contains exactly six scheduler-bound physical piles,
  three hopper symbols and three OPERATING/configurable washing cards;
- every physical Benguerir stockpile hover contains tonnage, grade and repère
  position, while hopper hover omits invented rates/capacities;
- pan, wheel zoom, fit and double-click reset remain in the embedded viewer.

A browser-rendered production screenshot is still required to compare iframe
crop, CSS pixel size, font rasterization and surrounding Streamlit layout
against the source in one combined visual.

## Findings

- No static P0/P1/P2 mismatch: the renderer output itself is identical.
- Browser-rendered evidence is unavailable because the required in-app browser
  tool is not exposed in this session. Playwright CLI fallback requires user
  authorization under the selected Product Design workflow.

## Comparison history

1. Reused the exact source SVG renderer rather than reconstructing the drawing.
2. Replaced the production Plotly presentation seam while preserving the
   existing scenario/plan/simulation state builder, filters and inspectors.
3. Changed the Monitor default from GLOBAL to the source CHAIN composition.
4. Verified exact source/production SVG equality and focused app integration.
5. Added the six-pile Benguerir geometry and validated its SVG structure and
   hover contract without replacing the shared Claude renderer.

## Implementation checklist

- [x] Exact Claude drawing source copied into production.
- [x] Plan, simulation, inventory, flow and machine-state binding preserved.
- [x] Zoom/pan and progressive-label behavior preserved.
- [x] Focused renderer and Monitor tests passed.
- [ ] Capture production in a browser and perform the required combined visual
      comparison after the user authorizes Playwright CLI fallback.

final result: blocked

---

# Benguerir washing-line design QA

## Source truth

- User-supplied current Benguerir compact/global screenshot in the 22-Jul-2026 request.
- Previous local captures:
  - `generated_outputs/simulator_visual_qa/benguerir-current-global-1920x1080.png`
  - `generated_outputs/simulator_visual_qa/benguerir-current-detail-1920x1080.png`

The requested visual change is confined to the washing process block: show three rectangular lines, with an accessible distinction between operating and future status, without changing the yard topology or planning model.

## Implementation evidence

- Global, 1920×1080: `generated_outputs/simulator_visual_qa/benguerir-washing-lines-global-1920x1080.png`
- Global, 1366×768: `generated_outputs/simulator_visual_qa/benguerir-washing-lines-global-1366x768.png`
- Benguerir detail, 1920×1080: `generated_outputs/simulator_visual_qa/benguerir-washing-lines-benguerir-1920x1080.png`
- Benguerir detail, 1366×768: `generated_outputs/simulator_visual_qa/benguerir-washing-lines-benguerir-1366x768.png`

State: S3 initial state; tags, evidence and flows enabled. The installed in-app browser connector was not exposed in this session, so the Plotly figure was exported directly at both target viewports; Streamlit AppTest and renderer tests cover app integration and interaction-independent output.

## Comparison findings

- The formerly empty compact washing block now contains three stacked, bounded rectangles without overlapping its title, inlet/outlet, raw-yard assets or washed-yard assets.
- The detail view replaces the single neutral WASHING node with the same three-line stack while retaining the separate FILTRATION and SETTLING nodes and downstream routes.
- Line 1 uses green fill/solid border plus `● OPERATING`; Lines 2 and 3 use violet fill/dotted border plus `◇ FUTURE`. Status therefore does not rely on colour alone and does not reuse selection blue.
- The status legend explicitly identifies this as static configuration lifecycle, not live running state.
- All three labels remain readable at 1366×768 and 1920×1080. No card/title or card/card collision was observed.
- Geometry and solver-isolation tests confirm the cards remain inside the washing zone, inherit into GLOBAL/CHAIN, and cannot become machines, spaces, nodes, links or jobs.

## Iteration history

1. Preserved the existing staged Benguerir layout and introduced a separate display-only process-line primitive.
2. Used a stacked layout because the compact washing zone is narrow; the same ordering is reused in detail.
3. Added text, icons and line patterns after reviewing colour semantics so future state cannot be mistaken for selection or live machine state.
4. Propagated the evidence caveat into the app, assumptions register and Rev K report.

final result: blocked — static visual comparison and regression QA passed, but interactive in-app browser inspection could not run because the browser connector was not exposed in this session.

---

# Executive Overview planning-readiness QA

## Implementation evidence

- Desktop: `generated_outputs/simulator_visual_qa/overview-readiness-1920x1080.png`
- Standard laptop: `generated_outputs/simulator_visual_qa/overview-readiness-1366x768.png`
- Narrow viewport with sidebar open: `generated_outputs/simulator_visual_qa/overview-readiness-900x1000.png`

State: default S3 scenario loaded with no plan, one validation warning and no
blocking errors. The full Streamlit app was rendered in local headless Chrome;
the screenshots therefore include native buttons, the sidebar and the persistent
validation drawer rather than an isolated HTML mock-up.

## Findings and iteration

- Replaced the stretched metadata sentence with four compact facts and a clear
  loaded → check inputs → generate plan workflow.
- Kept exactly one primary `Generate Plan` control in the sidebar; the overview
  provides only secondary review/validation actions and a text handoff.
- Surfaced the highest-priority validation issue without expanding the detailed
  drawer and used textual status labels in addition to colour.
- Corrected the overview evidence count to include site-level evidence, matching
  the sidebar total (8 ASSUMED/TBC entities in the current S3 dataset).
- The first narrow capture exposed a compressed three-column action row. The
  sidebar handoff was moved to a full-width row and the final 900 px capture
  confirms readable buttons and wrapping.
- Four facts collapse to a 2×2 grid and the workflow collapses to one item per
  row below 980 px; no horizontal overflow or clipped card content was observed.

Final result: passed — responsive render QA plus Streamlit navigation, empty-
state, single-primary-action and page smoke tests passed.

---

# Corridor proximity correction QA

## Implementation evidence

- GLOBAL, 1920×1080 viewport: `generated_outputs/simulator_visual_qa/corridor-correction-global-1920x1080.png`
- GLOBAL, 1366×768 viewport: `generated_outputs/simulator_visual_qa/corridor-correction-global-1366x768.png`
- CHAIN, 1920×1080 viewport: `generated_outputs/simulator_visual_qa/corridor-correction-chain-1920x1080.png`
- CHAIN, 1366×768 viewport: `generated_outputs/simulator_visual_qa/corridor-correction-chain-1366x768.png`

The full local Streamlit app was rendered in headless Chrome. GLOBAL uses the
S3 initial state; CHAIN uses the cached S3 demo state. The screenshots isolate
the rendered Plotly mimic so the schematic remains legible at the laptop width.

## Findings

- C1 remains bound to Louta and reads `provisional ≈2 h · nearer`; C2 remains
  bound to Benguerir and reads `provisional ≈4 h · farther` in both views.
- C1 is visibly shorter than C2. Measured schematic polyline lengths are
  25.77 versus 33.37 units in CHAIN (C2 is 1.30× longer), and 23.82 versus
  32.32 units in GLOBAL (C2 is 1.36× longer).
- Both subtitles remain readable at 1366×768 and 1920×1080, with no observed
  corridor-label/node collision or corridor crossing.

Final result: passed — the composite geometry and labels consistently show
Louta/C1 as nearer and Benguerir/C2 as farther at both desktop widths.

---

# Post-plan overview layout visibility QA

## Evidence

- Clicked S3 solve, 1920×1080: `generated_outputs/simulator_visual_qa/post-fix-overview-s3-clicked-solve-1920x1080.png`
- DOM metrics: `generated_outputs/simulator_visual_qa/post-fix-overview-s3-clicked-solve-dom.json`

## Findings

- The layout had not been deleted: it was built after the feeder/configuration
  strips and Plotly appended 255 shapes plus 139 annotations incrementally.
  The synchronous build took roughly 9–18 seconds and left the chart below the
  visible fold while Streamlit still showed `Stop` / `solve in progress`.
- Shape and annotation validation is now batched once; server-side composite
  figure construction is approximately 0.07 s.
- The overview toolbar defaults collapsed, the chart renders before context
  strips, and the finished solve triggers one clean rerun. In clicked-solve QA,
  the chart starts at y=491 px with about 589 px visible without scrolling;
  the sidebar is idle, Generate Plan is enabled and the plan is FEASIBLE.
- No browser-console, page or server errors were observed. The full engineering
  toolbar remains expanded on page 15.

Final result: passed — the planned layout is visible in the initial viewport
and no longer appears to disappear during or after plan generation.

---

# Louta process-mimic + stockyard correction QA (25-Jul-2026, report Rev T)

## Evidence

- Source verified by reading pp. 1–8 of *Louta Beneficiation Plant Process
  description*, Rev B (IFA), 25-Apr-2023 (`C:\Users\Legion\OneDrive\Desktop\
  Process docs\Beneficiation docs\Louta process description.pdf`). Document
  control is inconsistent in the source itself: cover
  `Q80130-LOU-L02-PR-REP-00002` vs body header `…-REP-00004`.
- Screenshots (headless Chrome via Playwright, 1920×1080, full page, S3 loaded
  through `?scn=s3&page=15`):
  - Louta detail, **Initial state**:
    `generated_outputs/simulator_visual_qa/louta-corrected-initial-1920x1080.png`
  - Louta detail, **Final plan** (after a real in-browser solve):
    `generated_outputs/simulator_visual_qa/louta-corrected-final-1920x1080.png`
  - Composite **Global stockyard layout** (compact Louta block):
    `generated_outputs/simulator_visual_qa/louta-corrected-global-1920x1080.png`
  - Machine-readable digest: `…/louta-corrected-qa.json`

## Findings

- `MINE / screening · TBC` and the `MINE (extraction)` / `screening / crushing`
  placeholder blocks are gone from every view; `102MG MINE STORAGE` remains a
  separate display/reference area with its two drawing-supported lines.
- Washing renders as two separate rectangles `LINE L1` / `LINE L2`, both
  `● OPERATING` (green), under `WASHING / BENEFICIATION — 2 PARALLEL LINES`,
  between ore reclaiming and product storage.
- Ore band: two horizontal stockpiles, `110LG-OL1/-OL2 · 123.6 kt · 50%` with
  contiguous grade bands (`G · 61.8 kt` + `H-Cd`/`L-Cd · 61.8 kt`), R0–R1000
  rulers, one shared stacker on the rail between the piles and one bridge
  reclaimer drawn across each pile at its R500 reference position.
- Product band: `LT-P1/LT-P2 · 95.8 kt · 50%` with TBT/BT/MT bands, one shared
  `130LG-ST-01` rail between the lines, `140LG-RC-01/-02` on their own lines,
  both routes reaching the common C1 corridor (`≈2 h · nearer`).
- No `RC count: BoD 1 vs PD 2` warning and no reclaimer-count stamp anywhere on
  the canvas; the discrepancy is in the gap register and assumption S3-A8.
- State switching: Initial reads `Scenario snapshot t0`; Final plan reads
  `Terminal boundary T=24 · no active job at T` and carries the caption
  `102MG + 110LG ore lanes stay at static t0 (reference-only)`.
- Zero browser console errors and zero page errors in all three captures.

Final result: passed.

**Re-shooting these screenshots:** stop any Streamlit already serving the app,
delete `simulator/**/__pycache__`, then relaunch (`PYTHONDONTWRITEBYTECODE=1
streamlit run app.py --server.port <free port> --server.headless true`). A warm
Python process keeps the previously imported geometry module — the first attempt
in this pass rendered the pre-correction Louta layout for that reason.
`GEOMETRY_REVISION` only busts the browser's Plotly element key, not a running
server's imported modules.

---

# M16 stockyard + PAP/drying-feed correction QA (25-Jul-2026, report Rev U)

## Evidence

- Source verified by reading `reference_material/engineering_documents/
  Q80130-MZI-M16-ME-PFD-0001[3].pdf` (Rev 3, IFU, 28-Oct-2025) — the tag order
  along the yard (`M16A-A-T06` → `M16A-A-T11` → `M16A-B-T06` → `M16A-B-T11` →
  `M16A-C-T06` → future `M16A-C-T11` → `M16A-D-T06`) plus six
  "HUMIDE PHOSPHATE STORAGE HANGAR CAPACITY 125000 T" label pairs confirm four
  current two-hangar bays and two future bays. Supporting:
  `Q80130-MZI-M16-PR-PRO-0001[1].pdf` Rev 2, §2.1 (8 + 4 × 125,000 t; PAP fed
  by 6 × 1,500 TPH conveyors, 3 running; drying by 2 × 1,299 TPH, out of scope).
- Screenshots (headless Chrome via Playwright, 1920×1080, full page):
  - S2 MPH **initial state**:
    `generated_outputs/simulator_visual_qa/m16-corrected-s2-initial-1920x1080.png`
  - S2 MPH **final plan** (after an in-browser solve):
    `…/m16-corrected-s2-final-1920x1080.png`
  - **ST01-A selected**: `…/m16-corrected-s2-select-ST01-A-1920x1080.png`
  - **RC01-A selected**: `…/m16-corrected-s2-select-RC01-A-1920x1080.png`
  - **Future Expansion enabled**: `…/m16-corrected-s2-future-1920x1080.png`
  - **S3 entire value chain**: `…/m16-corrected-s3-chain-1920x1080.png`
  - Machine-readable digest: `…/m16-corrected-qa.json`

## Findings

- Four two-hangar bays render with the five machine lines interleaved and
  labelled `M16A-A-T06 · ST01-A · 2,600 t/h stacking line — serves bay 1`
  (and equivalently for the other four). No `T06`/`T11` corridor rail and no
  detached "machine corridor" zone exists in any view.
- Bay identity chips read `BAY 1 · ST01-A + RC01-A` … `BAY 4 · RC01-B +
  ST01-C`, sitting in the gap between the two hangars of each bay.
- Lane labels stay concise (`HP-01 · 80 kt · TBT`); capacity, evidence, served
  machines, reclaim stream and the R0–R1000 convention are in the hover.
- **Selection highlights only served piles:** ST01-A → 2 rings (HP-01/HP-05);
  RC01-A → 4 rings (HP-01/HP-05/HP-02/HP-06) with the other bays dimmed and
  the `M16A-A-T11` line highlighted. The details panel reports
  `Reachable (configured) HP-01, HP-05` and `HP-01, HP-05, HP-02, HP-06`.
- Two separate gate banks render as `H01…H04` twice with full-name chips
  `M16A-A-H01…H04 · clamshell + rod gates` and `M16A-B-H01…H04 …`, feeding a
  `PAP / DRYING ROUTE CONFLUENCE — TBC` switchyard ahead of PAP-1 and
  DRY-FEED; a grey chip carries `PAP 6 × 1,500 t/h (3 running) · drying
  2 × 1,299 t/h OUT OF SCOPE`.
- Future Expansion adds bay 5 (`HP-09*`/`HP-11*`), `M16A-C-T11` with
  `MPH · RC01-C*` (REF badge, dashed), bay 6 and `M16A-D-T06`; with the
  toggle off none of them exist and the canvas is 26 units shorter.
- S2 final plan shows `Terminal boundary T=24 · no active job at T`.
- The S3 value-chain view keeps the corrected Louta (LINE L1/L2, 110LG ore
  lanes) and Benguerir (BG-RAW-1) blocks unchanged beside the new M16 bays.
- Zero browser console errors and zero page errors across all six captures.

Final result: passed.

**Re-shooting:** same precondition as the Louta pass — stop any running
Streamlit, delete `simulator/**/__pycache__`, relaunch with
`PYTHONDONTWRITEBYTECODE=1`; a warm process keeps the previously imported
geometry module.

---

# Claude Design global-stockyard redesign integration QA (30-Jul-2026)

## Comparison target

- Source visual truth:
  `Global Stockyard Layout Redesign/handoff/global-stockyard-layout.html`
- Source capture:
  `C:\Users\Legion\AppData\Local\Temp\global-stockyard-handoff-default-1920x1080.png`
- Implementation:
  production Streamlit page `http://localhost:8515/?page=15&scn=s3`
- Intended viewport/state:
  1920 x 1080 CSS px, device scale factor 1, global site, all grades,
  all equipment states, S3 initial state.

## Evidence available

- The source capture was opened and inspected at 1920 x 1080.
- The production redesign integration passes the focused adapter, viewer and
  SVG-flow integration suite: 18 tests passed.
- The changed Python modules compile, and the production Streamlit health
  endpoint responds successfully.
- A browser-rendered implementation screenshot has not yet been captured.
  Therefore there is no valid same-viewport, side-by-side comparison input.

## Findings

- [P0] Browser-rendered comparison evidence is missing.
  Location: production Streamlit global stockyard page.
  Evidence: the source capture exists, but the implementation capture,
  interaction trace and console inspection are unavailable because the
  approved in-app browser automation surface is not exposed in this session.
  Impact: typography, spacing, palette fidelity, hover behavior, filters,
  selection, pan/zoom and responsive fit cannot be accepted from code or HTTP
  health alone.
  Fix: with user approval, capture the production page through the available
  local Playwright browser at the same 1920 x 1080 state, combine it with the
  source capture, inspect hover/filter/selection interactions and console
  errors, then resolve any P0/P1/P2 visual differences.

## Required fidelity surfaces

- Fonts and typography: blocked pending implementation capture.
- Spacing and layout rhythm: blocked pending implementation capture.
- Colors and visual tokens: source palette recorded; implementation comparison
  blocked pending capture.
- Image quality and asset fidelity: the target is a code-rendered industrial
  schematic with no raster content assets; rendered linework fidelity remains
  blocked pending capture.
- Copy and content: canonical production IDs and compact production metadata
  are covered by focused tests; visible wrapping and truncation remain blocked
  pending capture.

## Open questions

- User approval is required before using the local Playwright browser because
  the preferred in-app browser is unavailable.

## Implementation checklist

- Capture the implementation at the matched viewport and state.
- Put source and implementation captures in one comparison image.
- Test site/grade/equipment-state filters, hover, selection, pan/zoom and fit.
- Check browser console errors.
- Fix and re-capture every P0/P1/P2 mismatch before handoff.

## Comparison history

- Pass 0: source captured; implementation capture blocked. No visual fixes were
  accepted from code inspection alone.

Final result: blocked
