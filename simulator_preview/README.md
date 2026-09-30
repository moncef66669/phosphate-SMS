# MPH Operations Console — UI design preview

A **separate app** for reviewing a production/industrial console design for the
Monitor section, above all **Stockyard Layout & Flow**. Nothing under
`simulator/` is modified.

```
RUN-UI-PREVIEW.cmd                     (project root — port 8606)
# or
powershell -File Run-UI-Preview.ps1 -Port 8606 -Page flow -Scenario s3 -OpenBrowser
# or
cd simulator_preview && streamlit run app_preview.py
```

The production simulator (`RUN-LATEST-SIMULATOR.cmd`, port **8505**) can run at
the same time for side-by-side comparison. Each launcher only ever stops its
own app: the ownership guard matches on that app's own entry-point path.

Deep links: `?page=flow|situation|schedule|coverage` · `?scn=s1|s2|s3` ·
`?demo=1|s1|s2|s3` (one-off solve, used for screenshots).

---

## What changed, and why

The engineering content of the drawing is **unchanged**. What was redesigned is
the information architecture around it.

| Production page 15 | Console preview |
|---|---|
| Four separate places to answer "is anything wrong?" — pile warnings in the monitor view-model, tripped links + concurrency in the mimic, validation issues in a drawer at the very bottom, the stale-graph check as its own banner | **One ranked condition ribbon** with a single three-level severity vocabulary (`ALARM` / `CAUTION` / `ADVISORY`), backed by `hmi/conditions.py` |
| ~250 px of stacked radios, toggles and captions above the drawing | **Operator rail** on the left: view · state · time · layers · grade · job · asset focus · actions, in ~230 px of width instead of height |
| Asset details, coverage and gaps hidden in collapsed expanders below the fold | **Docked inspector** on the right, always visible: `ASSET` / `COND` / `FLOWS` / `COVERAGE` |
| Plan outcome only reachable on other pages | **KPI strip**: plan status, demand service, on-spec, unmet, throughput, safety breaches, and one tile per shared-capacity envelope |
| Light Streamlit chrome around a light drawing; colour used decoratively | **ISA-101 split**: graphite console chrome that recedes, ISA-101 light-grey drawing canvas, saturated red/amber reserved exclusively for abnormal conditions |

### Deliberately preserved
* **Grade-coloured pile fill** — process information, not decoration.
* **The evidence palette** (`CONFIRMED` / `DERIVED` / `INFERRED` / `ASSUMED` /
  `TBC`) and every dashed-TBC / display-only convention. Both are already
  redundantly encoded by icon, pattern or text; neutralising them to satisfy a
  style rule would destroy information.
* Every disclaimer: schematic/not-to-scale, repère position basis,
  no-live-control, display-only-is-not-a-solver-resource, changeover
  documented-but-not-modelled.

### New functions (not in the production page)
1. Ranked, deduplicated **condition list** with a machine-readable source per
   entry.
2. **Shared-envelope tiles + panel** — reads `Link.shared_group` and shows the
   free headroom (e.g. the M16 one-shared-2,600 t/h reception path).
3. **Asset focus** search across machines, stockpiles, conveyors *and*
   display-only reference geometry, in one control.
4. **Per-pile inventory sparkline** over the plan horizon with the safety-stock
   line marked.
5. **State digest export** — JSON of exactly what the screen is showing
   (flows, envelopes, conditions), for pasting into a review.
6. **Canvas skins** — `Document` (reference) and `Control room` (dark).
7. **Machine reachability matrix** on the Coverage page: the bay-adjacency rule
   is visible at a glance, including which cells the plan actually used.

---

## Architecture

```
simulator_preview/
  app_preview.py          console shell: title bar, solve, nav, footer
  .streamlit/config.toml  dark widget base (structure comes from hmi/css.py)
  hmi/
    bridge.py             read-only import boundary onto ../simulator
    tokens.py             design tokens + the ISA-101 rationale
    css.py                console stylesheet (never loads ui.theme)
    widgets.py            chips, ribbon, KPI tiles, kv tables, sparkline
    conditions.py         consolidated abnormal-condition model  (NEW)
    svg_mimic.py          the solid SVG drawing renderer         (NEW)
    svg_viewer.py         pan/zoom frame + label decluttering    (NEW)
    flow_diagram.py       stream/cross-section renderer — RETIRED
    canvas.py             canvas skins for the reference drawing
    page_layout_flow.py   the redesigned Stockyard Layout & Flow page
    pages_more.py         Situation / Schedule / Coverage console pages
  tests/test_function_parity.py
```

**Two drawings, switchable.** The rail's **Diagram** control chooses:

* **Solid (SVG)** — `hmi/svg_mimic.py`. The SAME arrangement, re-drawn.
  Default.
* **Production (reference)** — the unchanged
  `ui.process_mimic.process_mimic_figure`, restyled at the canvas level only.
  A test asserts the `Document` skin leaves every shape colour byte-identical,
  so this really is the current drawing.

### Why SVG rather than Plotly (and no, you don't have to leave Streamlit)
The arrangement in `ui.layout_geometry` is good; what read as thin was the
rendering. Plotly `shapes`/`annotations` cannot do stroke hierarchy, markers,
hatch patterns, or label chips with real backgrounds — everything ends up the
same hairline weight. Hand-authored SVG does all of it, and the output is a
plain string: the renderer is portable to Dash, NiceGUI, FastAPI + HTML or a
PDF plate unchanged if the app ever moves off Streamlit. It is delivered
through `components.html`, which renders in an iframe (see the QA note below).

### What the solid drawing changes — style only
* every filled body carries a darker outline, so shapes read as objects;
* belts are a thick dark casing with a lighter core and chevrons for
  direction, instead of a hairline;
* machines are compact plan-view bodies on their rail — travelling frame,
  bogies, a short directional boom stub, discharge chute (stacker) or bucket
  wheel (reclaimer), status lamp — sized to stay clear of the stockpiles;
* stockpiles are footprints with chamfered toes, material filling **along**
  the pad, free capacity dashed, safety stock ticked, repère ruler outside
  the pad so it never eats material;
* TBC and future are hatch patterns, not only dashed outlines;
* equipment labels sit on solid chips; pile labels sit directly on the
  coloured stock with a halo (see the review-fixes section);
* a drawing frame and title block — source reference, basis, bucket, time,
  and the vertical exaggeration **stated** (`V.E. ×2.1`), because a stockyard
  is long and shallow and true scale would render it a sliver.

**Order and adjacency are never recomputed.** Pile *sizes* are equalised and,
on single-site views, pile ROWS are re-spaced to a uniform pitch (see the two
sections below) — but the reading ORDER, left-right position and machine
docking are always preserved; the module never sorts or re-numbers the
geometry. The renderer consumes `PileGeo`, `RailGeo`, `ConveyorGeo.pts`,
`EquipGeo`, `ZoneGeo`, `ProcessLineGeo`, `ChipGeo`, `StampGeo`; machine
placement calls the production `machine_draw_pos` (fed the normalised
rail/pile), so a machine always sits on its rail at its repère.

### Uniform pile lanes (consistent size by design)
The source geometry draws equal-volume piles at very different sizes — in the
composite value-chain view a Benguerir product pile (36 × 3.0 units) and a
Louta product pile of the **same 191,512 t** (13 × 1.2) differed ~7× in area.
That is inconsistent *draw scale*, not the validated arrangement. `NormGeo`
re-draws every pile as one **uniform lane centred on its geometry centre**, so
order, adjacency and machine docking are preserved (centres never move) while
size is equalised; volume is carried by the fill bar + tonnage chip. Rails
recompute their span from the union of their flanked lanes, machines are placed
from the normalised rail + pile (so they stay locked to the pile), and conveyor
points are dragged through the same transform so belts pinned to a resized
edge or rail-end follow instead of dangling. Per-*site* scaling was rejected —
the geometry is volume-inconsistent even within a site (Louta draws 100 kt
wider than 247 kt), so only a per-pile rule delivers "equal volume → equal
size".

### Pile & machine labels (review fixes)
* **Split pile labels.** The space **name** sits *outside* the rectangle
  (small, above-left); only the **grade** goes on the coloured stock (small,
  centred, with a paint-order halo so it reads on fill or empty pad); tonnage,
  capacity, quality and evidence are **on hover**. Grade is the short band
  label — `MIX` only when the bands carry genuinely different grades, never the
  long grade id.
* **Compact machines that stay on their rails.** The machine body is small and
  the boom is a short directional **stub**, so the symbol sits on its own rail
  lane instead of reaching across and hiding the stockpile it serves (full
  reach is on hover). The vertical exaggeration is opened up (`V.E. ×2.1`) to
  give each row a clear machine lane.
* **Tiny machine names, off the stock.** The name is drawn small on the side
  *away* from the served pile (the boom points at the pile, the name goes
  opposite); repère + rate are on hover.
* **Display groupings.** `PILE_GROUPS` draws a set of piles as one titled
  bunker block (e.g. `BG-RAW-1` + `BG-RAW-2` → **MINE ORE**); members keep
  their fill/tonnage but drop their redundant individual ids. Display-only —
  both piles are still drawn and remain separate in the model.
* **Machine tags flip off process-line cards.** A tag that would cover a
  washing-line card is placed on the machine's other side (`_tag_hits_card`),
  so the cards — the "important elements" — are never overprinted.
* **Short process-line cards collapse to one row** (`● LINE 1`) instead of
  stacking two overlapping text rows.

### Harmonised row spacing (single-site views)
The source geometry gives each site a different pile-row pitch (MPH ~7, Benguerir
product ~9.4, Louta ore ~2.8) and Benguerir splits into raw + product stages
~28 units apart. On the LOUTA / BENGUERIR / MPH views `NormGeo(harmonize=True)`
re-spaces the pile ROWS onto one uniform grid (`ROW_PITCH`) so every block reads
with the same vertical rhythm — Louta's cramped rows open up, Benguerir's loose
ones tighten. Rows are **rigid**: each row keeps its uniform lane height and its
rails snap to a fixed clearance (a between-rail to the row midpoint), so a
machine can never be dragged onto its pile. A wide inter-**stage** gap is
**preserved** (it carries the washing/filtration equipment), only the pitch
*within* a stage is uniformised. Zones hug the piles they contain. The composite
**CHAIN** view is never harmonised — its y carries geography. A test suite pins
the machine-on-rail invariant, monotonic remap, and per-site uniform pitch.

### Zoom, pan and label decluttering
The sheet lives in `hmi/svg_viewer.py`: a fixed frame with a toolbar
(`−` / `+` / `Fit`, zoom readout, detail readout), **wheel to zoom at the
cursor, drag to pan, double-click to fit**, and `+` / `-` / `0` keys. The
sheet zooms inside the frame; nothing scrolls.

Label text is what overcrowds a schematic while elements are still small, so
every label carries a **detail tier** (`class="lod1|lod2|lod3"`) and the viewer
reveals tiers as you zoom:

| zoom | shown |
|---|---|
| fit … 1.25× | structure only — site titles, state lamps, destination boxes, pile bodies, belts, title block |
| 1.25 … 2.0× | + machine names + repère + rate, pile id + tonnage + grade, grade bands, evidence badges, repère R0/R500/R1000 |
| 2.0× … | + rail IDs, conveyor tags and rates, gate numbers, evidence/TBC prose, drawing stamps, quarter repères |

Long **process-stage** titles that overprinted the packed sub-yards are now
**hover-only** (the zone box + tooltip still names the stage); only the site
container title is drawn. Machine names, pile tonnage chips and evidence badges
moved to the zoom tier, so the fit view of the three-site chain reads as clean
structure instead of a wall of overlapping labels.

### Machine symbols and states
Machine **kind is silhouette, state is colour** — never the other way round:

* **stacker** — cross-braced square frame · feed arrow *in* from the belt ·
  single boom · discharge chute at the tip
* **reclaimer** — rounded frame with a slew ring · **counterweight boom**
  opposite the digging boom · bucket wheel at the tip · take-off arrow *out*

`svg_mimic.legend_svg()` renders a **symbol & state key** under the sheet using
the *same* `machine_glyph()` function as the drawing, so the key cannot drift
from it. It covers idle · stacking · reclaiming · travelling · maintenance ·
unavailable · future, for both machine types.

`?demo=states` is a review showcase that puts **stacking, reclaiming,
travelling and maintenance on one screen** at plan bucket 2. All four come from
the model — a maintenance calendar entry on ST01-C plus the plan's own activity
— not from painted-on colours. Idle needs a quiet bucket, Unavailable a
simulated outage, Future the Future-expansion layer.

### Retired
`hmi/flow_diagram.py` (a stream + cross-section re-arrangement) was reviewed
and rejected: it re-laid the yard out and encoded fill as height, an elevation
idiom on a plan-view layout. It is no longer offered in the UI. The module and
`tests/test_flow_diagram.py` are kept because several assertions are worth
holding regardless of renderer (bay/line reading order, shared capacity
counted once, site-boundary scoping).

### Why it is not a page inside the production app
`simulator/app.py:61` asserts `set(PAGES) == set(common.ALL_PAGE_LABELS)`, and
`common.page_label_from_qp` resolves `?page=N` **positionally**. Adding a page
would break that assert and renumber every existing deep link, including the
`?page=15` used by the QA drivers.

---

## Fidelity — stated honestly

| Page | Fidelity |
|---|---|
| **Layout & flow** | Every production page-15 control and information block has a counterpart. `tests/test_function_parity.py` is a **deletion guard** (substring grep over this app's source) — it proves nothing was dropped, not that each control works; the browser QA covers roughly a third of the table functionally. |
| Situation | New compact page. Not a port of `pages_overview.page_overview` — no scenario comparison, no editable inputs. |
| Schedule | New Gantt + delivery profile. Not a port of `pages_planning.page_schedule` — no replan controls or export tree. |
| Coverage | New reachability matrix. Not a port of `page_machine_coverage`. |
| Configure / Plan / Advanced sections | **Not in this preview.** Use the production app. |

Two production controls are **consolidated**, not removed:

* the sidebar `Plan timeline` basis + hour slider and the in-page bucket
  slider become one TIME control in the rail — it writes the same
  `PROCESS_MIMIC_BASIS_KEY` / `PROCESS_MIMIC_HOUR_KEY` session keys;
* the asset-type radio plus three separate selectboxes become one type-tagged
  `ASSET FOCUS` selector.

The six layer toggles are one multi-select with the same six options.

### Scope limitations
* **`Current simulation` state is unreachable in this preview.** There is no
  Simulation Control page here, so nothing ever populates `session_state.sim`;
  the state option is implemented and wired but never offered, and the
  conditions that depend on it (simulated outage windows, tripped links) cannot
  fire. Use the production app for simulation replay.
* The preview binds to `ui.common` (`init_state`, `load_scenario`,
  `request_solve`, `run_plan`, `typical_solve_s`, `revalidate`) and to three
  private `ui.process_mimic` helpers. If that surface changes in `simulator/`,
  this app must be re-checked.

---

## Verification

```
python -m pytest simulator_preview/tests -q      # 146 tests
```

`tests/test_flow_diagram.py` pins the new renderer: the bay/machine-line
reading order (a sign error there silently renumbers the bays), the
shared-capacity rule (the reception envelope must read 2,600 t/h, not
8 × 2,600), view scoping (a site view must not absorb the rest of the chain),
that an absent stage is stated rather than drawn as zero, that every
disclaimer is still on the sheet, and that repère 0/1000 map to the section
ends with out-of-range values clamped.

Covers: a probe per production control; the consolidation record; the
non-interference guarantees (bytecode guard proven in a fresh interpreter with
bytecode writing *enabled*, no production-stylesheet import, no production-page
registration, distinct port + ownership guard); the condition model's severity
mapping; `Document` skin colour-identity; `Control room` mapping/alpha rules;
inspector field contracts; HTML escaping; and the ISA-101 rule that saturated
red/amber appear only in alarm/caution selectors.

Browser QA: `generated_outputs/ui_preview_qa/ui-preview-qa.json` with explicit
`{check, expected, actual, pass}` records plus 1920×1080 screenshots.

## Status

Design review artefact. Not wired into the report pipeline, not part of the
production test suite, and not a certified control or safety HMI — it issues no
commands.

## QA note — the drawing lives in an iframe

`components.html` renders inside an iframe, so `page.inner_text("body")` in the
parent does **not** see drawing text. Probes must use
`frame_locator("iframe").last.locator("svg").text_content()` (SVG nodes are not
HTMLElements, so `text_content`, not `inner_text`). The browser QA carries an
explicit negative control asserting that a drawing-only string is present in
the frame and absent from the parent — without it, a probe can pass against the
console chrome and quietly stop testing the drawing.

The viewer is the FIRST `components.html` iframe and the symbol key the second.
LOD probes must read **visible** text only —
`querySelectorAll('text')` filtered on `getClientRects().length` — because a
hidden tier is still in the DOM and a naive `text_content()` check would pass
against labels the operator cannot see.
