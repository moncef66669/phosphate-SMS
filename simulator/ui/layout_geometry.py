"""Yard/display geometry CONFIGURATION for the process mimic (no Plotly here).

Every coordinate in this module is SCHEMATIC — there is no surveyed GA/plot
plan on file for any site. The supplied M16 stockyard layout (user, 15-Jul-
2026) confirms the RELATIVE topology: the stacker lines (T06) and reclaimer
lines (T11) run BETWEEN/alongside the stockpile banks they serve, with the
machines inside their working corridor — it does not provide reliable metric
coordinates. Each element therefore distinguishes:

    PHYSICAL TOPOLOGY CONFIRMED  ·  METRIC POSITION SCHEMATIC/TBC

Rules enforced here:
  - exact positions only where a source supports them (none currently);
  - display-only assets are marked per geometry type — `MachineGeo.display
    =True`, `PileGeo.modelled=False`, `ConveyorGeo.planning_enabled=False`
    (note: `MachineGeo` has NO `planning_enabled` field; that flag lives on
    the domain `Machine`) — and are NEVER bound to solver links or
    inventory, nor present in `Scenario.machines`/`Scenario.spaces`;
  - no 102MG -> C1 connection exists (102MG is PRE-beneficiation mine
    storage; the C1 feed comes from beneficiation-product storage);
  - machine reachability comes from configured `reachable_spaces`, never
    from visual distance — `validate_geometry` checks rail/pile adjacency;
  - future-expansion items render only behind a toggle and are never solver
    inventory.

M16 physical tag mapping (user layout evidence, 15-Jul-2026):
    ST01-A = M16A-A-ST01 · ST01-B = M16A-B-ST01 · ST01-C = M16A-C-ST01
    RC01-A = M16A-A-RC01 · RC01-B = M16A-B-RC01
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .labels import SITE_PREFIX, site_tag  # noqa: F401  (shared label module)

XY = Tuple[float, float]

#: solver id -> physical tag. Defined once in the planning layer (PFD-0001[3]
#: equipment list) and re-exported here so the mimic and its tests keep a
#: single source of the mapping.
from mph_sim.datasets import (M16_BAYS, M16_FUTURE_BAYS,  # noqa: E402
                             M16_FUTURE_LINES, M16_MACHINE_LINES,
                             M16_PHYSICAL_TAGS, M16_RECLAIM_STREAMS,
                             WASHING_LINE_RESIDENCE_TIME_H,
                             m16_reachable_spaces, m16_stream_of)
from mph_sim.flow_config import destination_line_operating_state  # noqa: E402

TOPO_CONF = "PHYSICAL TOPOLOGY CONFIRMED · METRIC POSITION SCHEMATIC/TBC"
TOPO_TBC = "TOPOLOGY & POSITION TBC (schematic)"

# Include this revision in the Streamlit Plotly element key.  Geometry is
# display configuration rather than solver state, so a source correction can
# otherwise leave an already-mounted browser chart showing the previous
# annotations even though a rerun constructed the updated figure.
GEOMETRY_REVISION = "2026-07-29-louta-l01-v21"


# ---- geometry primitives ---------------------------------------------------------
@dataclass(frozen=True)
class DisplayGradeComponent:
    """One display-only grade component within a reference stockpile.

    ``frac_from``/``frac_to`` are mass-proportional schematic band bounds,
    not surveyed longitudinal coordinates.  The component never becomes
    solver inventory.
    """
    component_id: str
    frac_from: float
    frac_to: float
    mass_fraction: float
    grade_id: str
    grade_label: str
    quality: Dict[str, float] = field(default_factory=dict)
    grade_name: str = ""


@dataclass
class PileGeo:
    pile_id: str                  # scenario StockSpace id, or display pseudo-id
    x0: float
    y0: float
    x1: float
    y1: float
    modelled: bool = True         # False -> display-only, never a solver space
    label: str = ""
    tbc: bool = False
    future: bool = False          # behind the Future-expansion toggle only
    capacity_t: Optional[float] = None  # known/display capacity; not live fill
    display_mass_t: Optional[float] = None  # static t0 display mass; no solver
    display_note: str = ""       # display-only inventory/quality caveat
    display_grade_id: str = ""   # proxy/reference grade; never solver inventory
    display_grade_label: str = ""
    display_quality: Dict[str, float] = field(default_factory=dict)
    display_grade_basis: str = ""
    display_grade_components: List[DisplayGradeComponent] = field(
        default_factory=list)
    display_grade_issues: List[str] = field(default_factory=list)
    # A multi-band lane draws one tonnage label per grade band, so the
    # whole-pile total is optional per site display ruling: Benguerir keeps the
    # bare pile id (Rev S, tonnage in hover); the Louta lanes also show total
    # mass and fill % on the canvas (project instruction 25-Jul-2026).
    label_with_total: bool = False
    # Renderer-only footprint multiplier.  Capacity and inventory remain in
    # the scenario; this only lets a physically larger pad remain visibly
    # larger after the SVG normalises schematic source coordinates.
    visual_width_factor: float = 1.0
    # Optional extra hover lines for a MODELLED pile (capacity/evidence/served
    # machines/repère convention). Opt-in so lanes that already have a settled
    # hover contract keep it unchanged.
    hover_note: str = ""
    # Optional operator-facing name when the stable internal key is not a
    # physical stockpile tag. Louta's legacy `102MG-SP-*` keys are retained
    # for scenario/config compatibility, but the L01 process description uses
    # those tags for samplers, so the canvas says MINE STOCKPILE-01/-02.
    canvas_label: str = ""


@dataclass
class RailGeo:
    """Horizontal machine line inside its working corridor."""
    rail_id: str
    x0: float
    y: float
    x1: float
    kind: str = "stacker"                    # machine type on this line
    flanks: List[str] = field(default_factory=list)   # served pile bank(s)
    working_sides: str = "both"              # above | below | both
    label: str = ""
    tbc: bool = False
    parking_frac: float = 0.04               # idle/maintenance park position
    travel: Tuple[float, float] = (0.0, 1.0)  # travel limits (frac of rail)
    source: str = "user-supplied M16 stockyard layout (15-Jul-2026)"
    confidence: str = TOPO_CONF
    label_pos: str = "left"                  # left (outside) | above (inside)
    # How many stockpile lanes deep the served band is on one side of the
    # line. 1 = a single lane of hangars (the default). Greater values remain
    # available for genuine multi-lane banks; M16 uses 1 because each bay is
    # now drawn as one side-by-side horizontal pair.
    flank_depth: int = 1


@dataclass
class MachineGeo:
    machine_id: str               # scenario Machine id, or display tag
    rail_id: str
    parking_frac: float = 0.04
    scale: float = 1.0
    display: bool = False         # True -> not a solver machine (dashed, TBC)
    kind: str = "stacker"         # symbol for display-only machines
    label: str = ""               # site-qualified display label
    physical_tag: str = ""        # e.g. M16A-A-ST01 (user layout evidence)
    note: str = ""
    label_side: int = 0           # -1 left · 0 below · +1 right (declutter)
    twin_boom: bool = False       # ONE twin-/double-boom machine (e.g. B02
    #                             # "Twice boom stacker ST3**") — never two
    # ---- package vs planning scope (20-Jul-2026, user-supplied Table 20) -----
    # A source package may mark equipment "out of scope" for ITS handling
    # contract while the job-allocation application's planning ownership of
    # that same equipment remains a SEPARATE, still-open question. Never
    # infer one from the other.
    package_scope: str = ""       # e.g. "OUT_OF_SCOPE" per the source table
    planning_scope: str = ""      # e.g. "TBC" — application ownership unknown
    # ---- visual pile association ---------------------------------------------
    # `served_pile` (single) states which drawn stockpile a DISPLAY machine
    # works on — applied display geometry, never inferred from visual
    # distance — and `home_repere` is the reference/home position used when no
    # job position exists.  `served_piles` is the multi-pile form for machines
    # that translate between several bays (M16A-*-T06/T11 lines); it documents
    # the served set for hover/tests and never widens solver reachability,
    # which stays in `Machine.reachable_spaces`.  Both forms coexist: use
    # `served()` to read whichever was configured.
    served_pile: str = ""
    served_piles: List[str] = field(default_factory=list)
    home_repere: int = 500
    future: bool = False          # explicit future lifecycle; never infer from display

    def served(self) -> List[str]:
        """Configured served piles, single or multi form (never inferred)."""
        if self.served_piles:
            return list(self.served_piles)
        return [self.served_pile] if self.served_pile else []


@dataclass
class ConveyorGeo:
    cid: str
    pts: List[XY]
    link_ids: List[str] = field(default_factory=list)
    planning_enabled: bool = True     # False -> display-only conveyor
    tag: str = ""
    sub: str = ""
    tbc: bool = False
    label_frac: float = 0.5
    label_dy: float = 0.9
    compact: bool = False
    machine_id: str = ""              # per-machine route (e.g. 140LG-CV-03/RL1):
    #                                 # state/flow derive from that machine's jobs
    future: bool = False               # explicit future lifecycle only

    def __post_init__(self):
        if not self.link_ids and not self.machine_id:
            self.planning_enabled = False


@dataclass
class EquipGeo:
    kind: str        # tower|gate|diverter|sampler|scale|analyser|junction|
    #                # corridor_in|corridor_out|destbox|hopper
    x: float
    y: float
    label: str = ""
    link_ids: List[str] = field(default_factory=list)
    tbc: bool = False
    scale: float = 1.0
    w: float = 0.0   # destbox width/height
    h: float = 0.0


@dataclass
class ProcessLineGeo:
    """Visual process-line card, separate from mobile machine geometry.

    ``status`` is a static configuration/lifecycle label used by the mimic,
    not a live PLC or equipment-running state.  A card may represent a
    solver-modelled process-transfer stage (as Louta L1/L2 now do), but the
    plant line itself is not falsely represented as a mobile ``Machine``.
    """
    line_id: str
    x0: float
    y0: float
    x1: float
    y1: float
    label: str
    status: str                    # OPERATING | FUTURE
    source: str = ""
    evidence_status: str = "USER_DIRECTED"
    note: str = ""
    residence_minutes: Optional[float] = None
    treatment_time: str = ""
    treatment_time_source: str = ""


@dataclass
class ZoneGeo:
    label: str
    x0: float
    y0: float
    x1: float
    y1: float
    style: str = "scope"          # scope (SMS) | stage (process step)
    tbc: bool = False


@dataclass
class ChipGeo:
    tag: str
    x: float
    y: float
    note: str = "function/connectivity TBC"
    kind: str = "gap"      # gap (amber — genuine engineering gap/conflict)
    #                      # | info (grey — source fact/rating, no warning)


@dataclass
class StampGeo:
    x: float
    y: float
    text: str = "SCHEMATIC — NOT TO SCALE"


@dataclass
class SiteGeometry:
    site_id: str                  # LOUTA | BENGUERIR | MPH | CHAIN | GLOBAL
    title: str
    bounds: Tuple[float, float, float, float]
    not_to_scale: bool
    source: str
    piles: List[PileGeo] = field(default_factory=list)
    rails: List[RailGeo] = field(default_factory=list)
    machines: List[MachineGeo] = field(default_factory=list)
    conveyors: List[ConveyorGeo] = field(default_factory=list)
    equipment: List[EquipGeo] = field(default_factory=list)
    process_lines: List[ProcessLineGeo] = field(default_factory=list)
    zones: List[ZoneGeo] = field(default_factory=list)
    chips: List[ChipGeo] = field(default_factory=list)
    stamps: List[StampGeo] = field(default_factory=list)
    height_px: int = 560
    gaps: List[Tuple[str, str, str]] = field(default_factory=list)

    def pile(self, pid: str) -> Optional[PileGeo]:
        return next((p for p in self.piles if p.pile_id == pid), None)

    def rail(self, rid: str) -> Optional[RailGeo]:
        return next((r for r in self.rails if r.rail_id == rid), None)


_L02 = ("user-reviewed L02 handling philosophy + user equipment table "
        "(14-Jul-2026) — document refs TBC")
_CROP = "cropped Louta layout drawing (no title block)"
_LOU_L01_PD = (
    "Q80130-LOU-L01-PR-REP-0002[3].pdf, PDF p.11 (file/revision table: "
    "Rev 3, 15-Sep-2025; cover field still says Revision No. 2)"
)
_LOU_L01_PSR = (
    "Q80130-LOU-L01-PR-PSR-0001[A].pdf, PDF p.10 (two stackers, one "
    "dedicated to each stockyard; older 1,058 t/h rate)"
)
_M16LAY = "user-supplied M16 stockyard layout (15-Jul-2026)"
#: Benguerir authoritative source (18-Jul-2026 correction pass)
_B02 = ("Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, "
        "§§2.3.1–2.3.3 and §2.6")
_B01 = "Q80130-BEN-B01-PR-REP-0002 Rev B, 26-Jul-2023 (IFR)"
_BEN_MINE_LAYOUT_SOURCE = (
    "Project-team Benguerir six-stockpile layout instruction and supplied "
    "plan crop, 28-Jul-2026; B01 pp.5–8 confirms TM1 + TSM2/TSM3 mine "
    "reception lines and semi-mobile/RoM hoppers. Physical positions and "
    "unshown equipment tags remain schematic/TBC."
)
# The project-team UI instruction uses numeric aliases and asks for this
# lifecycle baseline.  Available design evidence calls the beneficiation
# trains A/B/C and does not, by itself, prove commissioning status; retain
# that reconciliation explicitly in the hover/evidence text.
_BEN_WASH_LINE_SOURCE = (
    "Project-team Benguerir layout/UI instructions, 22/28-Jul-2026; numeric "
    "Line 1/2/3 aliases correspond provisionally to design Trains A/B/C. "
    "All three are configured OPERATING for the planning scenario; as-built "
    "and commissioning evidence remains to be reconciled."
)
_BEN_WASH_TREATMENT_TIME = (
    f"line residence "
    f"{WASHING_LINE_RESIDENCE_TIME_H['BENGUERIR'] * 60:g} min (ASSUMED) · "
    "documented scrubber step 3 min"
)
_BEN_WASH_TREATMENT_SOURCE = (
    "30 min: project-team assumption, 26-Jul-2026; scrubber: "
    "Q80130-BEN-B02-PR-BOD-0001 Rev 0, §7.1 p.19 and §7.3.1 p.25"
)
_BEN_WASH_LINE_BASELINE = (
    ("BG-WASH-L1", "LINE 1", "Train A", "OPERATING"),
    ("BG-WASH-L2", "LINE 2", "Train B", "OPERATING"),
    ("BG-WASH-L3", "LINE 3", "Train C", "OPERATING"),
)
#: user-supplied Table 20 — screened-product ore-storage topology + machine
#: reachability USER_CONFIRMED by the project team, 20-Jul-2026; the source
#: document itself carries no confirmed number/title-block — never invent one
_T20 = ("User-supplied Table 20 — Screened Product Storage Area; Benguerir "
        "ore-storage attribution and reachability confirmed by project "
        "team, 20-Jul-2026; source document number/revision TBC.")
_T20_OWNERSHIP = ("Physical topology confirmed; planning status follows "
                  "the loaded scenario; live SMS integration remains TBC.")
#: Table 20 rates humid — identical for both stackers and the shared
#: reclaimer. Stated on every machine so no view attributes a rate by
#: proximity instead of by evidence.
_T20_RATE = "2,100 nominal / 2,500 design t/h, humid."
_T20_DISPLAY = ("The six-pile built-in binds this geometry to live scheduler "
                "assets; older snapshots may leave it reference-only.")
_T20_ZONE = ("SCREENED-PRODUCT ORE STORAGE — PRE-BENEFICIATION · 2 × "
            "200,000 t (400,000 t total live capacity) · <15 mm · open "
            "longitudinal · chevron stacking · 12 d nominal / 14 d design "
            "autonomy — source basis · humid-tonne rates")
_T20_CONFLICT = ("Table 20 (screened-ore storage): reclaimer 2,100 nominal "
                 "/ 2,500 design t/h, humid — marked 'Out of scope' in the "
                 "source package. B02 (expansion interface, §2.3.2): raw "
                 "bucket-wheel reclaimer/RB interface 2,600 nominal / 3,120 "
                 "design t/h. Whether B02 upgrades, replaces or "
                 "supplements the Table 20 reclaimer is TBC — no equipment "
                 "list/PFD proves equivalence; values kept separately "
                 "traceable.")


def _benguerir_washing_lines(ox: float, oy: float,
                              compact: bool) -> List[ProcessLineGeo]:
    """Three parallel washing-line cards requested for the Benguerir mimic.

    The full/detail view nests them in the WASHING process node and keeps the
    downstream FILTRATION and SETTLING nodes separate.  The compact view uses
    the same lifecycle data in a narrower stacked block.  Coordinates remain
    schematic and the collection is display-only by type.
    """
    if compact:
        x0, x1 = ox + 25.0, ox + 37.5
        bands = ((oy + 19.20, oy + 20.10),
                 (oy + 17.85, oy + 18.75),
                 (oy + 16.50, oy + 17.40))
    else:
        x0, x1 = ox + 11.0, ox + 23.0
        bands = ((oy + 29.35, oy + 30.25),
                 (oy + 28.00, oy + 28.90),
                 (oy + 26.65, oy + 27.55))

    result: List[ProcessLineGeo] = []
    for (line_id, label, source_alias, status), (y0, y1) in zip(
            _BEN_WASH_LINE_BASELINE, bands):
        result.append(ProcessLineGeo(
            line_id, x0, y0, x1, y1, label, status,
            source=_BEN_WASH_LINE_SOURCE,
            evidence_status="USER_DIRECTED / DOCUMENT_RECONCILIATION_TBC",
            note=(f"{label} is the project-team UI alias for {source_alias}. "
                  f"Static configuration status: {status}; this is not live "
                  "equipment telemetry. Display/reference only: absent from "
                  "Scenario machines, spaces, nodes and links. "
                  f"{_BEN_WASH_LINE_SOURCE}"),
            residence_minutes=(
                WASHING_LINE_RESIDENCE_TIME_H["BENGUERIR"] * 60),
            treatment_time=_BEN_WASH_TREATMENT_TIME,
            treatment_time_source=_BEN_WASH_TREATMENT_SOURCE))
    return result


# ==================================== M16 ==========================================
#: M16 humid-yard flow sheet — the authoritative source for the machine and
#: conveyor identities and for the ALTERNATING machine-line / hangar-bay
#: sequence. It is a flow sheet: metric positions are NOT to scale.
_M16PFD = ("Q80130-MZI-M16-ME-PFD-0001[3] Rev 3, IFU, 28-Oct-2025 — "
           "Phosphate Storage Area flow sheet")
_M16PRO = "Q80130-MZI-M16-PR-PRO-0001 Rev 2, 22-May-2025, §2.1"
_M16_TOPO = ("MACHINE LINE / SIDE-BY-SIDE BAY SEQUENCE CONFIRMED "
             "(PFD-0001[3]) · METRIC POSITION SCHEMATIC — flow sheet, "
             "not to scale")
_M16_BAY_RULE = (
    "Project-team physical rule (25-Jul-2026): a stacker/reclaimer that "
    "translates along its machine line between adjacent hangar bays serves "
    "those adjacent bays — and only those. No machine reaches every hangar.")
_M16_PAIR_LAYOUT = (
    "Project-team display ruling (26-Jul-2026): the two hangars in each bay "
    "are drawn side-by-side on one horizontal row, with every serving "
    "machine line spanning both hangars. This makes shared access visible "
    "without claiming surveyed dimensions.")
_M16_HP_INFERRED = (
    "Hangar numbering inside each bay is USER-DIRECTED / INFERRED: the PFD "
    "shows eight 125,000 t hangars in four two-hangar bays but does not name "
    "them. Pending the plot plan and the official SMS pile-ID register.")


def _m16_section(sc, ox: float = 0.0, oy: float = 0.0,
                 compact: bool = False,
                 show_future: bool = False) -> SiteGeometry:
    """M16 receiving hub — FOUR side-by-side two-hangar bays with the
    machine lines interleaved between them, as PFD-0001[3] draws the yard:

        M16A-A-T06 (ST01-A) · bay 1 · M16A-A-T11 (RC01-A) · bay 2 ·
        M16A-B-T06 (ST01-B) · bay 3 · M16A-B-T11 (RC01-B) · bay 4 ·
        M16A-C-T06 (ST01-C)
        [future: bay 5 · M16A-C-T11 (RC01-C) · bay 6 · M16A-D-T06 (ST01-D)]

    There is NO shared `T06`/`T11` corridor and no detached machine corridor:
    every machine sits on its own canonical line, flanked only by the bays it
    can physically serve (`Machine.reachable_spaces` carries the same matrix).

    Downstream, the A and B reclaim streams stay separate through their own
    T11/T11S/T12 conveyors and their own clamshell/rod gate bank
    (`M16A-A-H01…H04` / `M16A-B-H01…H04`); the PAP/drying confluence is TBC.
    """
    # ---- vertical grid -----------------------------------------------------
    # The future band continues the same sequence BELOW the current yard and
    # only exists behind the toggle, so it consumes no space when hidden.
    future_h = 26.0 if (show_future and not compact) else 0.0
    cur = oy + future_h
    if compact:
        px0, px1 = ox + 13.0, ox + 42.0
        rail_x0, rail_x1 = ox + 11.5, ox + 43.0
        lane_h, reserved_lane_gap, pair_gap, bay_gap = 1.6, 0.8, 1.2, 1.6
        ms = 0.55
        y_top = oy + 43.0
        width = 66.0
    else:
        px0, px1 = ox + 28.0, ox + 104.0
        rail_x0, rail_x1 = ox + 26.5, ox + 105.5
        lane_h, reserved_lane_gap, pair_gap, bay_gap = 2.2, 1.2, 3.0, 2.5
        ms = 1.0
        y_top = cur + 44.0
        width = 142.0
    g = SiteGeometry(
        "MPH", "MPH M16 receiving & stockyard SMS",
        bounds=(ox, oy, ox + width, cur + 48.0), not_to_scale=True,
        source=f"{_M16PFD} + {_M16PRO} + MPH FRS")

    # ---- rails + bays, alternating from the top ----------------------------
    # sequence: line, bay, line, bay, line, bay, line, bay, line
    lines = list(M16_MACHINE_LINES.items())          # ordered A-T06 … C-T06
    # A bay is one horizontal row containing two side-by-side hangars. Keep
    # the previous two-lane vertical reservation so all canonical machine-line
    # y positions, corridor routes and downstream equipment remain stable;
    # centre the new horizontal pair inside that reserved band.
    pile_w = (px1 - px0 - pair_gap) / 2.0
    bay_span = 2 * lane_h + reserved_lane_gap
    rail_y: Dict[str, float] = {}
    bay_box: Dict[int, Tuple[float, float]] = {}
    y = y_top
    for index, (mid, (line, bays, kind)) in enumerate(lines):
        rail_y[line] = y
        g.rails.append(RailGeo(
            line, rail_x0, y, rail_x1, kind=kind,
            flanks=m16_reachable_spaces(mid),
            working_sides=("below" if index == 0 else
                           "above" if index == len(lines) - 1 else "both"),
            label=("" if compact else
                   f"{line} · {mid} · "
                   + ("2,600 t/h stacking line" if kind == "stacker"
                      else "2,500 t/h reclaim line")
                   + " — serves "
                   + " + ".join(f"bay {b}" for b in bays)),
            source=_M16PFD, confidence=_M16_TOPO, label_pos="above",
            flank_depth=1))
        if index < len(lines) - 1:                   # a bay follows each line
            bay_no = index + 1
            top = y - bay_gap
            bay_box[bay_no] = (top - bay_span, top)
            served_here = [mid for mid in M16_MACHINE_LINES
                           if bay_no in M16_MACHINE_LINES[mid][1]]
            for lane, pid in enumerate(M16_BAYS[bay_no - 1][1]):
                x0 = px0 + lane * (pile_w + pair_gap)
                y0 = top - (bay_span + lane_h) / 2.0
                g.piles.append(PileGeo(
                    pid, x0, y0, x0 + pile_w, y0 + lane_h,
                    label=site_tag("MPH", pid),
                    hover_note=(
                        f"capacity 125,000 t (CONFIRMED, {_M16PFD})<br>"
                        f"bay {bay_no} · served by "
                        + " + ".join(
                            f"{m} ({M16_MACHINE_LINES[m][0]})"
                            for m in served_here)
                        + "<br>repères R0–R1000 · reclaimed on the "
                        + m16_stream_of(pid)
                        + " stream<br>hangar name INFERRED (bay topology "
                          "CONFIRMED)")))
            y = bay_box[bay_no][0] - bay_gap
    for bay_no, (y0, y1) in bay_box.items():
        served = [mid for mid in M16_MACHINE_LINES
                  if bay_no in M16_MACHINE_LINES[mid][1]]
        g.zones.append(ZoneGeo(
            "", px0 - 0.8, y0 - 0.5, px1 + 0.8, y1 + 0.5, style="stage"))
        if not compact:                              # identity chip sits
            # between the preceding machine line and the horizontal pair
            g.chips.append(ChipGeo(
                f"BAY {bay_no} · {' + '.join(served)}",
                (px0 + px1) / 2, y1 - 0.7,
                note=f"bay {bay_no} = "
                     f"{' / '.join(M16_BAYS[bay_no - 1][1])} · reachable by "
                     f"{', '.join(served)}. {_M16_PAIR_LAYOUT} "
                     f"{_M16_BAY_RULE} "
                     f"{_M16_HP_INFERRED}", kind="info"))

    # ---- machines: one per line, parked clear of each other -----------------
    park = {"ST01-A": 0.15, "RC01-A": 0.30, "ST01-B": 0.50,
            "RC01-B": 0.70, "ST01-C": 0.85}
    for mid, (line, bays, kind) in M16_MACHINE_LINES.items():
        reach = m16_reachable_spaces(mid)
        g.machines.append(MachineGeo(
            mid, line, park[mid], ms, label=site_tag("MPH", mid),
            physical_tag=M16_PHYSICAL_TAGS[mid],
            served_piles=list(reach), label_side=-1 if compact else 0,
            note=f"{M16_PHYSICAL_TAGS[mid]} on machine line {line} "
                 + ("(2,600 t/h stacker)" if kind == "stacker"
                    else "(2,500 t/h bucket-wheel reclaimer)")
                 + " — identity, line and relative position CONFIRMED by the "
                   "PFD. Serves "
                 + " + ".join(f"bay {b} ({'/'.join(M16_BAYS[b - 1][1])})"
                              for b in bays)
                 + f". {_M16_BAY_RULE} {_M16_HP_INFERRED}"))

    # ---- reception: corridors -> tower -> the three stacking lines ----------
    ymid = (rail_y["M16A-A-T11"] + rail_y["M16A-B-T11"]) / 2
    tower_x = ox + (7.5 if compact else 12.0)
    spine_x = ox + (9.8 if compact else 21.0)
    corr_x = ox + (3.0 if compact else 4.0)
    has_corridors = any(l.id == "C1" for l in sc.links)
    if has_corridors:
        g.equipment += [
            EquipGeo("corridor_in", corr_x, rail_y["M16A-A-T11"],
                     "C1 (Louta)", link_ids=["C1"], scale=0.8 if compact else 1.0),
            EquipGeo("corridor_in", corr_x, rail_y["M16A-B-T11"],
                     "C2 (Benguerir)", link_ids=["C2"],
                     scale=0.8 if compact else 1.0),
        ]
        g.conveyors += [
            ConveyorGeo("C1-tail", [(corr_x + 1.2, rail_y["M16A-A-T11"]),
                                    (tower_x - 0.8, ymid + 0.8)],
                        link_ids=["C1"], tag="", compact=True),
            ConveyorGeo("C2-tail", [(corr_x + 1.2, rail_y["M16A-B-T11"]),
                                    (tower_x - 0.8, ymid - 0.8)],
                        link_ids=["C2"], tag="", compact=True),
        ]
    else:      # M16-only scenario (S2): arrivals are SIMULATED inputs
        g.equipment.append(EquipGeo("corridor_in", corr_x, ymid,
                                    "SIM." if compact else "SIMULATED",
                                    link_ids=["L-CORR-IN"],
                                    scale=0.8 if compact else 1.0))
        g.conveyors.append(ConveyorGeo(
            "SIM-IN", [(corr_x + 1.2, ymid), (tower_x - 0.8, ymid)],
            link_ids=["L-CORR-IN"], tbc=True, compact=True))
    g.equipment.append(EquipGeo("tower", tower_x, ymid,
                                "M16 reception tower (TT01)",
                                scale=0.8 if compact else 1.0))
    if not compact:
        g.chips.append(ChipGeo(
            "CURRENT reception: ONE shared 2,600 t/h path (M16A-A-T01)",
            tower_x + 2.0, ymid - 6.5,
            note="PRO-0001 §2.1.1 places auto sampler M16VP01 on M16A-A-T01 "
                 "and M16VP02 on 'the adjacent FUTURE conveyor', and "
                 "PFD-0001[3] marks that line FUTURE. C1 and C2 therefore "
                 "SHARE one current reception envelope — modelled as a "
                 "shared-capacity group on every stacking leg, so aggregate "
                 "reception never reaches 2 × 2,600 t/h.", kind="info"))
    if not compact:
        # TEN-0002 + PFD auto-sampler M16VP01 / analysers M16AE301/302 on the
        # reception belt; exact positions TBC
        g.equipment += [
            EquipGeo("scale", tower_x + 2.4, ymid, "belt scale M16A-A-T02W",
                     tbc=True),
            EquipGeo("sampler", tower_x + 4.0, ymid,
                     "auto sampler M16VP01", tbc=True),
            EquipGeo("analyser", tower_x + 5.6, ymid,
                     "analysers M16AE301/302", tbc=True),
        ]
    g.conveyors.append(ConveyorGeo(
        "T01/T02", [(tower_x + 0.8, ymid), (spine_x, ymid)],
        link_ids=[f"L-STK-{p}" for p in
                  [pile for _, piles in M16_BAYS for pile in piles]],
        tag="T01/T02" if compact else "T01/T02 · 2,600 t/h",
        label_frac=0.5, label_dy=-1.5, compact=compact))
    stacker_lines = [(mid, line) for mid, (line, _, k) in
                     M16_MACHINE_LINES.items() if k == "stacker"]
    for mid, line in stacker_lines:
        # real routed leg: along the T02/T03/T04 diverter chain, then into the
        # stacking line — the bend is where the diverter gate sits
        pts = [(spine_x, ymid), (spine_x, rail_y[line]),
               (rail_x0, rail_y[line])]
        pts = [pt for i, pt in enumerate(pts) if i == 0 or pt != pts[i - 1]]
        g.conveyors.append(ConveyorGeo(
            f"{line}-feed", pts,
            link_ids=[f"L-STK-{p}" for p in m16_reachable_spaces(mid)],
            tag="" if compact else line.replace("M16A-", "").replace("-T06",
                                                                     " feed"),
            sub="", label_frac=0.85, label_dy=1.2, compact=compact))
        g.equipment.append(EquipGeo("diverter", spine_x, rail_y[line], "",
                                    scale=0.7 if compact else 0.8))

    if not compact:
        g.chips.append(ChipGeo(
            "T02H / T03H / T04H diverter gates → stacker line",
            spine_x + 5.5, rail_y["M16A-A-T06"] + 1.6,
            note="PFD-0001[3]: the reception belts T01/T02 feed T03/T04 and "
                 "the diverter gates select which stacking line receives the "
                 "arrival. Which stacker may then use the leg is enforced by "
                 "machine reachability, not by the conveyor.", kind="info"))

    # ---- reclaim: two separate streams, each with its own gate bank ---------
    gate_x = ox + (48.0 if compact else 113.0)
    conf_x = ox + (53.0 if compact else 124.0)
    dest_x = ox + (60.0 if compact else 134.0)
    conf_w, conf_h = (8.0, 3.0) if compact else (10.0, 4.0)
    dest_w, dest_h = (10.0, 4.0) if compact else (12.0, 5.0)
    y_conf = ymid
    for stream in ("A", "B"):
        line = f"M16A-{stream}-T11"
        ry = rail_y[line]
        stream_piles = [p for p in M16_RECLAIM_STREAMS[stream]]
        rcl_links = [f"L-RCL-{p}" for p in stream_piles]
        bank_link = f"L-H-{stream}"          # CONFIRMED discharge equipment
        route_links = [f"L-RTE-{stream}-PAP", f"L-RTE-{stream}-DRY"]
        up = ry > y_conf
        g.conveyors.append(ConveyorGeo(
            f"M16A-{stream}-T12",
            [(rail_x1, ry), (gate_x - (3.5 if compact else 6.0), ry)],
            link_ids=rcl_links,
            tag="" if compact else f"{stream}-T12",
            sub="" if compact else "2,500 t/h",
            label_frac=0.5, label_dy=-1.3 if ry > ymid else 1.3,
            compact=compact))
        # clamshell + rod gate bank: 4 gates in the detail view, one grouped
        # symbol in the compact composite views
        if compact:
            g.equipment.append(EquipGeo(
                "gate", gate_x, ry - (2.0 if up else -2.0),
                f"{stream}-H01…H04", link_ids=[bank_link], scale=0.8))
        else:
            for k in range(4):
                g.equipment.append(EquipGeo(
                    "gate", gate_x + 1.9 * k,
                    ry - (2.8 if up else -2.8),
                    f"H{k + 1:02d}", link_ids=[bank_link], scale=0.85))
            g.chips.append(ChipGeo(
                f"M16A-{stream}-H01…H04 · clamshell + rod gates",
                gate_x + 8.5, ry + (1.5 if up else -1.5),
                note="CONFIRMED discharge equipment (PFD-0001[3]): T12 → "
                     f"hopper M16A-A-R01 with air blasters → this bank → "
                     f"M16A-{stream}-T13 (1,500 t/h). Neither the PFD nor "
                     "PRO-0001 assigns individual H numbers to PAP or "
                     "drying, so the PAP-versus-drying choice is a SEPARATE "
                     "downstream route/diverter abstraction, drawn dashed "
                     "amber and classified DERIVED/TBC.",
                kind="info"))
        g.conveyors.append(ConveyorGeo(
            f"M16A-{stream}-T13",
            [(gate_x + (1.2 if compact else 5.7),
              ry - (4.0 if up else -4.0)),
             (conf_x - conf_w / 2 - 2.0, y_conf + (0.9 if up else -0.9)),
             (conf_x - conf_w / 2, y_conf + (0.9 if up else -0.9))],
            link_ids=[bank_link],
            tag="" if compact else f"{stream}-T13 · 1,500 t/h",
            sub="", label_frac=0.3, label_dy=-1.2 if up else 1.2,
            compact=compact))
    pap2_link_ids = ["L-RTE-A-PAP2", "L-RTE-B-PAP2"]
    pap2_state = destination_line_operating_state(sc, "PAP-2")
    pap2_available = pap2_state == "AVAILABLE"
    pap2_y = y_conf + (12.0 if compact else 16.5)

    for stream in ("A", "B"):
        ry = rail_y[f"M16A-{stream}-T11"]
        g.equipment.append(EquipGeo(
            "diverter", conf_x - conf_w / 2 - 2.0,
            y_conf + (0.9 if ry > y_conf else -0.9),
            "" if compact else f"{stream} route selection",
            link_ids=[
                f"L-RTE-{stream}-PAP",
                f"L-RTE-{stream}-PAP2",
                f"L-RTE-{stream}-DRY",
            ],
            tbc=True, scale=0.7 if compact else 0.85))
    g.equipment += [
        EquipGeo("destbox", conf_x, y_conf,
                 "PAP / DRYING ROUTE CONFLUENCE — TBC" if not compact
                 else "PAP / DRYING — TBC", w=conf_w, h=conf_h, tbc=True),
        EquipGeo("destbox", dest_x, y_conf + (7.0 if compact else 10.0),
                 "PAP-1" if compact else "PAP-1 (wet process)",
                 w=dest_w, h=dest_h),
        EquipGeo(
            "destbox", dest_x, pap2_y,
            "PAP-2" if compact else "PAP-2 (wet process)",
            link_ids=pap2_link_ids, w=dest_w, h=dest_h,
        ),
        EquipGeo("destbox", dest_x, y_conf - (7.0 if compact else 10.0),
                 "DRY-FEED" if compact else "DRY-FEED → drying → SPH",
                 w=dest_w, h=dest_h),
    ]
    g.conveyors += [
        ConveyorGeo("H-PAP", [(conf_x + conf_w / 2, y_conf + 0.9),
                              (dest_x - dest_w / 2,
                               y_conf + (7.0 if compact else 10.0) - dest_h / 3)],
                    link_ids=["L-RTE-A-PAP", "L-RTE-B-PAP"], tbc=True,
                    tag="" if compact else "→ PAP-1",
                    sub="", label_frac=0.45, label_dy=1.2, compact=compact),
        ConveyorGeo(
            "H-PAP2",
            [
                (conf_x + conf_w / 2, y_conf + 0.9),
                (dest_x - dest_w / 2, pap2_y - dest_h / 3),
            ],
            link_ids=pap2_link_ids, tbc=True, future=not pap2_available,
            tag="" if compact else "→ PAP-2",
            sub="", label_frac=0.55, label_dy=1.2, compact=compact,
        ),
        ConveyorGeo("H-DRY", [(conf_x + conf_w / 2, y_conf - 0.9),
                              (dest_x - dest_w / 2,
                               y_conf - (7.0 if compact else 10.0) + dest_h / 3)],
                    link_ids=["L-RTE-A-DRY", "L-RTE-B-DRY"], tbc=True,
                    tag="" if compact else "→ drying",
                    sub="", label_frac=0.45, label_dy=-1.2, compact=compact),
    ]
    # Keep the destination body short enough to fit the compact global view.
    # Lifecycle remains explicit in its own always-visible label immediately
    # above the box, where AVAILABLE/FUTURE cannot collide with the line name.
    g.chips.append(ChipGeo(
        pap2_state, dest_x, pap2_y + dest_h / 2 + 0.9,
        note=f"PAP-2 receiving line configuration: {pap2_state}.",
        kind="info",
    ))

    if not compact:
        g.chips.append(ChipGeo(
            "ROUTE SELECTION DERIVED/TBC · PAP 6 × 1,500 t/h "
            "(3 running) · drying 2 × 1,299 t/h OUT OF SCOPE",
            conf_x + 2.0, y_conf - conf_h / 2 - 3.4,
            note="Conveyor counts and ratings are CONFIRMED (PRO-0001 §2.1); "
                 "the destination ASSIGNMENT is not. Neither source says "
                 "which gate or diverter sends a stream to PAP rather than "
                 "drying, and the confluence point is TBC — so the "
                 "switchyard, its destination route legs and the per-bank "
                 "mutual exclusion are drawn dashed amber as DERIVED "
                 "abstractions, never as confirmed equipment. The documented "
                 "10-minute changeover is NOT modelled."))

    # ---- future extension: same sequence, behind the toggle -----------------
    if show_future and not compact:
        # bays first, then the lines, so a future line can flank BOTH of the
        # bays it would translate between (same adjacency rule as the current
        # yard). The whole band exists only while the toggle is on.
        f_bay_piles = {}          # bay number -> lane ids
        f_bay_rows = {}           # bay number -> (bottom lane y0, top y)
        fy = rail_y["M16A-C-T06"] - bay_gap
        for f_index, (bay_label, bay_piles) in enumerate(M16_FUTURE_BAYS):
            bay_no = 5 + f_index
            top = fy
            lane_ys = []
            for lane, pid in enumerate(bay_piles):
                x0 = px0 + lane * (pile_w + pair_gap)
                y0 = top - (bay_span + lane_h) / 2.0
                lane_ys.append(y0)
                g.piles.append(PileGeo(
                    pid, x0, y0, x0 + pile_w, y0 + lane_h,
                    modelled=False, tbc=True,
                    future=True, capacity_t=125_000,
                    label=f"{pid} · FUTURE",
                    display_note="future hangar — space allocation only "
                                 "(PRO-0001 §2.1: 4 × 125,000 t future). The "
                                 "identifier is INFERRED and display-only: "
                                 "the PFD names neither the current nor the "
                                 "future hangars. Never solver inventory."))
            bay_bottom = top - bay_span
            g.zones.append(ZoneGeo("", px0 - 0.8, bay_bottom - 0.5,
                                   px1 + 0.8, top + 0.5, style="stage",
                                   tbc=True))
            g.chips.append(ChipGeo(
                f"{bay_label.replace('BAY-', 'BAY ')} · FUTURE "
                f"({' / '.join(bay_piles)})",
                (px0 + px1) / 2, top - 0.7,
                note="future hangar bay — PFD-0001[3] FUTURE EXTENSION and "
                     "PRO-0001 §2.1 (space allocation for 4 × 125,000 t). "
                     "HP-09*…HP-12* are INFERRED display-only identifiers "
                     "(the PFD names no hangar); not planning spaces and not "
                     f"in Scenario.spaces. {_M16_PAIR_LAYOUT}", kind="info"))
            f_bay_piles[bay_no] = list(bay_piles)
            f_bay_rows[bay_no] = (bay_bottom, top)
            fy = bay_bottom - 2 * bay_gap         # room for the next line
        # the adjacency rule applies to the CURRENT edge line too: with the
        # future bays present, M16A-C-T06 translates between bay 4 and bay 5
        c_t06 = g.rail("M16A-C-T06")
        c_t06.flanks = list(c_t06.flanks) + list(f_bay_piles[5])
        c_t06.working_sides = "both"
        c_t06.label = (c_t06.label.replace("serves bay 4",
                                           "serves bay 4 + future bay 5")
                       if c_t06.label else c_t06.label)
        for mg in g.machines:
            if mg.machine_id == "ST01-C":
                mg.served_piles = list(mg.served_piles) + list(f_bay_piles[5])
                mg.note += (" With Future expansion the same adjacency rule "
                            "extends it to future bay 5 (display only — "
                            "future hangars are never solver spaces, so "
                            "Machine.reachable_spaces stays HP-04/HP-08).")
        for f_mid, (f_line, f_bays, f_kind) in M16_FUTURE_LINES.items():
            served = [pid for b in f_bays for pid in f_bay_piles.get(b, [])]
            first_bay = min(f_bays)
            f_rail_y = f_bay_rows[first_bay][0] - bay_gap
            g.rails.append(RailGeo(
                f_line, rail_x0, f_rail_y, rail_x1, kind=f_kind,
                flanks=served,
                working_sides="both" if len(f_bays) > 1 else "above",
                tbc=True, label=f"{f_line} · {f_mid} · FUTURE",
                source=_M16PFD, confidence=_M16_TOPO, label_pos="above",
                flank_depth=1))
            g.machines.append(MachineGeo(
                f_mid, f_line, 0.5, ms, display=True, kind=f_kind,
                label=site_tag("MPH", f_mid),
                physical_tag=("M16A-C-RC01" if f_kind == "reclaimer"
                              else "M16A-D-ST01"),
                served_piles=served, future=True,
                note=("FUTURE equipment on the continuing PFD sequence "
                      f"({f_line}), serving "
                      + " + ".join(f"bay {b}" for b in f_bays)
                      + ". Display/reference only: absent from "
                        "Scenario.machines — no solver jobs, no live state. "
                      + _M16_BAY_RULE)))
        g.stamps.append(StampGeo(
            ox + 42, rail_y["M16A-C-T06"] - 24.5,
            "FUTURE EXTENSION — 4 × 125,000 t hangars + M16A-C-RC01 + "
            "M16A-D-ST01 · never solver resources"))

    if not compact:
        g.stamps.append(StampGeo(ox + 62, cur + 46.6, _M16_TOPO))
    g.gaps += [
        ("MPH", "M16 hangar numbering inside the bays", _M16_HP_INFERRED),
        ("MPH", "M16 metric geometry",
         "machine-line / bay sequence CONFIRMED by " + _M16PFD
         + f"; {_M16_PAIR_LAYOUT} The sheet is a flow diagram — surveyed "
           "metric coordinates, "
           "rail travel limits and hangar dimensions remain TBC (no plot "
           "plan)"),
        ("MPH", "M16 machine reach",
         _M16_BAY_RULE + " Applied as ST01-A→bay 1 · RC01-A→bays 1-2 · "
         "ST01-B→bays 2-3 · RC01-B→bays 3-4 · ST01-C→bay 4. A reach/GA "
         "matrix confirming the travel limits per line is still required"),
        ("MPH", "PAP / drying route confluence",
         "the A and B reclaim streams stay separate through their own "
         "T11/T11S/T12 conveyors, gate bank (M16A-A/B-H01…H04) and 1,500 t/h "
         "T13 belt; neither the PFD nor PRO-0001 assigns individual H numbers "
         "to PAP or drying, and the downstream confluence point is TBC"),
        ("MPH", "reclaimed-material quality",
         "MVP reclaims at CURRENT PILE AVERAGE quality — active reclaim-face/"
         "segment consumption not yet modelled (limitation, ASSUMPTIONS §D)"),
    ]
    g.height_px = 820 if (show_future and not compact) else 660
    return g


def m16_geometry(sc, show_future: bool = False) -> SiteGeometry:
    return _m16_section(sc, 0, 0, compact=False, show_future=show_future)


# ======================= shared display-only composition ==========================
#: Compact band labels for the three shared raw BT ore profiles.
_RAW_BT_COMPONENT_LABELS = {
    "RAW-BT-GLOBAL": "GLOBAL",
    "RAW-BT-HIGH-CD": "HIGH-CD",
    "RAW-BT-LOW-CD": "LOW-CD",
}


def _cfg_pile_float(sc, key: str, pile_id: str) -> Optional[float]:
    """Per-pile display number from `Scenario.config`, or None."""
    cfg = getattr(sc, "config", {}) or {}
    values = cfg.get(key, {})
    if not isinstance(values, dict):
        return None
    value = values.get(pile_id)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _display_component_quality(grade_obj, *,
                               keep_moisture: bool) -> Dict[str, float]:
    quality = dict(getattr(grade_obj, "quality", {}) or {})
    if not keep_moisture:
        # Benguerir B02 already gives a site-specific raw moisture range
        # (10–14 %). Moisture is a site/material-profile property, not part of
        # the shared grade identity. On Louta ore piles, the source-backed
        # 15 % value is retained.
        quality.pop("moisture", None)
    return quality


def _parse_display_components(sc, pile_id: str, rows, *, keep_moisture: bool):
    """Validate one reference pile's display component rows.

    Returns ``(components, issues)``. Invalid inputs are REPORTED, never
    repaired or normalised: any issue yields an empty component list so the
    caller keeps its documented fallback and `validate_geometry` surfaces the
    data problem.
    """
    if not isinstance(rows, list):
        return [], [f"{pile_id}: grade components must be a list"]
    parsed = []
    issues: List[str] = []
    seen_ids = set()
    total_fraction = 0.0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            issues.append(f"{pile_id}: component {index} must be a mapping")
            continue
        component_id = str(row.get("component_id", f"C{index}") or f"C{index}")
        if component_id in seen_ids:
            issues.append(f"{pile_id}: duplicate component id {component_id}")
        seen_ids.add(component_id)
        component_grade_id = str(row.get("grade_id", "") or "")
        component_grade = sc.grade(component_grade_id) \
            if component_grade_id and hasattr(sc, "grade") else None
        if component_grade is None:
            issues.append(
                f"{pile_id}/{component_id}: unknown grade {component_grade_id!r}")
            continue
        try:
            mass_fraction = float(row.get("mass_fraction", 0.0))
        except (TypeError, ValueError):
            mass_fraction = -1.0
        if not (0.0 < mass_fraction <= 1.0):
            issues.append(
                f"{pile_id}/{component_id}: mass_fraction must be in (0, 1]")
            continue
        parsed.append((
            component_id, mass_fraction, component_grade_id,
            _RAW_BT_COMPONENT_LABELS.get(component_grade_id,
                                         component_grade_id),
            str(getattr(component_grade, "name", component_grade_id)
                or component_grade_id),
            _display_component_quality(component_grade,
                                       keep_moisture=keep_moisture),
        ))
        total_fraction += mass_fraction

    if abs(total_fraction - 1.0) > 1e-9:
        issues.append(
            f"{pile_id}: component mass fractions sum to "
            f"{total_fraction:g}, expected 1")
    if issues:
        return [], issues

    components: List[DisplayGradeComponent] = []
    cursor = 0.0
    for component_id, mass_fraction, component_grade_id, label, name, quality \
            in parsed:
        components.append(DisplayGradeComponent(
            component_id=component_id,
            frac_from=cursor,
            frac_to=cursor + mass_fraction,
            mass_fraction=mass_fraction,
            grade_id=component_grade_id,
            grade_label=label,
            quality=quality,
            grade_name=name,
        ))
        cursor += mass_fraction
    return components, []


def _composite_display_grade(components: List[DisplayGradeComponent],
                             basis: str) -> Dict[str, object]:
    """Mass-weighted composite of validated display components."""
    # A composite parameter is valid only when every positive-mass component
    # supplies it. Missing chemistry is never treated as zero.
    common_params = set(components[0].quality)
    for component in components[1:]:
        common_params.intersection_update(component.quality)
    composite_quality = {
        param: sum(
            component.mass_fraction * component.quality[param]
            for component in components)
        for param in sorted(common_params)
    }
    composition_label = " + ".join(
        f"{component.grade_label} {component.mass_fraction:.0%}"
        for component in components
    )
    return {
        "display_grade_id": "MIXED",
        "display_grade_label": f"MIX {composition_label}",
        "display_quality": composite_quality,
        "display_grade_basis": basis,
        "display_grade_components": components,
        "display_grade_issues": [],
    }


# ================================== BENGUERIR ======================================
def _bg_raw_display_mass(sc, pile_id: str) -> Optional[float]:
    """Legacy static t0 mass fallback for snapshots without bound raw spaces."""
    return _cfg_pile_float(sc, "BG_RAW_DISPLAY_START_MASS_T", pile_id)


def _bg_raw_display_grade(sc, pile_id: str) -> Dict[str, object]:
    """Legacy grade composition for an unbound Benguerir raw-pile drawing.

    Multi-grade components are mass-weighted and drawn as schematic bands.
    If component data is missing or invalid, the legacy single-grade mapping
    remains a visible fallback and validation carries the data issue. Current
    six-pile scenarios bind these drawings to StockSpace state and clear this
    overlay.
    """
    cfg = getattr(sc, "config", {}) or {}
    mapping = cfg.get("BG_RAW_DISPLAY_GRADE_IDS", {})
    if not isinstance(mapping, dict):
        return {}
    grade_id = str(mapping.get(pile_id, "") or "")
    if not grade_id:
        return {}
    grade = sc.grade(grade_id) if hasattr(sc, "grade") else None
    short_labels = {
        "RAW-BT-GLOBAL": "BT-GLOBAL",
        "RAW-BT-HIGH-CD": "BT-HIGH-CD",
        "RAW-BT-LOW-CD": "BT-LOW-CD",
    }
    fallback = {
        "display_grade_id": grade_id,
        "display_grade_label": short_labels.get(
            grade_id, getattr(grade, "name", grade_id)),
        "display_quality": _display_component_quality(grade,
                                                      keep_moisture=False),
        "display_grade_basis": str(
            cfg.get("BG_RAW_DISPLAY_GRADE_BASIS", "") or ""),
    }
    component_cfg = cfg.get("BG_RAW_DISPLAY_GRADE_COMPONENTS", {})
    if not isinstance(component_cfg, dict):
        fallback["display_grade_issues"] = [
            "BG_RAW_DISPLAY_GRADE_COMPONENTS must be a mapping"]
        return fallback
    rows = component_cfg.get(pile_id, [])
    if not rows:
        return fallback
    components, issues = _parse_display_components(sc, pile_id, rows,
                                                   keep_moisture=False)
    if issues:
        fallback["display_grade_issues"] = issues
        return fallback
    grade_basis = str(cfg.get("BG_RAW_DISPLAY_GRADE_BASIS", "") or "")
    composition_basis = str(
        cfg.get("BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS", "") or "")
    return _composite_display_grade(
        components,
        "; ".join(value for value in (grade_basis, composition_basis)
                  if value))


def _benguerir_base_geometry(sc, ox: float = 0.0, oy: float = 0.0,
                              compact: bool = False) -> SiteGeometry:
    """Benguerir BBE — INTEGRATED staged view per the B02 mechanical BoD
    (Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, §§2.3.1–2.3.3 +
    §2.6): RAW/SCREENED MINE STORAGE → WASHING/FILTRATION/SETTLING → NEW
    WASHED-PRODUCT STORAGE → C2 corridor / train. The earlier B01 2023
    screening evidence is the upstream stage of the SAME process chain —
    process stages connected; exact phase/tag/PFD reconciliation TBC.

    Washed yard: two 191,512 t lanes, ONE twin-boom stacker ST3** (SL5/SL6),
    reclaimers RC2**/RC3** on SEPARATE schematic reclaim lines (BoD schematic
    suggests separate lines; reachability & metric positions TBC — no GA).
    Per project-team instruction, the B02 table is the current Benguerir
    configuration. The ** marks remain visible only as source notation."""
    _cfg = getattr(sc, "config", {}) or {}
    b02_on = bool(_cfg.get("BEN_B02_OPTION_ENABLED", True))
    raw_c2_on = bool(_cfg.get("BEN_RAW_TO_C2_ENABLED", False))
    g = SiteGeometry(
        "BENGUERIR", "BENGUERIR BBE — staged mine + beneficiation SMS",
        bounds=(ox, oy, ox + (84 if compact else 124),
                oy + (22 if compact else 58.5)), not_to_scale=True,
        source=_B02 + " — geometry schematic, no GA")

    if compact:
        # ---------------- compact staged band (chain/global views) -----------
        g.zones.append(ZoneGeo("BENGUERIR SMS — BBE staged (B02)",
                               ox + 1, oy + 0.3, ox + 82, oy + 21.6))
        # Name the stage the same way the detail view does — a compact band
        # reading only "ORE STORAGE" does not say WHICH stage it is, and the
        # 400,000 t is the WHOLE yard, never a per-pile figure.
        g.zones.append(ZoneGeo(
            "SCREENED-PRODUCT ORE · 2 × 200,000 t · 400,000 t total",
            ox + 1.5, oy + 15.0, ox + 21.5, oy + 21.2,
            style="stage"))
        g.piles += [
            # Visible IDs in CHAIN/GLOBAL: an unlabelled lane reads as an
            # anonymous box. The 200,000 t capacity allocation is a direct
            # project-team instruction (22-Jul-2026). Starting/live mass and
            # the explicit shared raw-grade composition are stored separately in
            # scenario config and remain outside the solver.
            PileGeo("BG-RAW-1", ox + 3, oy + 18.2, ox + 20, oy + 19.4,
                    modelled=False, tbc=True,
                    label=site_tag("BENGUERIR", "BG-RAW-1")
                    + " · DISPLAY/REF", capacity_t=200_000,
                    display_mass_t=_bg_raw_display_mass(sc, "BG-RAW-1"),
                    display_note="ASSUMED composition · not live assay",
                    **_bg_raw_display_grade(sc, "BG-RAW-1")),
            PileGeo("BG-RAW-2", ox + 3, oy + 15.8, ox + 20, oy + 17.0,
                    modelled=False, tbc=True,
                    label=site_tag("BENGUERIR", "BG-RAW-2")
                    + " · DISPLAY/REF", capacity_t=200_000,
                    display_mass_t=_bg_raw_display_mass(sc, "BG-RAW-2"),
                    display_note="ASSUMED composition · not live assay",
                    **_bg_raw_display_grade(sc, "BG-RAW-2")),
        ]
        g.rails += [
            RailGeo("BG-RAW-ST1-R", ox + 1.8, oy + 19.7, ox + 21.2,
                    kind="stacker", flanks=["BG-RAW-1"],
                    working_sides="below", label="", tbc=True,
                    source=_T20, confidence=TOPO_CONF),
            RailGeo("BG-RAW-R", ox + 1.8, oy + 17.6, ox + 21.2,
                    kind="reclaimer", flanks=["BG-RAW-1", "BG-RAW-2"],
                    working_sides="both", label="", tbc=True,
                    source=_T20, confidence=TOPO_CONF),
            RailGeo("BG-RAW-ST2-R", ox + 1.8, oy + 15.4, ox + 21.2,
                    kind="stacker", flanks=["BG-RAW-2"],
                    working_sides="above", label="", tbc=True,
                    source=_T20, confidence=TOPO_CONF),
        ]
        g.machines += [
            # Compact notes carry the SAME rates and scope distinction as the
            # detail view — a reader hovering in CHAIN/GLOBAL must not have to
            # open the site view to learn the machine's rating or its status.
            MachineGeo("BG-RAW-ST-1", "BG-RAW-ST1-R", 0.82, 0.4,
                       display=True, kind="stacker", label_side=-1,
                       label="BEN · Raw ST-1",
                       package_scope="TBC", planning_scope="TBC",
                       note=f"{_T20} Physical tag TBC. Reachability USER_CONFIRMED: "
                            f"BG-RAW-1 only — dedicated stacker, never "
                            f"BG-RAW-2. Rate: {_T20_RATE} "
                            f"{_T20_OWNERSHIP} {_T20_DISPLAY}"),
            MachineGeo("BG-RAW-BWR", "BG-RAW-R", 0.82, 0.4,
                       display=True, kind="reclaimer", label_side=-1,
                       label="BEN · Raw RC (shared)",
                       package_scope="OUT_OF_SCOPE", planning_scope="TBC",
                       note=f"{_T20} Physical tag TBC. Reachability USER_CONFIRMED: reaches "
                            f"BOTH BG-RAW-1 and BG-RAW-2 — ONE shared "
                            f"machine, never a second reclaimer. Table 20 "
                            f"rate: {_T20_RATE} Marked 'Out of scope' in "
                            f"the source package (package_scope); that does "
                            f"not prevent the current six-pile scenario from "
                            f"scheduling it. "
                            f"{_T20_CONFLICT} {_T20_OWNERSHIP} "
                            f"{_T20_DISPLAY}"),
            MachineGeo("BG-RAW-ST-2", "BG-RAW-ST2-R", 0.82, 0.4,
                       display=True, kind="stacker", label_side=-1,
                       label="BEN · Raw ST-2",
                       package_scope="TBC", planning_scope="TBC",
                       note=f"{_T20} Physical tag TBC. Reachability USER_CONFIRMED: "
                            f"BG-RAW-2 only — dedicated stacker, never "
                            f"BG-RAW-1. Rate: {_T20_RATE} "
                            f"{_T20_OWNERSHIP} {_T20_DISPLAY}"),
        ]
        g.zones.append(ZoneGeo("WASHING · 3 LINES → filtration / settling",
                               ox + 24, oy + 15.0, ox + 38.5, oy + 21.2,
                               style="stage", tbc=True))
        g.process_lines += _benguerir_washing_lines(ox, oy, compact=True)
        g.conveyors += [
            ConveyorGeo("BG-RAW-WASH", [(ox + 21.5, oy + 17.8),
                                        (ox + 24, oy + 17.8)],
                        tbc=True, compact=True),
            ConveyorGeo("BG-WASH-YARD", [(ox + 31, oy + 15.0),
                                         (ox + 31, oy + 13.4),
                                         (ox + 26, oy + 12.2)],
                        tbc=True, compact=True),
        ]
        # washed-product yard: 2 lanes · twin-boom ST3 between · RC2/RC3 on
        # separate schematic lines
        g.piles += [
            PileGeo("BG-P1", ox + 22, oy + 9.6, ox + 58, oy + 12.6,
                    label=site_tag("BENGUERIR", "BG-P1")),
            PileGeo("BG-P2", ox + 22, oy + 2.6, ox + 58, oy + 5.6,
                    label=site_tag("BENGUERIR", "BG-P2")),
        ]
        g.rails += [
            RailGeo("BG-SL", ox + 20.5, oy + 7.6, ox + 59.5, kind="stacker",
                    flanks=["BG-P1", "BG-P2"], working_sides="both",
                    label="", tbc=True, source=_B02, confidence=TOPO_TBC),
            RailGeo("BG-RL-P1", ox + 20.5, oy + 13.8, ox + 59.5,
                    kind="reclaimer", flanks=["BG-P1"],
                    working_sides="below", label="", tbc=True,
                    source=_B02, confidence=TOPO_TBC),
            RailGeo("BG-RL-P2", ox + 20.5, oy + 1.3, ox + 59.5,
                    kind="reclaimer", flanks=["BG-P2"],
                    working_sides="above", label="", tbc=True,
                    source=_B02, confidence=TOPO_TBC),
        ]
        g.machines += [
            MachineGeo("ST3", "BG-SL", 0.45, 0.5, twin_boom=True,
                       label="BEN · ST3", physical_tag="ST3",
                       label_side=1,
                       note="twin-boom stacker ST3** — CURRENT (B02 table; "
                            "'**' source notation only); plans recovered "
                            "output from all three washing campaigns"),
            MachineGeo("RC2", "BG-RL-P1", 0.30, 0.5, label="BEN · RC2",
                       physical_tag="RC2", label_side=-1,
                       note="bucket-wheel reclaimer RC2** — CURRENT (B02 "
                            "table; '**' source notation only)"),
            MachineGeo("RC3", "BG-RL-P2", 0.62, 0.5, label="BEN · RC3",
                       physical_tag="RC3", label_side=1,
                       note="bucket-wheel reclaimer RC3** — CURRENT (B02 "
                            "table; '**' source notation only)"),
        ]
        g.conveyors += [
            ConveyorGeo("BG-RL7", [(ox + 59.5, oy + 13.8),
                                   (ox + 62.5, oy + 9.0)],
                        machine_id="RC2", compact=True),
            ConveyorGeo("BG-RL8", [(ox + 59.5, oy + 1.3),
                                   (ox + 62.5, oy + 7.6)],
                        machine_id="RC3", compact=True),
            ConveyorGeo("BG-PROD-C2", [(ox + 63, oy + 8.4),
                                       (ox + 66.5, oy + 14.5),
                                       (ox + 76, oy + 14.5)],
                        link_ids=["L-BG-PROD-C2"], tag="", compact=True),
            ConveyorGeo("BG-TRAIN-P", [(ox + 63, oy + 8.4),
                                       (ox + 67, oy + 5.0),
                                       (ox + 70.5, oy + 5.0)],
                        link_ids=["L-BG-TRAIN-PROD"], tag="", compact=True),
        ]
        g.equipment += [
            EquipGeo("junction", ox + 63, oy + 8.4, "RL12/RL13"),
            EquipGeo("corridor_out", ox + 78.5, oy + 14.5, "C2 → M16",
                     link_ids=["C2"], scale=0.8),
            EquipGeo("destbox", ox + 74.5, oy + 4.8, "BG-TRAIN", w=7, h=2.6),
        ]
        g.chips += [
            ChipGeo("raw→C2 CONDITIONAL — " + ("ENABLED (assumes B02 "
                                               "interface selected)"
                                               if raw_c2_on else "disabled"),
                    ox + 70, oy + 18.6,
                    note="B02 §2.6: raw ore to corridor requires phase-1 "
                         "250 m³ hopper modification and/or RB9/RB10; the "
                         "2,600 t/h rate assumes the Table 20 single-"
                         "reclaimer bottleneck (2,100 nominal) has been "
                         "resolved by the B02 expansion interface"),
            ChipGeo("raw→train 1,000/1,200 t/h (ref)", ox + 70, oy + 16.6,
                    note="documented interface — no modelled raw inventory"),
            ChipGeo("Table 20 vs B02 reclaimer conflict — TBC", ox + 66,
                    oy + 13.4, note=_T20_CONFLICT),
        ]
        g.stamps.append(StampGeo(ox + 14, oy + 0.9))
        g.gaps = _bg_gaps(b02_on)
        g.height_px = 400
        return g

    # ---------------- DETAIL view: integrated staged plan --------------------
    # Three readable stage bands, top to bottom along the material flow:
    #   1. SCREENED-PRODUCT ORE STORAGE   (Table 20 — display/reference)
    #   2. WASHING / FILTRATION / SETTLING (process interface, not solver)
    #   3. NEW WASHED-PRODUCT STORAGE      (B02 — current configuration)
    # Evidence detail (tags TBC, metric positions, the Table 20 vs B02
    # reclaimer-rating conflict) lives in machine notes, chips and the data-
    # gaps panel — the drawing itself stays uncluttered.

    # ---- stage 1: raw/screened ore storage (top band) ----------------------
    # USER-CONFIRMED topology (project team, 20-Jul-2026, Table 20):
    # 2 piles · 2 DEDICATED stackers (one per pile) · 1 SHARED reclaimer
    # reaching both piles. Tags and metric positions TBC (evidence panel).
    g.zones.append(ZoneGeo(
        "SCREENED-PRODUCT ORE STORAGE — PRE-BENEFICIATION · 2 stockpiles "
        "× 200,000 t · 400,000 t total live capacity · <15 mm · "
        "2 dedicated stackers "
        "+ 1 shared reclaimer (Table 20)",
        ox + 1, oy + 34.0, ox + 122.5, oy + 56.8))
    g.piles += [
        PileGeo("BG-RAW-1", ox + 8, oy + 51.2, ox + 62, oy + 54.2,
                modelled=False, tbc=True, label="RAW PILE 1",
                capacity_t=200_000,
                display_mass_t=_bg_raw_display_mass(sc, "BG-RAW-1"),
                display_note="ASSUMED composition · not live assay",
                **_bg_raw_display_grade(sc, "BG-RAW-1")),
        PileGeo("BG-RAW-2", ox + 8, oy + 43.0, ox + 62, oy + 46.0,
                modelled=False, tbc=True, label="RAW PILE 2",
                capacity_t=200_000,
                display_mass_t=_bg_raw_display_mass(sc, "BG-RAW-2"),
                display_note="ASSUMED composition · not live assay",
                **_bg_raw_display_grade(sc, "BG-RAW-2")),
    ]
    g.rails += [
        RailGeo("BG-RAW-ST1-R", ox + 6.5, oy + 55.15, ox + 63.5,
                kind="stacker", flanks=["BG-RAW-1"], working_sides="below",
                label="Raw stacker 1 line — dedicated to pile 1",
                tbc=True, source=_T20, confidence=TOPO_CONF,
                label_pos="above"),
        RailGeo("BG-RAW-R", ox + 6.5, oy + 48.6, ox + 63.5,
                kind="reclaimer", flanks=["BG-RAW-1", "BG-RAW-2"],
                working_sides="both",
                label="Shared raw reclaim line — reaches both piles",
                tbc=True, source=_T20, confidence=TOPO_CONF,
                label_pos="above"),
        RailGeo("BG-RAW-ST2-R", ox + 6.5, oy + 40.4, ox + 63.5,
                kind="stacker", flanks=["BG-RAW-2"], working_sides="above",
                label="Raw stacker 2 line — dedicated to pile 2",
                tbc=True, source=_T20, confidence=TOPO_CONF,
                label_pos="above"),
    ]
    g.machines += [
        MachineGeo("BG-RAW-ST-1", "BG-RAW-ST1-R", 0.28, 0.6, display=True,
                   kind="stacker", label="BEN · Raw stacker 1",
                   label_side=1, package_scope="TBC", planning_scope="TBC",
                   note=f"{_T20} Physical tag TBC. Reachability "
                        f"USER_CONFIRMED: BG-RAW-1 only — dedicated "
                        f"stacker, never BG-RAW-2. Rate: {_T20_RATE} "
                        f"{_T20_OWNERSHIP} {_T20_DISPLAY}"),
        MachineGeo("BG-RAW-BWR", "BG-RAW-R", 0.52, 0.6, display=True,
                   kind="reclaimer", label="BEN · Shared raw reclaimer",
                   label_side=1, package_scope="OUT_OF_SCOPE",
                   planning_scope="TBC",
                   note=f"{_T20} Physical tag TBC. Reachability "
                        f"USER_CONFIRMED: reaches BOTH BG-RAW-1 and "
                        f"BG-RAW-2 — ONE shared machine, never a second "
                        f"reclaimer. Table 20 rate: 2,100 nominal / 2,500 "
                        f"design t/h, humid; marked 'Out of scope' in the "
                        f"source package. {_T20_CONFLICT} "
                        f"{_T20_OWNERSHIP} {_T20_DISPLAY}"),
        MachineGeo("BG-RAW-ST-2", "BG-RAW-ST2-R", 0.74, 0.6, display=True,
                   kind="stacker", label="BEN · Raw stacker 2",
                   label_side=1, package_scope="TBC", planning_scope="TBC",
                   note=f"{_T20} Physical tag TBC. Reachability "
                        f"USER_CONFIRMED: BG-RAW-2 only — dedicated "
                        f"stacker, never BG-RAW-1. Rate: {_T20_RATE} "
                        f"{_T20_OWNERSHIP} {_T20_DISPLAY}"),
    ]
    # evidence chips: grey = source facts, amber = genuine gaps/conflicts;
    # all placed clear of the raw->C2 (y~53) and raw->train (y~44.5) routes
    g.chips += [
        ChipGeo("Table 20 — screened-product storage · source document "
                "number/revision TBC", ox + 93, oy + 55.6, note=_T20,
                kind="info"),
        ChipGeo("RB5–RB8 raw reclaim conveyors · 2,600/3,120 t/h",
                ox + 77.5, oy + 50.9,
                note="raw reclaim CONVEYORS downstream of the reclaimer "
                     "(B02 §2.3.2) — conveyor rating, not the Table 20 "
                     "reclaimer's own rating", kind="info"),
        ChipGeo("⚠ Table 20 vs B02 raw-reclaimer rating — unresolved",
                ox + 77.5, oy + 49.3, note=_T20_CONFLICT),
        ChipGeo("RB9/RB10 — conditional raw→C2 prerequisite",
                ox + 77.5, oy + 47.8,
                note="phase-1 250 m³ hopper modification and/or conveyors "
                     "RB9/RB10 are the documented prerequisite of the "
                     "conditional raw→C2 route (B02 §2.6; the source's "
                     "'**' mark is notation only)"),
        ChipGeo("B01 2023: 1 stacker + 1 reclaimer 04BG-RC-01 — "
                "reconciliation TBC", ox + 77.5, oy + 46.3,
                note=_B01 + " — process stages connected (B02 §2.6); exact "
                     "04BG-* ↔ RB/SL/RL-* mapping unproven; tag registers "
                     "NOT merged; Table 20 machine count/rates NOT assumed "
                     "identical to B01's"),
        ChipGeo("open longitudinal · chevron stacking · 12 d nominal / "
                "14 d design autonomy (as printed)",
                ox + 77.5, oy + 39.7,
                note="Table 20 physical descriptors — autonomy figures "
                     "kept verbatim, not reinterpreted or assigned "
                     "per pile", kind="info"),
        ChipGeo("raw: 10–14 % H₂O · 1.2–1.4 t/m³ · 30–35° · abrasive",
                ox + 77.5, oy + 38.2,
                note="B02 §2.3.1 material properties", kind="info"),
    ]
    # ---- stage 2: washing / filtration / settling ----------------------------
    # Process interface between the two yards — transformation boundary.
    # Each line is an executable process stage with configured campaign feed,
    # recovery and downstream ST3 dependency.
    g.zones.append(ZoneGeo(
        "WASHING / FILTRATION / SETTLING (3 WASHING LINES) — process "
        "interface · PLANNED CAMPAIGNS",
        ox + 8, oy + 25.7, ox + 66, oy + 31.7, style="stage", tbc=True))
    g.process_lines += _benguerir_washing_lines(ox, oy, compact=False)
    g.equipment += [
        EquipGeo("destbox", ox + 34, oy + 28.2, "FILTRATION", w=12, h=3.2),
        EquipGeo("destbox", ox + 51, oy + 28.2, "SETTLING", w=12, h=3.2),
    ]
    g.conveyors += [
        ConveyorGeo("BG-WASH-1", [(ox + 23.2, oy + 28.2),
                                  (ox + 27.8, oy + 28.2)],
                    tbc=True, compact=True),
        ConveyorGeo("BG-WASH-2", [(ox + 40.2, oy + 28.2),
                                  (ox + 44.8, oy + 28.2)],
                    tbc=True, compact=True),
        # raw yard -> washing, fed by the shared raw reclaimer (routed on
        # the left, clear of the piles and outlet routes)
        ConveyorGeo("BG-RAW-WASH", [(ox + 6.5, oy + 48.6),
                                    (ox + 4.2, oy + 48.6),
                                    (ox + 4.2, oy + 28.2),
                                    (ox + 11, oy + 28.2)],
                    tag="raw → washing", sub="via shared raw reclaimer",
                    tbc=True, label_frac=0.55, label_dy=0.0, compact=True),
        # washing -> washed yard: recovered output is stacked by ST3
        ConveyorGeo("BG-WASH-YARD", [(ox + 57, oy + 28.2),
                                     (ox + 64.5, oy + 28.2),
                                     (ox + 64.5, oy + 21.6)],
                    tag="washing output → ST3 → yard",
                    sub="three planned campaigns · recovery assumed",
                    tbc=True, label_frac=0.8, label_dy=0.0),
    ]
    # ---- stage 3: washed-product yard (B02 §2.3.3) ---------------------------
    g.zones.append(ZoneGeo(
        "NEW WASHED-PRODUCT STORAGE — 2 × 191,512 t · combined-yard "
        "autonomy ≈6.1 d nominal / ≈5.1 d design (BoD: 5/6 d)",
        ox + 1, oy + 0.6, ox + 96, oy + 22.6))
    g.piles += [
        PileGeo("BG-P1", ox + 10, oy + 15.0, ox + 70, oy + 18.4,
                label=site_tag("BENGUERIR", "BG-P1") + " · 191,512 t"),
        PileGeo("BG-P2", ox + 10, oy + 5.6, ox + 70, oy + 9.0,
                label=site_tag("BENGUERIR", "BG-P2") + " · 191,512 t"),
    ]
    g.rails += [
        RailGeo("BG-SL", ox + 8.5, oy + 12.0, ox + 71.5, kind="stacker",
                flanks=["BG-P1", "BG-P2"], working_sides="both",
                label="ST3 twin-boom stacking line (SL5/SL6) · "
                      "2,100/2,520 t/h",
                tbc=True, source=_B02, confidence=TOPO_TBC,
                label_pos="above"),
        RailGeo("BG-RL-P1", ox + 8.5, oy + 20.0, ox + 71.5, kind="reclaimer",
                flanks=["BG-P1"], working_sides="below",
                label="RC2 reclaim line · 2,600/3,120 t/h",
                tbc=True, source=_B02,
                confidence=TOPO_TBC, label_pos="above"),
        RailGeo("BG-RL-P2", ox + 8.5, oy + 3.4, ox + 71.5, kind="reclaimer",
                flanks=["BG-P2"], working_sides="above",
                label="RC3 reclaim line · 2,600/3,120 t/h",
                tbc=True, source=_B02,
                confidence=TOPO_TBC, label_pos="above"),
    ]
    g.machines += [
        MachineGeo("ST3", "BG-SL", 0.45, 0.85, twin_boom=True,
                   label=site_tag("BENGUERIR", "ST3"), physical_tag="ST3",
                   label_side=1,
                   note="Twin-boom stacker ST3 — CURRENT configuration "
                        "(B02 table; the source's '**' mark is notation "
                        "only) · 2,100 nominal / 2,520 design t/h · MVP "
                        "plans recovered output from the three washing "
                        "campaigns; line-to-pile mapping is ASSUMED"),
        MachineGeo("RC2", "BG-RL-P1", 0.30, 0.85,
                   label=site_tag("BENGUERIR", "RC2"), physical_tag="RC2",
                   label_side=-1,
                   note="Bucket-wheel reclaimer RC2 — CURRENT "
                        "configuration (B02 table; the source's '**' mark "
                        "is notation only) · 2,600 nominal / 3,120 design "
                        "t/h · modelled reclaim jobs · reach/metric "
                        "positions TBC (no GA)"),
        MachineGeo("RC3", "BG-RL-P2", 0.62, 0.85,
                   label=site_tag("BENGUERIR", "RC3"), physical_tag="RC3",
                   label_side=1,
                   note="Bucket-wheel reclaimer RC3 — CURRENT "
                        "configuration (B02 table; the source's '**' mark "
                        "is notation only) · 2,600 nominal / 3,120 design "
                        "t/h · modelled reclaim jobs · reach/metric "
                        "positions TBC (no GA)"),
    ]
    # ---- product reclaim collection → C2 / train -----------------------------
    g.conveyors += [
        ConveyorGeo("BG-RL7", [(ox + 71.5, oy + 20.0), (ox + 76, oy + 20.0),
                               (ox + 80, oy + 13.2)],
                    machine_id="RC2", tag="RL7 → RL12",
                    label_frac=0.35, label_dy=1.3),
        ConveyorGeo("BG-RL8", [(ox + 71.5, oy + 3.4), (ox + 76, oy + 3.4),
                               (ox + 80, oy + 11.2)],
                    machine_id="RC3", tag="RL8 → RL13",
                    label_frac=0.35, label_dy=-1.3),
        ConveyorGeo("BG-PROD-C2", [(ox + 80.5, oy + 12.2),
                                   (ox + 85, oy + 14.8),
                                   (ox + 90.4, oy + 14.8)],
                    link_ids=["L-BG-PROD-C2"],
                    tag="washed product → C2", label_frac=0.6, label_dy=1.4),
        ConveyorGeo("BG-C2", [(ox + 91.6, oy + 14.8), (ox + 95.2, oy + 14.8)],
                    link_ids=["C2"], tag="", compact=True),
        ConveyorGeo("BG-TRAIN-P", [(ox + 80.5, oy + 12.2),
                                   (ox + 85, oy + 6.2),
                                   (ox + 103.5, oy + 6.2)],
                    link_ids=["L-BG-TRAIN-PROD"],
                    tag="washed product → train",
                    sub="design 1,200 t/h · demand-planned route",
                    label_frac=0.62, label_dy=-1.3),
    ]
    # conditional raw → C2 + raw → train (documented interfaces, B02 §2.6)
    if raw_c2_on:
        g.conveyors.append(ConveyorGeo(
            "BG-RAW-C2", [(ox + 62, oy + 53.0), (ox + 91, oy + 53.0),
                          (ox + 91, oy + 15.4)],
            link_ids=["L-BG-RAW-C2"],
            tag="raw ore → C2 — CONDITIONAL route ENABLED",
            sub="prerequisite assumed met: hopper mod / RB9-RB10",
            label_frac=0.85, label_dy=1.2))
    else:
        g.conveyors.append(ConveyorGeo(
            "BG-RAW-C2", [(ox + 62, oy + 53.0), (ox + 91, oy + 53.0),
                          (ox + 91, oy + 15.4)],
            tag="raw ore → C2 — CONDITIONAL (disabled)",
            sub="hopper mod and/or RB9/RB10 required · "
                "BEN_RAW_TO_C2_ENABLED=false",
            tbc=True, label_frac=0.85, label_dy=1.2))
    g.conveyors.append(ConveyorGeo(
        "BG-TRAIN-R", [(ox + 62, oy + 44.5), (ox + 104, oy + 44.5),
                       (ox + 104, oy + 7.4), (ox + 103.5, oy + 7.4)],
        link_ids=["L-BG-TRAIN-RAW"],
        tag="raw ore → train", sub="design 1,200 t/h · planning scope TBC",
        label_frac=0.85, label_dy=-1.2))
    g.equipment += [
        EquipGeo("junction", ox + 80.5, oy + 12.2, "RL12/RL13"),
        EquipGeo("tower", ox + 91, oy + 14.8, "C2 head — ONE corridor",
                 scale=0.8),
        EquipGeo("corridor_out", ox + 97.5, oy + 14.8, "C2 → M16",
                 link_ids=["C2"], scale=0.8),
        EquipGeo("destbox", ox + 110.5, oy + 6.6, "BG-TRAIN station",
                 w=13, h=4.2),
    ]
    g.chips += [
        ChipGeo("ONE loading station ASSUMED — simultaneous raw+product "
                "use TBC", ox + 110, oy + 3.2,
                note="B02 §2.6 — two documented train interfaces, station "
                     "count not stated; shared-capacity group configured"),
        ChipGeo("washed (filtration): 23 % H₂O · settled product: 15–20 % "
                "H₂O · 1.2–1.4 t/m³ · 35° · abrasive — grade-vector 12 % "
                "H₂O is a GENERIC placeholder", ox + 46, oy + 1.5,
                note="B02 §2.3.1 — Benguerir-specific material states; "
                     "shared TBT/BT/MT vectors unchanged", kind="info"),
    ]
    g.stamps += [
        StampGeo(ox + 14, oy + 1.5),
        StampGeo(ox + 98, oy + 21.5,
                 ("B02 CURRENT configuration · ST3/RC2/RC3 · '**' is "
                  "source notation only") if b02_on else
                 ("B02 current configuration DISABLED only for this "
                  "non-baseline sensitivity · reference only")),
    ]
    g.gaps = _bg_gaps(b02_on)
    g.height_px = 800
    return g


def _shift_geometry_x(g: SiteGeometry, dx: float) -> None:
    """Translate one already-built schematic horizontally, in place."""
    for p in g.piles:
        p.x0 += dx
        p.x1 += dx
    for r in g.rails:
        r.x0 += dx
        r.x1 += dx
    for c in g.conveyors:
        c.pts = [(x + dx, y) for x, y in c.pts]
    for e in g.equipment:
        e.x += dx
    for line in g.process_lines:
        line.x0 += dx
        line.x1 += dx
    for z in g.zones:
        z.x0 += dx
        z.x1 += dx
    for chip in g.chips:
        chip.x += dx
    for stamp in g.stamps:
        stamp.x += dx


def _bind_benguerir_scheduler_assets(sc, g: SiteGeometry) -> None:
    """Promote stable Benguerir geometry IDs when the scenario models them.

    Older saved scenarios may still carry the raw yard as reference-only.
    New built-ins contain the same physical IDs as scheduler resources.  The
    geometry must bind without duplicating the piles or machines.
    """
    space_ids = {space.id for space in getattr(sc, "spaces", [])}
    machine_ids = {machine.id for machine in getattr(sc, "machines", [])}
    for p in g.piles:
        if p.pile_id not in space_ids:
            continue
        p.modelled = True
        p.tbc = False
        p.label = site_tag("BENGUERIR", p.pile_id)
        # Live inventory/quality now comes from the scenario view-model.
        p.display_mass_t = None
        p.display_note = ""
        p.display_grade_id = ""
        p.display_grade_label = ""
        p.display_quality = {}
        p.display_grade_basis = ""
        p.display_grade_components = []
        p.display_grade_issues = []
    served = {
        "BG-MINE-ST-1": ["BG-MINE-1"],
        "BG-MINE-BWR": ["BG-MINE-1", "BG-MINE-2"],
        "BG-MINE-ST-2": ["BG-MINE-2"],
        "BG-RAW-ST-1": ["BG-RAW-1"],
        "BG-RAW-BWR": ["BG-RAW-1", "BG-RAW-2"],
        "BG-RAW-ST-2": ["BG-RAW-2"],
    }
    for machine in g.machines:
        if machine.machine_id not in machine_ids:
            continue
        machine.display = False
        machine.future = False
        machine.served_piles = served.get(machine.machine_id,
                                           machine.served_piles)
        machine.planning_scope = "IN_SCOPE"


def _benguerir_mine_stage(
        ox: float, oy: float, compact: bool
) -> Tuple[List[PileGeo], List[RailGeo], List[MachineGeo],
           List[EquipGeo], List[ConveyorGeo], ZoneGeo]:
    """User-confirmed upstream mine stage: two large piles and three hoppers.

    Hopper identities are source-backed (TM1/TSM2/TSM3); their displayed
    boxes intentionally carry no capacity/rate because the available B01
    evidence only gives a TBC rating for one line.
    """
    if compact:
        p1 = (ox + 3.0, oy + 18.2, ox + 22.0, oy + 19.4)
        p2 = (ox + 3.0, oy + 15.8, ox + 22.0, oy + 17.0)
        rx0, rx1 = ox + 1.8, ox + 23.2
        y_st1, y_bwr, y_st2 = oy + 19.7, oy + 17.6, oy + 15.4
        hopper_specs = (
            ("TM1", ox + 5.0, oy + 21.0,
             "L-BG-HOP-L1",
             [(ox + 5.0, oy + 20.4), (ox + 5.0, y_st1)]),
            ("TSM3", ox + 18.0, oy + 21.0,
             "L-BG-HOP-L2",
             [(ox + 18.0, oy + 20.4), (ox + 18.0, y_st1)]),
            ("TSM2", ox + 11.5, oy + 21.0,
             "L-BG-HOP-L3",
             [(ox + 11.5, oy + 20.4), (ox + 22.6, oy + 20.4),
              (ox + 22.6, y_st2)]),
        )
        machine_scale = 0.40
        zone = ZoneGeo(
            "MINE ORE · 2 × 200,000 t · 3 MINE HOPPERS",
            ox + 0.5, oy + 14.6, ox + 23.7, oy + 22.0, style="stage")
    else:
        p1 = (ox + 12.0, oy + 72.0, ox + 112.0, oy + 74.7)
        p2 = (ox + 12.0, oy + 61.0, ox + 112.0, oy + 63.7)
        rx0, rx1 = ox + 10.5, ox + 113.5
        y_st1, y_bwr, y_st2 = oy + 76.0, oy + 67.85, oy + 59.7
        hopper_specs = (
            ("TM1", ox + 4.0, oy + 74.3,
             "L-BG-HOP-L1",
             [(ox + 5.2, oy + 74.3), (ox + 8.0, oy + 74.3),
              (rx0, y_st1)]),
            ("TSM3", ox + 4.0, oy + 68.0,
             "L-BG-HOP-L2",
             [(ox + 5.2, oy + 68.0), (ox + 8.0, oy + 68.0),
              (ox + 8.0, oy + 75.2), (rx0, oy + 75.2)]),
            ("TSM2", ox + 4.0, oy + 61.7,
             "L-BG-HOP-L3",
             [(ox + 5.2, oy + 61.7), (ox + 8.0, oy + 61.7),
              (rx0, y_st2)]),
        )
        machine_scale = 0.62
        zone = ZoneGeo(
            "MINE ORE STORAGE · 2 stockpiles × 200,000 t · "
            "TM1 / TSM2 / TSM3 HOPPERS",
            ox + 1.0, oy + 58.4, ox + 122.5, oy + 77.4, style="stage")

    piles = [
        PileGeo(
            "BG-MINE-1", *p1, label=site_tag("BENGUERIR", "BG-MINE-1"),
            capacity_t=200_000, visual_width_factor=1.55,
            hover_note="scenario inventory · upstream mine ore"),
        PileGeo(
            "BG-MINE-2", *p2, label=site_tag("BENGUERIR", "BG-MINE-2"),
            capacity_t=200_000, visual_width_factor=1.55,
            hover_note="scenario inventory · upstream mine ore"),
    ]
    rails = [
        RailGeo(
            "BG-MINE-ST1-R", rx0, y_st1, rx1, kind="stacker",
            flanks=["BG-MINE-1"], working_sides="below",
            label="" if compact else "Mine stacker 1 · pile 1",
            source=_BEN_MINE_LAYOUT_SOURCE, confidence=TOPO_CONF),
        RailGeo(
            "BG-MINE-R", rx0, y_bwr, rx1, kind="reclaimer",
            flanks=["BG-MINE-1", "BG-MINE-2"], working_sides="both",
            label="" if compact else "Shared mine reclaimer · both piles",
            source=_BEN_MINE_LAYOUT_SOURCE, confidence=TOPO_CONF),
        RailGeo(
            "BG-MINE-ST2-R", rx0, y_st2, rx1, kind="stacker",
            flanks=["BG-MINE-2"], working_sides="above",
            label="" if compact else "Mine stacker 2 · pile 2",
            source=_BEN_MINE_LAYOUT_SOURCE, confidence=TOPO_CONF),
    ]
    machines = [
        MachineGeo(
            "BG-MINE-ST-1", "BG-MINE-ST1-R", 0.28, machine_scale,
            kind="stacker", label=site_tag("BENGUERIR", "BG-MINE-ST-1"),
            served_pile="BG-MINE-1", label_side=1,
            note="Scenario-backed mine stacker · physical tag and metric "
                 "position TBC; configured rate is shown on hover."),
        MachineGeo(
            "BG-MINE-BWR", "BG-MINE-R", 0.52, machine_scale,
            kind="reclaimer", label=site_tag("BENGUERIR", "BG-MINE-BWR"),
            served_piles=["BG-MINE-1", "BG-MINE-2"], label_side=1,
            note="Scenario-backed shared mine reclaimer · reaches both "
                 "mine piles; physical tag and metric position TBC."),
        MachineGeo(
            "BG-MINE-ST-2", "BG-MINE-ST2-R", 0.74, machine_scale,
            kind="stacker", label=site_tag("BENGUERIR", "BG-MINE-ST-2"),
            served_pile="BG-MINE-2", label_side=1,
            note="Scenario-backed mine stacker · physical tag and metric "
                 "position TBC; configured rate is shown on hover."),
    ]
    equipment = [
        EquipGeo("hopper", x, y, label, link_ids=[link_id], tbc=True,
                 scale=0.70 if compact else 0.90)
        for label, x, y, link_id, _ in hopper_specs
    ]
    conveyors = [
        ConveyorGeo(
            f"BG-HOP-{label}", points, link_ids=[link_id],
            tag="", compact=True)
        for label, _, _, link_id, points in hopper_specs
    ]
    return piles, rails, machines, equipment, conveyors, zone


def _benguerir_mine_to_screened_routes(
        ox: float, oy: float, compact: bool
) -> Tuple[List[ConveyorGeo], EquipGeo]:
    """Connect mine reclaim to the two dedicated screened-pile stackers.

    The three simulation campaigns share the mine-reclaim collection, then
    split to the two configured screened piles: L1/L3 to BG-RAW-1 and L2 to
    BG-RAW-2.  This is a schematic process connection, not a claim about
    physical conveyor tags or surveyed routing.
    """
    mine_links = [f"L-BG-MINE-RCL-OUT-L{i}" for i in (1, 2, 3)]
    raw_1_links = [f"L-BG-SCREEN-STK-OUT-L{i}" for i in (1, 3)]
    raw_2_links = ["L-BG-SCREEN-STK-OUT-L2"]
    if compact:
        screen_x, screen_y = ox + 24.4, oy + 17.6
        routes = [
            ConveyorGeo(
                "BG-MINE-SCREEN",
                [(ox + 23.2, oy + 17.6), (screen_x, screen_y)],
                link_ids=mine_links, compact=True),
            ConveyorGeo(
                "BG-SCREEN-RAW-1",
                [(screen_x, screen_y), (ox + 24.8, oy + 17.6),
                 (ox + 24.8, oy + 19.7), (ox + 25.8, oy + 19.7)],
                link_ids=raw_1_links, compact=True),
            ConveyorGeo(
                "BG-SCREEN-RAW-2",
                [(screen_x, screen_y), (ox + 24.8, oy + 17.6),
                 (ox + 24.8, oy + 15.4), (ox + 25.8, oy + 15.4)],
                link_ids=raw_2_links, compact=True),
        ]
        screen = EquipGeo(
            "gate", screen_x, screen_y, "SCREEN",
            link_ids=mine_links, tbc=True, scale=0.55)
    else:
        screen_x, screen_y = ox + 119.0, oy + 57.6
        routes = [
            ConveyorGeo(
                "BG-MINE-SCREEN",
                [(ox + 113.5, oy + 67.85), (screen_x, oy + 67.85),
                 (screen_x, screen_y)],
                link_ids=mine_links, tag="mine ore → screening",
                label_frac=0.72, label_dy=1.0),
            ConveyorGeo(
                "BG-SCREEN-RAW-1",
                [(screen_x, screen_y), (screen_x, oy + 55.15),
                 (ox + 63.5, oy + 55.15)],
                link_ids=raw_1_links, tag="screened ore → pile 1",
                label_frac=0.70, label_dy=1.0),
            ConveyorGeo(
                "BG-SCREEN-RAW-2",
                [(screen_x, screen_y), (ox + 116.0, screen_y),
                 (ox + 116.0, oy + 40.4), (ox + 63.5, oy + 40.4)],
                link_ids=raw_2_links, tag="screened ore → pile 2",
                label_frac=0.78, label_dy=-1.0),
        ]
        screen = EquipGeo(
            "gate", screen_x, screen_y, "SCREEN",
            link_ids=mine_links, tbc=True, scale=0.85)
    return routes, screen


def benguerir_geometry(sc, ox: float = 0.0, oy: float = 0.0,
                       compact: bool = False) -> SiteGeometry:
    """Six-pile Benguerir arrangement with stable scheduler-bound IDs."""
    g = _benguerir_base_geometry(sc, ox=ox, oy=oy, compact=compact)
    if compact:
        # Open a full upstream bay while retaining the already-validated raw,
        # washing, product, train and C2 relative geometry.
        _shift_geometry_x(g, 24.0)
        g.bounds = (ox, oy, ox + 108.0, oy + 22.5)
        g.zones = [
            z for z in g.zones
            if not (z.style == "scope"
                    and "BENGUERIR SMS" in z.label.upper())
        ]
        g.zones.insert(
            0, ZoneGeo("BENGUERIR MINE + BENEFICIATION SMS",
                       ox + 0.2, oy + 0.2, ox + 107.5, oy + 22.2))
        g.height_px = 450
    else:
        g.bounds = (ox, oy, ox + 124.0, oy + 78.5)
        g.height_px = 980

    piles, rails, machines, equipment, conveyors, mine_zone = \
        _benguerir_mine_stage(ox, oy, compact)
    g.piles = piles + g.piles
    g.rails = rails + g.rails
    g.machines = machines + g.machines
    g.equipment = equipment + g.equipment
    g.conveyors = conveyors + g.conveyors
    g.zones.append(mine_zone)
    screened_routes, screen = _benguerir_mine_to_screened_routes(
        ox, oy, compact)
    g.conveyors = screened_routes + g.conveyors
    g.equipment.append(screen)
    g.source += " + " + _BEN_MINE_LAYOUT_SOURCE
    g.gaps.append((
        "BENGUERIR", "six-stockpile mine/beneficiation layout",
        "Project-team instruction 28-Jul-2026: BG-MINE-1/-2 + "
        "BG-RAW-1/-2 + BG-P1/-2, with TM1/TSM2/TSM3 mine hoppers. "
        "Hopper ratings are not shown; available B01 evidence gives only "
        "one line-specific TBC value. Metric positions remain schematic."))
    _bind_benguerir_scheduler_assets(sc, g)
    return g


def _bg_gaps(b02_on: bool):
    return [
        ("BENGUERIR", "B02 current-configuration status",
         "The project team confirms the B02 table as the current Benguerir "
         "configuration; '**' is retained as source notation only. " +
         ("ST3/RC2/RC3 enabled in the baseline"
          if b02_on else "current assets disabled only for a non-baseline "
          "sensitivity: reference-only")),
        ("BENGUERIR", "raw/screened ore-storage topology (Table 20)",
         "USER_CONFIRMED 20-Jul-2026: 2 piles · 2 dedicated stackers (one "
         "per pile) · 1 shared reclaimer reaching both piles. Machine "
         "physical tags and metric rail positions remain TBC — none "
         "invented. " + _T20),
        ("BENGUERIR", "raw ore-storage grade composition / live snapshot",
         "400,000 t total live capacity from Table 20 allocated as "
         "BG-RAW-1 = 200,000 t and BG-RAW-2 = 200,000 t per project-team "
         "instruction 22-Jul-2026. A separate project-team instruction "
         "24-Jul-2026 sets the scenario starting mass to 100,000 t per pile "
         "(50% fill). These masses are now solver inventory. The current "
         "demonstration assigns the shared mine-neutral Global grade to "
         "BG-RAW-1 and Low-Cd to BG-RAW-2; no live pile-specific Benguerir "
         "assay or segment snapshot is loaded. Multiple profiles may coexist "
         "physically, but they need surveyed zones/reclaim sequencing before "
         "the planner can treat them as independently reclaimable."),
        ("BENGUERIR", "raw-storage reclaimer capacity conflict",
         "Table 20 (screened-ore storage): 2,100 nominal / 2,500 design "
         "t/h, humid, reclaimer marked 'Out of scope' in the source "
         "package. B02 (expansion interface, §2.3.2): 2,600 nominal / "
         "3,120 design t/h. Whether B02 upgrades, replaces or supplements "
         "the Table 20 reclaimer is TBC — no equipment list/PFD proves "
         "equivalence; both values kept separately traceable, neither "
         "silently overwritten"),
        ("BENGUERIR", "raw-storage package scope vs planning scope",
         "the source table marks the reclaimer 'Out of scope' for ITS "
         "handling package (package_scope=OUT_OF_SCOPE) — this is NOT "
         "inferred to mean outside the SMS or the job-allocation "
         "application. Per the 28-Jul-2026 simulation instruction, all three "
         "raw-storage machines are present in Scenario.machines and receive "
         "solver jobs in the six-pile built-in. Live SMS integration and "
         "physical equipment tags remain TBC."),
        ("BENGUERIR", "B01 ↔ B02 ↔ Table 20 equipment mapping",
         "process stages connected (B02 §2.6); exact 04BG-* ↔ RB/SL/RL-* "
         "equipment/conveyor mapping and phase relationship TBC — tag "
         "registers not merged. B01's '1 reclaimer + 1 stacker 2,200 t/h' "
         "and Table 20's '2 stackers + 1 reclaimer, 2,100/2,500 each' "
         "describe the same process stage from different sources and are "
         "NOT assumed identical — preserved as a named document conflict"),
        ("BENGUERIR", "reclaim-line arrangement",
         "BoD schematic suggests SEPARATE RC2/RC3 lines — drawn so, but "
         "metric positions and machine-to-pile reachability TBC (no GA); "
         "solver reachability both-lanes is ASSUMED"),
        ("BENGUERIR", "stockpile autonomy",
         "combined-yard theoretical ≈6.1 d nominal / ≈5.1 d design; BoD "
         "table states 5/6 d; earlier datasheet's ≈10-day claim is a "
         "DOCUMENT CONFLICT (retained, not silently deleted)"),
        ("BENGUERIR", "'3 qualities on 2 piles' operating rule",
         "from the earlier conveying-system datasheet only — NOT established "
         "by the B02 BoD; operating rule TBC; model uses one grade per pile"),
        ("BENGUERIR", "washing output / ST3 stacking",
         "Three campaign feed rates, recoveries and line-to-product-grade "
         "mapping are ASSUMED because the available BoD gives equipment "
         "capacities rather than approved production orders; recovered "
         "output is planned through ST3"),
        ("BENGUERIR", "train loading scope",
         "raw + product train interfaces DOCUMENTED (1,000/1,200 t/h); "
         "whether this layer controls train demand is SCOPE_TBC; one "
         "loading station ASSUMED shared — simultaneous use TBC"),
        ("BENGUERIR", "B01 2023 mass-rate inconsistencies",
         "1,208 t/h screen undersize vs 1,055 t/h product handling · "
         "541+89 = 630 vs stated 628 t/h reject — preserved data gaps, "
         "never silently normalised (" + _B01 + ")"),
        ("BENGUERIR", "same-pile concurrency policy",
         "TBC for Benguerir — MPH rule (USER_CONFIRMED) not extrapolated"),
    ]


# ==================================== LOUTA ========================================
#: Louta Beneficiation Plant process description — the equipment-tagged
#: AUTHORITATIVE Louta source (verified 25-Jul-2026).  Document control in the
#: PDF itself is inconsistent: the cover states REP-00002 while every body
#: header/footer states REP-00004.  Both numbers are recorded verbatim; neither
#: is silently preferred.
_LOU_PD = ("Louta Beneficiation Plant Process description, Rev B "
           "(IFA), 25-Apr-2023, §3.1 pp. 5–8 — document number inconsistent "
           "in the source: cover Q80130-LOU-L02-PR-REP-00002 vs body header "
           "Q80130-LOU-L02-PR-REP-00004")
#: L02 washing/beneficiation capacity, process description p. 5
_LOU_PD_LINES = "two parallel production lines L1 & L2 · 3 Mt tsm/year each"
_LOU_WASH_TREATMENT_TIME = (
    f"line residence "
    f"{WASHING_LINE_RESIDENCE_TIME_H['LOUTA'] * 60:g} min (ASSUMED) · "
    "documented scrubber step 2.5–3 min"
)
_LOU_WASH_TREATMENT_SOURCE = (
    "30 min: project-team assumption, 26-Jul-2026; scrubber: "
    "Q80130-LOU-L02-PR-BOD-0001 Rev B, §8.2.1 p.21"
)

LOUTA_GAPS = [
    ("LOUTA", "102MG screened-product storage (2 stackers + 1 reclaimer)",
     "MODELLING_GAP[MISSING_ROUTE_RATE_REACH] · PRE-beneficiation mine "
     "storage: the L01 process description confirms two stockpiles, "
     "dedicated stackers 102MG-ST-01/-02 at 2,200 t/h nominal and one shared "
     "reclaimer 110MG-RC-01. Actual capacities, live mass, reclaimer rate, "
     "metric reach/positions and executable route integration remain open. "
     "S3-A25 explicitly assumes a "
     "display-only 100,000 t capacity and 50,000 t t0 mass per pile (50%) "
     "with two raw-profile bands; not solver inventory. The confirmed "
     "494,274 t homogenized ore-storage capacity is NOT theirs. "
     + _LOU_L01_PD),
    ("LOUTA", "102MG → 110LG ore-storage route",
     "110MG-RC-01 and reclaim conveyor 110MG-CB-01 toward beneficiation are "
     "documented, as is the CP-06 / FG-01 bypass to 120LG-CV-09. The exact "
     "L01-to-L02 interface and solver transfer remain unconfigured; no direct "
     "102MG → C1 connection exists or is drawn. " + _LOU_L01_PD),
    ("LOUTA", "110/120LG ore-storage machine count — source discrepancy",
     "the generic BoD Rev B table lists ONE ore reclaimer; the later, "
     "equipment-tagged process description specifies TWO ore reclaimers "
     "120LG-RC-01/-02 (each 3,000 t/h wet) and ONE ore stacker 110LG-ST-01 "
     "(2,100 t/h wet, boom conveyor 110LG-BC-01, two homogenized stockpiles "
     "by chevron stacking). The process-description configuration is used "
     "for the mimic. " + _LOU_PD),
    ("LOUTA", "110/120LG ore reclaimer ↔ pile pairing",
     "the process description confirms TWO homogenized ore stockpiles and "
     "TWO ore reclaimers but does NOT state their pairing (both reclaim "
     "routes converge on the common 120LG-CV-02/-03 (RB4/RB5) transfer "
     "routing, which can feed washing L1 or L2). One reclaimer per pile is "
     "the APPLIED PLANNING GEOMETRY — a reach/GA matrix is required to "
     "confirm it"),
    ("LOUTA", "110/120LG homogenized ore-storage split",
     "total homogenized ore-storage capacity 494,274 t confirmed (Louta BoD "
     "Rev B); the BoD gives no individual pile capacities, so the equal "
     "two-pile split (2 × 247,137 t) is DERIVED, and the 50% starting "
     "inventory (123,568.5 t per pile) is an ASSUMED demonstration "
     "baseline, not an SMS snapshot"),
    ("LOUTA", "110/120LG ore composition bands",
     "the shared raw BT profiles (Global, High-Cd, Low-Cd) use chemistry "
     "currently documented in the Louta BoD, but their distribution across the two "
     "homogenized piles is ASSUMED (50/50 per pile, mass-proportional "
     "bands at display repères) — no pile survey, SQMS segment snapshot or "
     "campaign rule is on file"),
    ("LOUTA", "washing / beneficiation lines L1 + L2",
     "CONFIRMED current parallel configuration: " + _LOU_PD_LINES
     + ". Each line is represented by an explicit solver process transfer "
       "from its 110LG ore pile to a line-specific WIP interface, including "
       "the configured recovery and 30-minute minimum dependency. The plant "
       "line remains a process stage, not a mobile Machine; per-line live "
       "availability and operating campaign data remain unavailable"),
    ("LOUTA", "130/140LG product-storage split",
     "total product-storage capacity 383,024 t confirmed (Louta BoD Rev B) "
     "and ONE shared product stacker 130LG-ST-01 + TWO product reclaimers "
     "140LG-RC-01/-02 (3,000 t/h each) are confirmed by the process "
     "description; the PHYSICAL product-pile count is NOT explicitly "
     "stated. LT-P1/LT-P2 are the planning/solver lines with a DERIVED "
     "equal split (2 × 191,512 t) and an ASSUMED 50% start (95,756 t "
     "each)"),
    ("LOUTA", "C1 feeder confluence (RL3/RL4)",
     "each reclaim route selects RL3/RL4 via its flip gate (140LG-FG-01/"
     "-02); simultaneous partial-rate reclaim TBC/configurable — exclusive "
     "feeder group active until confluence is confirmed"),
    ("LOUTA", "same-pile concurrency policy",
     "physical simultaneous stack/reclaim clearance remains TBC; S0 applies "
     "a conservative no-same-pile-in-the-same-hour rule until the Louta GA "
     "and operating procedure are supplied"),
]

C1_FEEDER_TXT = ("C1 FEEDER SELECTION — either 140LG-RC-01 or -02 "
                 "(3,000 t/h nom each) · C1 ≤ 2,600 t/h · simultaneous "
                 "partial-rate reclaim TBC/configurable")


def _lou_mine_display_capacity(sc, pile_id: str) -> Optional[float]:
    """Per-pile ASSUMED display capacity for 102MG mine storage."""
    return _cfg_pile_float(
        sc, "LOU_MINE_102MG_DISPLAY_CAPACITY_T", pile_id)


def _lou_mine_display_mass(sc, pile_id: str) -> Optional[float]:
    """Static ASSUMED t0 display mass for 102MG (never solver inventory)."""
    return _cfg_pile_float(
        sc, "LOU_MINE_102MG_DISPLAY_START_MASS_T", pile_id)


def _lou_mine_display_grade(sc, pile_id: str) -> Dict[str, object]:
    """ASSUMED multi-profile display composition for a 102MG mine pile."""
    cfg = getattr(sc, "config", {}) or {}
    component_cfg = cfg.get("LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS", {})
    if not isinstance(component_cfg, dict):
        return {"display_grade_issues": [
            "LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS must be a mapping"]}
    rows = component_cfg.get(pile_id, [])
    if not rows:
        return {}
    components, issues = _parse_display_components(
        sc, pile_id, rows, keep_moisture=True)
    if issues:
        return {"display_grade_issues": issues}
    source_basis = str(
        cfg.get("LOU_MINE_102MG_DISPLAY_GRADE_BASIS", "") or "")
    composition_basis = str(
        cfg.get(
            "LOU_MINE_102MG_DISPLAY_GRADE_COMPOSITION_BASIS", "") or "")
    return _composite_display_grade(
        components,
        "; ".join(value for value in (source_basis, composition_basis)
                  if value))


def _lou_mine_pile(sc, pile_id: str, x0: float, y0: float, x1: float,
                   y1: float, label: str,
                   with_total: bool = True) -> PileGeo:
    """One 102MG reference lane with the explicit assumed demo baseline."""
    return PileGeo(
        pile_id, x0, y0, x1, y1, modelled=False, tbc=True, label=label,
        capacity_t=_lou_mine_display_capacity(sc, pile_id),
        display_mass_t=_lou_mine_display_mass(sc, pile_id),
        display_note="ASSUMED demo: 100,000 t capacity · 50,000 t t0 "
                     "(50%) · not an SMS snapshot or solver inventory",
        label_with_total=with_total, canvas_label=label,
        **_lou_mine_display_grade(sc, pile_id))


def _lou_ore_pile(sc, pile_id: str, x0: float, y0: float, x1: float,
                  y1: float, label: str,
                  with_total: bool = True) -> PileGeo:
    """One model-backed homogenized ore stockpile.

    Capacity, inventory and profile bands resolve from the matching
    ``StockSpace``/monitor view-model.  The equal capacity split and t0
    inventory remain explicit assumptions in the scenario; they are no
    longer copied into a second, static display-only lane.
    """
    return PileGeo(
        pile_id, x0, y0, x1, y1, modelled=True, tbc=True, label=label,
        label_with_total=with_total,
        hover_note="solver inventory · DERIVED equal capacity split · "
                   "ASSUMED t0 mass/profile campaign")


def _louta_washing_lines(ox: float, oy: float,
                         compact: bool) -> List[ProcessLineGeo]:
    """The TWO parallel Louta washing/beneficiation lines L1 and L2.

    Both are CURRENT parallel production lines per the process description
    (p. 5: 3 Mt tsm/year each) — never a future expansion, so both carry the
    OPERATING style.  The cards identify the process stages; feed/output is
    represented by explicit process transfers and line-specific WIP spaces,
    while the fixed plant lines are not misclassified as mobile machines.
    """
    if compact:
        bands = ((ox + 28.5, oy + 12.0, ox + 41.5, oy + 13.4),
                 (ox + 28.5, oy + 9.4, ox + 41.5, oy + 10.8))
    else:
        bands = ((ox + 20.0, oy + 18.4, ox + 45.0, oy + 20.4),
                 (ox + 50.0, oy + 18.4, ox + 75.0, oy + 20.4))
    note_common = (
        "CURRENT parallel production line per the Louta process "
        "description (3 Mt tsm/year per line). Solver-modelled process "
        "transfer with a line-specific WIP interface; the line is a process "
        "stage rather than a mobile Machine. The configured 30-minute "
        "minimum dependency is enforced before downstream product stacking. "
        + _LOU_PD)
    return [
        ProcessLineGeo("LT-WASH-L1", *bands[0], label="LINE L1",
                       status="OPERATING", source=_LOU_PD,
                       evidence_status="CONFIRMED",
                       note="Washing line L1 — " + note_common,
                       residence_minutes=(
                           WASHING_LINE_RESIDENCE_TIME_H["LOUTA"] * 60),
                       treatment_time=_LOU_WASH_TREATMENT_TIME,
                       treatment_time_source=_LOU_WASH_TREATMENT_SOURCE),
        ProcessLineGeo("LT-WASH-L2", *bands[1], label="LINE L2",
                       status="OPERATING", source=_LOU_PD,
                       evidence_status="CONFIRMED",
                       note="Washing line L2 — " + note_common,
                       residence_minutes=(
                           WASHING_LINE_RESIDENCE_TIME_H["LOUTA"] * 60),
                       treatment_time=_LOU_WASH_TREATMENT_TIME,
                       treatment_time_source=_LOU_WASH_TREATMENT_SOURCE),
    ]


def louta_geometry(sc, ox: float = 0.0, oy: float = 0.0,
                   compact: bool = False) -> SiteGeometry:
    """Louta = TWO control scopes: Mine SMS (L01/102MG pre-beneficiation mine
    storage) and Beneficiation SMS (L02).

    Process flow, top to bottom (process description §3.1):
        102MG mine storage → TWO homogenized ore stockpiles (110LG-ST-01
        stacking rail between them, 120LG-RC-01/-02 bridge reclaimers each
        on its own pile) → washing L1 / L2 → TWO product planning lines
        (130LG-ST-01 shared stacker, 140LG-RC-01/-02) → C1 corridor.

    The L01 screened-product yard has TWO dedicated stackers and ONE shared
    reclaimer; these remain reference-only until live state, metric reach and
    the L01-to-L02 solver interface are configured.  102MG must NOT connect
    to C1/M16 directly, and the confirmed 494,274 t homogenized ore-storage
    capacity belongs to the 110LG piles, never to 102MG.  The 110/120LG ore
    stage, L1/L2 process transfers, 130/140LG product stage and C1 dispatch
    are model-backed.
    """
    g = SiteGeometry(
        "LOUTA", "LOUTA mine + beneficiation SMS",
        bounds=(ox, oy, ox + 128, oy + 56), not_to_scale=True,
        source=f"{_LOU_L01_PD} + {_LOU_L01_PSR} + {_LOU_PD} + {_CROP}")
    # ---------------- L01 MINE SMS scope (top) ----------------------------------
    # The empty MINE/screening placeholder boxes were removed (25-Jul-2026):
    # they carried no evidence and no asset.  Extraction/screening enters as a
    # single labelled interface arrow.
    g.zones.append(ZoneGeo(
        "LOUTA MINE SMS · 102MG SCREENED-PRODUCT STORAGE · 2 STOCKPILES "
        "· 2 DEDICATED STACKERS + 1 SHARED RECLAIMER",
        ox + 1, oy + 40.8, ox + 104, oy + 54.3))
    g.conveyors.append(ConveyorGeo(
        "LT-MINE-102MG", [(ox + 7, oy + 46.9), (ox + 18, oy + 46.9)],
        tag="TSM1 / TSM2", label_frac=0.05, label_dy=-1.2))
    g.equipment.append(EquipGeo(
        "tower", ox + 18, oy + 46.9, "102MG-DT-01", scale=0.72))
    g.conveyors += [
        ConveyorGeo(
            "LT-102MG-ST1-FEED",
            [(ox + 18, oy + 46.9), (ox + 18, oy + 52.2),
             (ox + 20.5, oy + 52.2)],
            tbc=True),
        ConveyorGeo(
            "LT-102MG-ST2-FEED",
            [(ox + 18, oy + 46.9), (ox + 18, oy + 41.5),
             (ox + 20.5, oy + 41.5)],
            tbc=True),
    ]
    # The counts and dedicated/shared duties are documented; rail placement
    # remains schematic because neither supplied source is a metric GA.
    g.piles += [
        _lou_mine_pile(
            sc, "102MG-SP-01", ox + 22, oy + 48.4, ox + 92, oy + 51.4,
            "MINE STOCKPILE-01"),
        _lou_mine_pile(
            sc, "102MG-SP-02", ox + 22, oy + 42.4, ox + 92, oy + 45.4,
            "MINE STOCKPILE-02"),
    ]
    g.rails += [
        RailGeo(
            "102MG-ST1-R", ox + 20.5, oy + 52.2, ox + 93.5,
            kind="stacker", flanks=["102MG-SP-01"],
            working_sides="below",
            label="",
            tbc=True, source=f"{_LOU_L01_PD} + {_LOU_L01_PSR}",
            confidence=TOPO_CONF),
        RailGeo(
            "110MG-R", ox + 20.5, oy + 46.9, ox + 93.5,
            kind="reclaimer", flanks=["102MG-SP-01", "102MG-SP-02"],
            working_sides="both",
            label="",
            tbc=True, source=_LOU_L01_PD, confidence=TOPO_CONF),
        RailGeo(
            "102MG-ST2-R", ox + 20.5, oy + 41.5, ox + 93.5,
            kind="stacker", flanks=["102MG-SP-02"],
            working_sides="above",
            label="",
            tbc=True, source=f"{_LOU_L01_PD} + {_LOU_L01_PSR}",
            confidence=TOPO_CONF),
    ]
    g.machines += [
        MachineGeo("102MG-ST-01", "102MG-ST1-R", 0.28, 0.62, display=True,
                   kind="stacker", label=site_tag("LOUTA", "102MG-ST-01"),
                   served_pile="102MG-SP-01", home_repere=280,
                   label_side=1,
                   note="Dedicated to MINE STOCKPILE-01 · 2,200 t/h in the "
                        "L01 Rev-3 process description (older study: 1,058 "
                        "t/h) · reference-only; metric position schematic"),
        MachineGeo("110MG-RC-01", "110MG-R", 0.52, 0.62, display=True,
                   kind="reclaimer",
                   label=site_tag("LOUTA", "110MG-RC-01"),
                   served_piles=["102MG-SP-01", "102MG-SP-02"],
                   label_side=1,
                   note="One shared reclaimer for both stockpiles; "
                        "110MG-CB-01 carries material toward beneficiation · "
                        "reference-only; rate and metric reach not loaded"),
        MachineGeo("102MG-ST-02", "102MG-ST2-R", 0.72, 0.62, display=True,
                   kind="stacker", label=site_tag("LOUTA", "102MG-ST-02"),
                   served_pile="102MG-SP-02", home_repere=720,
                   label_side=1,
                   note="Dedicated to MINE STOCKPILE-02 · 2,200 t/h in the "
                        "L01 Rev-3 process description (older study: 1,058 "
                        "t/h) · reference-only; metric position schematic"),
    ]
    for i, tg in enumerate([
            "102MG-CP-05/06/07/08 · stacker feeders",
            "102MG-FG-01 · bypass to 120LG-CV-09",
            "102MG-SP-01/02 · samplers (not pile IDs)",
    ]):
        g.chips.append(ChipGeo(tg, ox + 98.0, oy + 51.0 - 2.4 * i))
    # Documented reclaim direction; the L01/L02 interface remains schematic.
    g.conveyors.append(ConveyorGeo(
        "LT-102MG-BEN",
        [(ox + 93.5, oy + 46.9), (ox + 96, oy + 46.9),
         (ox + 96, oy + 39.4), (ox + 84, oy + 39.4),
         (ox + 84, oy + 38.8)],
        tag="110MG-CB-01 → beneficiation",
        sub="L01/L02 interface schematic", tbc=True,
        label_frac=0.68, label_dy=-0.8))
    # ---------------- L02 BENEFICIATION SMS scope -------------------------------
    g.zones.append(ZoneGeo("LOUTA BENEFICIATION SMS — control scope "
                           "(L02 · Mine-SMS endpoint TBC)",
                           ox + 1, oy + 0.7, ox + 126.5, oy + 38.6))
    # ---- stage 1: homogenized ore storage (110/120LG) --------------------------
    # Process description §3.1.1–3.1.2: ONE ore stacker 110LG-ST-01 (2,100 t/h
    # wet) with boom conveyor 110LG-BC-01 forms exactly TWO homogenized
    # stockpiles by chevron stacking; TWO bridge-type bucket-wheel reclaimers
    # 120LG-RC-01/-02 at 3,000 t/h wet each, whose routes converge on the
    # common RB4/RB5 transfer routing feeding washing L1 or L2.
    g.zones.append(ZoneGeo("110/120LG HOMOGENIZED ORE STORAGE — 2 stockpiles "
                           "· 494,274 t total · 2 × 247,137 t DERIVED split",
                           ox + 3, oy + 23.6, ox + 74, oy + 38.0,
                           style="stage", tbc=True))
    g.chips.append(ChipGeo(
        "S0 MINE-FEED BOUNDARY", ox + 99, oy + 34.2,
        note="LT-MINE-FEED-GLOBAL / LOW-CD · 10,000 t each · finite solver "
             "buffers feeding 110LG-ST-01; not 102MG inventory",
        kind="info"))
    g.piles += [
        _lou_ore_pile(sc, "110LG-OL1", ox + 8, oy + 32.4, ox + 66, oy + 35.2,
                      "ORE PILE 1"),
        _lou_ore_pile(sc, "110LG-OL2", ox + 8, oy + 26.6, ox + 66, oy + 29.4,
                      "ORE PILE 2"),
    ]
    g.rails += [
        RailGeo("120LG-R1", ox + 6.5, oy + 35.9, ox + 67.5, kind="reclaimer",
                flanks=["110LG-OL1"], working_sides="below",
                label="120LG-RC-01 reclaim line — ore pile 1 · 3,000 t/h wet",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
        RailGeo("110LG-R", ox + 6.5, oy + 30.9, ox + 67.5, kind="stacker",
                flanks=["110LG-OL1", "110LG-OL2"], working_sides="both",
                label="110LG-ST-01 common stacking rail (110LG-TC-01 tripper "
                      "→ 110LG-BC-01 boom) · 2,100 t/h wet · either pile",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
        RailGeo("120LG-R2", ox + 6.5, oy + 25.1, ox + 67.5, kind="reclaimer",
                flanks=["110LG-OL2"], working_sides="above",
                label="120LG-RC-02 reclaim line — ore pile 2 · 3,000 t/h wet",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
    ]
    g.machines += [
        MachineGeo("110LG-ST-01", "110LG-R", 0.50, 0.6,
                   kind="stacker", label=site_tag("LOUTA", "110LG-ST-01"),
                   label_side=1,
                   note="ONE shared ore stacker on the common longitudinal "
                        "stacking rail between the two piles: 2,100 t/h wet, "
                        "fed by tripper conveyor 110LG-TC-01 (CB3), boom "
                        "conveyor 110LG-BC-01 forms BOTH homogenized "
                        "stockpiles by chevron stacking — either pile. "
                        "Solver-controlled for the explicit S0 ore-stock "
                        "replenishment transfers. " + _LOU_PD),
        MachineGeo("120LG-RC-01", "120LG-R1", 0.28, 0.6,
                   kind="reclaimer", label=site_tag("LOUTA", "120LG-RC-01"),
                   label_side=1, served_pile="110LG-OL1", home_repere=500,
                   note="Bridge-type bucket-wheel reclaimer with cross-"
                        "stockpile lateral boom conveyor, 3,000 t/h wet "
                        "(120LG-CV-06/RB1 → 120LG-CV-01/RB2 → flip gate "
                        "120LG-FG-01 → RB4/RB5 → scrubber feed hopper L1 or "
                        "L2). Shown ACROSS ore pile 1 at its R500 reference "
                        "position: one-reclaimer-per-pile is the APPLIED "
                        "PLANNING GEOMETRY — the process description "
                        "confirms two piles and two reclaimers but does not "
                        "state their pairing. Solver-controlled L1 feed. "
                        + _LOU_PD),
        MachineGeo("120LG-RC-02", "120LG-R2", 0.28, 0.6,
                   kind="reclaimer", label=site_tag("LOUTA", "120LG-RC-02"),
                   label_side=1, served_pile="110LG-OL2", home_repere=500,
                   note="Bridge-type bucket-wheel reclaimer, 3,000 t/h wet "
                        "(120LG-CV-07/RB3 → flip gate 120LG-FG-04 → RB4/RB5 "
                        "→ scrubber feed hopper L1 or L2). Shown ACROSS ore "
                        "pile 2 at its R500 reference position — pairing is "
                        "applied planning geometry, not documented. "
                        "Solver-controlled L2 feed. "
                        + _LOU_PD),
    ]
    # both reclaim routes converge into the COMMON transfer routing that can
    # feed either washing line (process description §3.1.2)
    g.conveyors += [
        ConveyorGeo("RB1/RB2", [(ox + 67.5, oy + 35.9), (ox + 71.5, oy + 35.9),
                                (ox + 71.5, oy + 31.4)],
                    link_ids=["L-LT-OL1-RB"],
                    tag="120LG-CV-06/-01 · RB1/RB2", tbc=True,
                    label_frac=0.35, label_dy=1.1),
        ConveyorGeo("RB3", [(ox + 67.5, oy + 25.1), (ox + 71.5, oy + 25.1),
                            (ox + 71.5, oy + 30.4)],
                    link_ids=["L-LT-OL2-RB"],
                    tag="120LG-CV-07 · RB3", tbc=True,
                    label_frac=0.35, label_dy=-1.1),
        ConveyorGeo("RB4/RB5", [(ox + 71.5, oy + 30.4), (ox + 71.5, oy + 22.9),
                                (ox + 62, oy + 22.9), (ox + 32, oy + 22.9)],
                    link_ids=["L-LT-RB45-COMMON"],
                    tag="RB4/RB5 — common ore transfer",
                    sub="either reclaim route → washing L1 or L2",
                    tbc=True, label_frac=0.72, label_dy=0.9),
        ConveyorGeo("RB6", [(ox + 32, oy + 22.9), (ox + 32, oy + 21.2)],
                    link_ids=["L-LT-RB6-L1"],
                    tag="RB6 → 210L1-HP-01", tbc=True, label_frac=0.5,
                    label_dy=0.0, compact=True),
        ConveyorGeo("RB7", [(ox + 62, oy + 22.9), (ox + 62, oy + 21.2)],
                    link_ids=["L-LT-RB7-L2"],
                    tag="RB7 → 210L2-HP-01", tbc=True, label_frac=0.5,
                    label_dy=0.0, compact=True),
    ]
    g.equipment.append(EquipGeo("diverter", ox + 71.5, oy + 30.9,
                                "120LG-FG-01/-04 flip gates", tbc=True,
                                scale=0.8))
    # ---- stage 2: washing / beneficiation — TWO PARALLEL LINES ------------------
    g.zones.append(ZoneGeo("WASHING / BENEFICIATION — 2 PARALLEL LINES",
                           ox + 14, oy + 15.8, ox + 80, oy + 22.0,
                           style="stage"))
    g.process_lines += _louta_washing_lines(ox, oy, compact=False)
    # washed product -> product-storage stacking (130LG), routed clear of the
    # product lanes on the far left
    g.conveyors += [
        ConveyorGeo("LT-L1-PSTORE", [(ox + 30, oy + 18.4), (ox + 30, oy + 16.6),
                                     (ox + 5.5, oy + 16.6)],
                    link_ids=["L-LT-WIP1-SL11"],
                    tbc=True, compact=True),
        ConveyorGeo("LT-L2-PSTORE", [(ox + 60, oy + 18.4), (ox + 60, oy + 17.4),
                                     (ox + 5.5, oy + 17.4)],
                    link_ids=["L-LT-WIP2-SL11"],
                    tbc=True, compact=True),
        ConveyorGeo("SL11", [(ox + 5.5, oy + 16.6), (ox + 5.5, oy + 7.1),
                             (ox + 6.5, oy + 7.1)],
                    link_ids=["L-LT-SL11-P1", "L-LT-SL11-P2"],
                    tag="SL11", tbc=True, label_frac=1.0, label_dy=1.0),
    ]
    # ---- stage 3: product storage + 140LG reclaim (SOLVER planning lines) -------
    g.zones.append(ZoneGeo("130/140LG PRODUCT STORAGE — 383,024 t total · "
                           "2 × 191,512 t planning lines (DERIVED equal "
                           "split)",
                           ox + 3, oy + 0.85, ox + 89, oy + 15.2,
                           style="stage", tbc=True))
    g.piles += [
        PileGeo("LT-P1", ox + 8, oy + 8.6, ox + 80, oy + 11.6,
                label=site_tag("LOUTA", "LT-P1") + " · product line 1",
                tbc=True, label_with_total=True),
        PileGeo("LT-P2", ox + 8, oy + 2.6, ox + 80, oy + 5.6,
                label=site_tag("LOUTA", "LT-P2") + " · product line 2",
                tbc=True, label_with_total=True),
    ]
    g.rails += [
        RailGeo("140LG-RL1", ox + 6.5, oy + 13.1, ox + 81.5, kind="reclaimer",
                flanks=["LT-P1"], working_sides="below",
                label="140LG-RC-01 reclaim line — product line 1 · 3,000 t/h",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
        RailGeo("130LG-R", ox + 6.5, oy + 7.1, ox + 81.5, kind="stacker",
                flanks=["LT-P1", "LT-P2"], working_sides="both",
                label="130LG-ST-01 common product stacking rail — serves "
                      "both product lines",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
        RailGeo("140LG-RL2", ox + 6.5, oy + 1.2, ox + 81.5, kind="reclaimer",
                flanks=["LT-P2"], working_sides="above",
                label="140LG-RC-02 reclaim line — product line 2 · 3,000 t/h",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC,
                label_pos="above"),
    ]
    g.machines += [
        MachineGeo("130LG-ST-01", "130LG-R", 0.50, 0.6,
                   kind="stacker", label=site_tag("LOUTA", "130LG-ST-01"),
                   label_side=1,
                   note="ONE shared product stacker on the common stacking "
                        "rail, fed by 130LG-CV-11 (SL11) — serves BOTH "
                        "product lines (process description §3.1.3). "
                        "Solver-controlled for the two explicit product-"
                        "stacking transfers; 1,000 t/h is the disclosed S0 "
                        "planning envelope because the source gives no "
                        "nameplate rate · POSITION TBC. " + _LOU_PD),
        MachineGeo("140LG-RC-01", "140LG-RL1", 0.50, 0.8,
                   label=site_tag("LOUTA", "140LG-RC-01"),
                   served_pile="LT-P1",
                   note="product reclaimer — 3,000 t/h nominal · reclaims "
                        "product line 1 (applied planning geometry; pairing "
                        "not stated in the source) · route 140LG-CV-03 / RL1 "
                        "→ flip gate 140LG-FG-01 → RL3/RL4 → C1"),
        MachineGeo("140LG-RC-02", "140LG-RL2", 0.50, 0.8,
                   label=site_tag("LOUTA", "140LG-RC-02"),
                   served_pile="LT-P2",
                   note="product reclaimer — 3,000 t/h nominal · reclaims "
                        "product line 2 (applied planning geometry) · route "
                        "140LG-CV-04 / RL2 → flip gate 140LG-FG-02 → RL3/RL4 "
                        "→ C1 · availability/flexibility, NOT extra corridor "
                        "throughput"),
    ]
    # ---- documented reclaim topology: CV-03/RL1 · CV-04/RL2 · flip gates --------
    g.conveyors += [
        ConveyorGeo("LT-P1-COLLECT",
                    [(ox + 81.5, oy + 13.1), (ox + 85.5, oy + 13.1)],
                    link_ids=["L-LT-P1-OUT"], tag="140LG reclaim · line 1",
                    label_frac=0.5, label_dy=1.2, compact=compact),
        ConveyorGeo("LT-P2-COLLECT",
                    [(ox + 81.5, oy + 1.2), (ox + 85.5, oy + 1.2)],
                    link_ids=["L-LT-P2-OUT"], tag="140LG reclaim · line 2",
                    label_frac=0.5, label_dy=1.2, compact=compact),
        ConveyorGeo("RL1", [(ox + 85.5, oy + 13.1), (ox + 91, oy + 11.4),
                            (ox + 98.6, oy + 11.4)],
                    machine_id="140LG-RC-01", tag="140LG-CV-03 · RL1",
                    label_frac=0.45, label_dy=1.6, compact=compact),
        ConveyorGeo("RL2", [(ox + 85.5, oy + 1.2), (ox + 91, oy + 6.2),
                            (ox + 98.6, oy + 6.2)],
                    machine_id="140LG-RC-02", tag="140LG-CV-04 · RL2",
                    label_frac=0.45, label_dy=-1.6, compact=compact),
        ConveyorGeo("RL3", [(ox + 101.4, oy + 11.4), (ox + 108, oy + 10.2),
                            (ox + 112.5, oy + 10.2)],
                    tag="RL3", sub="selection via flip gates TBC", tbc=True,
                    label_frac=0.55, label_dy=1.2, compact=compact),
        ConveyorGeo("RL4", [(ox + 101.4, oy + 6.2), (ox + 108, oy + 7.4),
                            (ox + 112.5, oy + 7.4)],
                    tag="RL4", tbc=True, label_frac=0.55, label_dy=-1.2,
                    compact=compact),
        ConveyorGeo("C1-takeoff", [(ox + 113.5, oy + 8.8),
                                   (ox + 117.3, oy + 8.8)],
                    link_ids=["C1"], tag="C1 take-off", label_frac=0.5,
                    label_dy=-1.4, compact=compact),
    ]
    g.equipment += [
        EquipGeo("diverter", ox + 100, oy + 11.4, "140LG-FG-01 flip gate",
                 tbc=True, scale=0.8),
        EquipGeo("diverter", ox + 100, oy + 6.2, "140LG-FG-02 flip gate",
                 tbc=True, scale=0.8),
        EquipGeo("junction", ox + 113.5, oy + 8.8),
        EquipGeo("corridor_out", ox + 121, oy + 8.8, "C1 → M16",
                 link_ids=["C1"], scale=0.8),
    ]
    g.stamps += [
        StampGeo(ox + 106, oy + 2.6, C1_FEEDER_TXT),
        StampGeo(ox + 103, oy + 54.6),
    ]
    g.gaps += LOUTA_GAPS
    g.height_px = 780
    return g


# ================================= complete chain ==================================
def _louta_compact(sc, ox: float = 0.0, oy: float = 24.0) -> SiteGeometry:
    """Compact Louta band for the end-to-end/global view.

    Keeps the FOUR process stages recognisable and in flow order — 102MG mine
    storage → two homogenized 110/120LG ore stockpiles → washing L1/L2 → two
    130/140LG product planning lines → C1. The 102MG stage uses its documented
    two dedicated stackers + one shared reclaimer; the later ore/product
    stages use one shared stacker + one reclaimer per pile. NO anonymous
    repeated stage icons and no empty placeholder blocks; the 110/120LG and
    130/140LG stages are model-backed, while the 102MG lanes remain
    reference-only.
    """
    g = SiteGeometry("LOUTA", "LOUTA (compact)",
                     bounds=(ox, oy, ox + 68, oy + 26.5), not_to_scale=True,
                     source=f"{_LOU_L01_PD} + {_LOU_L01_PSR} + {_LOU_PD}")
    g.zones.append(ZoneGeo("LOUTA — Mine + Beneficiation SMS", ox + 0.5,
                           oy + 0.5, ox + 67.5, oy + 25.6))
    # --- 102MG mine storage (display-only) --------------------------------------
    g.conveyors.append(ConveyorGeo(
        "LT-MINE-102MG", [(ox + 0.9, oy + 19.8), (ox + 3.0, oy + 19.8)],
        compact=True))
    g.equipment.append(EquipGeo(
        "tower", ox + 3.0, oy + 19.8, "102MG-DT-01", scale=0.42))
    g.conveyors += [
        ConveyorGeo(
            "LT-102MG-ST1-FEED",
            [(ox + 3.0, oy + 19.8), (ox + 3.0, oy + 23.5),
             (ox + 3.5, oy + 23.5)],
            tbc=True, compact=True),
        ConveyorGeo(
            "LT-102MG-ST2-FEED",
            [(ox + 3.0, oy + 19.8), (ox + 3.0, oy + 16.1),
             (ox + 3.5, oy + 16.1)],
            tbc=True, compact=True),
    ]
    g.piles += [
        _lou_mine_pile(
            sc, "102MG-SP-01", ox + 4.5, oy + 21.0, ox + 28, oy + 22.8,
            "MINE STOCKPILE-01", with_total=False),
        _lou_mine_pile(
            sc, "102MG-SP-02", ox + 4.5, oy + 16.8, ox + 28, oy + 18.6,
            "MINE STOCKPILE-02", with_total=False),
    ]
    g.rails += [
        RailGeo(
            "102MG-ST1-R", ox + 3.5, oy + 23.5, ox + 29,
            kind="stacker", flanks=["102MG-SP-01"],
            working_sides="below", label="", tbc=True,
            source=f"{_LOU_L01_PD} + {_LOU_L01_PSR}",
            confidence=TOPO_CONF),
        RailGeo(
            "110MG-R", ox + 3.5, oy + 19.8, ox + 29,
            kind="reclaimer", flanks=["102MG-SP-01", "102MG-SP-02"],
            working_sides="both", label="", tbc=True,
            source=_LOU_L01_PD, confidence=TOPO_CONF),
        RailGeo(
            "102MG-ST2-R", ox + 3.5, oy + 16.1, ox + 29,
            kind="stacker", flanks=["102MG-SP-02"],
            working_sides="above", label="", tbc=True,
            source=f"{_LOU_L01_PD} + {_LOU_L01_PSR}",
            confidence=TOPO_CONF),
    ]
    g.machines += [
        MachineGeo("102MG-ST-01", "102MG-ST1-R", 0.28, 0.42,
                   display=True,
                   kind="stacker", label="LOU · 102MG-ST-01", label_side=-1,
                   served_pile="102MG-SP-01", home_repere=280,
                   note="dedicated pile 1 · 2,200 t/h documented · "
                        "reference-only"),
        MachineGeo("110MG-RC-01", "110MG-R", 0.52, 0.42, display=True,
                   kind="reclaimer", label="LOU · 110MG-RC-01",
                   label_side=1,
                   served_piles=["102MG-SP-01", "102MG-SP-02"],
                   note="shared reclaimer · both stockpiles · reference-only"),
        MachineGeo("102MG-ST-02", "102MG-ST2-R", 0.72, 0.42,
                   display=True,
                   kind="stacker", label="LOU · 102MG-ST-02", label_side=1,
                   served_pile="102MG-SP-02", home_repere=720,
                   note="dedicated pile 2 · 2,200 t/h documented · "
                        "reference-only"),
    ]
    g.zones.append(ZoneGeo("102MG SCREENED-PRODUCT STORAGE · 2 ST + 1 RC "
                           "· PRE-beneficiation", ox + 2, oy + 15.7,
                           ox + 30.5, oy + 24.4, style="scope"))
    g.chips.append(ChipGeo("DT-01 · CP-05/06/07/08 · 110MG-CB-01",
                           ox + 40, oy + 20.0))
    # --- 110/120LG homogenized ore storage (solver-modelled) --------------------
    g.conveyors.append(ConveyorGeo(
        "LT-102MG-BEN",
        [(ox + 29, oy + 19.8), (ox + 30.5, oy + 19.8),
         (ox + 30.5, oy + 15.2), (ox + 23, oy + 15.2)],
        tag="110MG-CB-01", sub="to beneficiation · interface schematic",
        tbc=True, label_frac=0.5, label_dy=0.0, compact=True))
    g.zones.append(ZoneGeo("110/120LG HOMOGENIZED ORE", ox + 2, oy + 7.9,
                           ox + 23, oy + 15.2, style="stage", tbc=True))
    g.piles += [
        _lou_ore_pile(sc, "110LG-OL1", ox + 4.5, oy + 11.9, ox + 19,
                      oy + 13.1, "ORE PILE 1", with_total=False),
        _lou_ore_pile(sc, "110LG-OL2", ox + 4.5, oy + 9.1, ox + 19,
                      oy + 10.3, "ORE PILE 2", with_total=False),
    ]
    g.rails += [
        RailGeo("120LG-R1", ox + 3.8, oy + 13.8, ox + 20, kind="reclaimer",
                flanks=["110LG-OL1"], working_sides="below", label="",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC),
        RailGeo("110LG-R", ox + 3.8, oy + 11.1, ox + 20, kind="stacker",
                flanks=["110LG-OL1", "110LG-OL2"], working_sides="both",
                label="", tbc=True, source=_LOU_PD, confidence=TOPO_TBC),
        RailGeo("120LG-R2", ox + 3.8, oy + 8.4, ox + 20, kind="reclaimer",
                flanks=["110LG-OL2"], working_sides="above", label="",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC),
    ]
    g.machines += [
        MachineGeo("110LG-ST-01", "110LG-R", 0.50, 0.4,
                   kind="stacker", label="LOU · 110LG-ST-01", label_side=1,
                   note="ONE shared ore stacker on the common stacking rail "
                        "between both piles · 2,100 t/h wet · boom conveyor "
                        "110LG-BC-01 · chevron, either pile · solver-"
                        "controlled S0 ore replenishment. " + _LOU_PD),
        MachineGeo("120LG-RC-01", "120LG-R1", 0.28, 0.4,
                   kind="reclaimer", label="LOU · 120LG-RC-01", label_side=-1,
                   served_pile="110LG-OL1", home_repere=500,
                   note="bridge reclaimer across ore pile 1 · 3,000 t/h wet "
                        "· pairing = applied planning geometry · solver L1 "
                        "feed. " + _LOU_PD),
        MachineGeo("120LG-RC-02", "120LG-R2", 0.28, 0.4,
                   kind="reclaimer", label="LOU · 120LG-RC-02", label_side=-1,
                   served_pile="110LG-OL2", home_repere=500,
                   note="bridge reclaimer across ore pile 2 · 3,000 t/h wet "
                        "· pairing = applied planning geometry · solver L2 "
                        "feed. " + _LOU_PD),
    ]
    # --- washing / beneficiation — TWO PARALLEL LINES ----------------------------
    g.conveyors += [
        ConveyorGeo("RB1/RB2", [(ox + 20, oy + 13.8), (ox + 24.5, oy + 13.8),
                                (ox + 24.5, oy + 11.1)],
                    link_ids=["L-LT-OL1-RB"], tbc=True,
                    compact=True),
        ConveyorGeo("RB3", [(ox + 20, oy + 8.4), (ox + 24.5, oy + 8.4),
                            (ox + 24.5, oy + 11.1)],
                    link_ids=["L-LT-OL2-RB"], tbc=True, compact=True),
        ConveyorGeo("RB4/RB5", [(ox + 24.5, oy + 11.1), (ox + 27, oy + 11.1)],
                    link_ids=["L-LT-RB45-COMMON"],
                    tbc=True, compact=True),
    ]
    g.equipment.append(EquipGeo("junction", ox + 24.5, oy + 11.1))
    g.zones.append(ZoneGeo("WASHING / BENEFICIATION — 2 PARALLEL LINES",
                           ox + 27, oy + 7.9, ox + 43, oy + 15.2,
                           style="stage"))
    g.process_lines += _louta_washing_lines(ox, oy, compact=True)
    # --- 130/140LG product storage (modelled lanes + solver reclaimers) ---------
    g.conveyors.append(ConveyorGeo(
        "SL11", [(ox + 43, oy + 11.1), (ox + 47.3, oy + 11.1)],
        link_ids=["L-LT-SL11-P1", "L-LT-SL11-P2"],
        tbc=True, compact=True))
    g.piles += [
        PileGeo("LT-P1", ox + 48, oy + 11.9, ox + 61, oy + 13.1,
                label="LOU · LT-P1", tbc=True),
        PileGeo("LT-P2", ox + 48, oy + 9.1, ox + 61, oy + 10.3,
                label="LOU · LT-P2", tbc=True),
    ]
    g.rails += [
        RailGeo("140LG-RL1", ox + 47.3, oy + 13.8, ox + 62, kind="reclaimer",
                flanks=["LT-P1"], working_sides="below", label="", tbc=True,
                source=_LOU_PD, confidence=TOPO_TBC),
        RailGeo("130LG-R", ox + 47.3, oy + 11.1, ox + 62, kind="stacker",
                flanks=["LT-P1", "LT-P2"], working_sides="both", label="",
                tbc=True, source=_LOU_PD, confidence=TOPO_TBC),
        RailGeo("140LG-RL2", ox + 47.3, oy + 8.4, ox + 62, kind="reclaimer",
                flanks=["LT-P2"], working_sides="above", label="", tbc=True,
                source=_LOU_PD, confidence=TOPO_TBC),
    ]
    g.machines += [
        MachineGeo("130LG-ST-01", "130LG-R", 0.50, 0.4,
                   kind="stacker", label="LOU · 130LG-ST-01", label_side=1,
                   note="ONE shared product stacker on the common stacking "
                        "rail — serves both product lines · solver-controlled "
                        "product stacking (1,000 t/h S0 envelope). " + _LOU_PD),
        MachineGeo("140LG-RC-01", "140LG-RL1", 0.50, 0.45,
                   label="LOU · 140LG-RC-01", label_side=-1,
                   served_pile="LT-P1",
                   note="3,000 t/h nom · reclaims product line 1 · "
                        "CV-03/RL1 → RL3/RL4 → C1"),
        MachineGeo("140LG-RC-02", "140LG-RL2", 0.50, 0.45,
                   label="LOU · 140LG-RC-02", label_side=-1,
                   served_pile="LT-P2",
                   note="3,000 t/h nom · reclaims product line 2 · "
                        "CV-04/RL2 → RL3/RL4 → C1"),
    ]
    g.zones.append(ZoneGeo("130/140LG PRODUCT", ox + 46, oy + 7.9,
                           ox + 63, oy + 15.2, style="stage", tbc=True))
    g.conveyors += [
        ConveyorGeo("LT-RL1", [(ox + 62, oy + 13.8), (ox + 65, oy + 13.8),
                               (ox + 65, oy + 10.5)],
                    machine_id="140LG-RC-01", compact=True),
        ConveyorGeo("LT-RL2", [(ox + 62, oy + 8.4), (ox + 65, oy + 8.4),
                               (ox + 65, oy + 10.5)],
                    machine_id="140LG-RC-02", compact=True),
        ConveyorGeo("LT-COLLECT", [(ox + 65, oy + 10.5), (ox + 65, oy + 4.2),
                                   (ox + 60, oy + 4.2)],
                    link_ids=["L-LT-P1-OUT", "L-LT-P2-OUT"],
                    tag="RL1/RL2 → RL3/RL4", label_frac=0.6, label_dy=-1.1,
                    compact=True),
    ]
    g.equipment.append(EquipGeo("corridor_out", ox + 57.5, oy + 4.2,
                                "C1", link_ids=["C1"], scale=0.7))
    g.gaps = []          # detailed gaps come from the full Louta geometry
    return g


def chain_geometry(sc) -> SiteGeometry:
    """End-to-end mimic: Louta (mine + beneficiation) and Benguerir converge
    on M16 through the C1/C2 corridor conveyors; PAP + DRY downstream."""
    g = SiteGeometry("CHAIN",
                     "Phosphate value chain — LOUTA + BENGUERIR → MPH M16",
                     bounds=(0, 0, 178, 50.5), not_to_scale=True,
                     source="composite schematic (see per-site sources)")
    # User-confirmed proximity correction (22-Jul-2026): Louta/C1 is nearer
    # to MPH than Benguerir/C2.  This remains a NOT-TO-SCALE schematic, but
    # the relative corridor lengths must not contradict that topology.
    lou = _louta_compact(sc, ox=32, oy=23.5)
    ben = benguerir_geometry(sc, ox=0, oy=0.5, compact=True)
    m16 = _m16_section(sc, ox=112, oy=0.5, compact=True)
    g.gaps += louta_geometry(sc).gaps      # keep the full gap register

    for part in (lou, ben, m16):
        g.piles += part.piles
        g.rails += part.rails
        g.machines += part.machines
        g.conveyors += part.conveyors
        g.equipment += part.equipment
        g.process_lines += part.process_lines
        g.zones += part.zones
        g.chips += part.chips
        g.gaps += part.gaps
    g._display_anchor = getattr(lou, "_display_anchor", {})  # type: ignore
    g.stamps.append(StampGeo(92, 49.3,
                             "END-TO-END MIMIC — SCHEMATIC, NOT TO SCALE"))
    _add_corridors(g, sc, m16, lou, ben)
    g.height_px = 700
    return g


def _add_corridors(g: SiteGeometry, sc, m16: SiteGeometry,
                   lou: SiteGeometry, ben: SiteGeometry):
    """C1/C2 physical corridor conveyors bound to the real delayed links."""
    c1 = next((l for l in sc.links if l.id == "C1"), None)
    c2 = next((l for l in sc.links if l.id == "C2"), None)
    ifs = {e.label: e for e in m16.equipment if e.kind == "corridor_in"}
    e1, e2 = ifs.get("C1 (Louta)"), ifs.get("C2 (Benguerir)")

    def _outlet(part: SiteGeometry, link_id: str):
        return next((e for e in part.equipment
                     if e.kind == "corridor_out" and link_id in e.link_ids), None)

    o1, o2 = _outlet(lou, "C1"), _outlet(ben, "C2")
    if c1 is not None and e1 is not None and o1 is not None:
        bend_x = e1.x - 8
        g.conveyors.append(ConveyorGeo(
            "C1", [(o1.x, o1.y), (bend_x, o1.y), (e1.x - 1.2, e1.y)],
            link_ids=["C1"], tag="C1",
            sub=f"transit {c1.transit_buckets * sc.sim.dt_h:g} h",
            label_frac=0.42, label_dy=2.4))
    if c2 is not None and e2 is not None and o2 is not None:
        bend_x = e2.x - 8
        g.conveyors.append(ConveyorGeo(
            "C2", [(o2.x, o2.y), (bend_x, o2.y), (e2.x - 1.2, e2.y)],
            link_ids=["C2"], tag="C2",
            sub=f"transit {c2.transit_buckets * sc.sim.dt_h:g} h",
            label_frac=0.42, label_dy=-2.4))


# ================================= GLOBAL view =====================================
def global_geometry(sc) -> SiteGeometry:
    """Global Stockyard Layout & Flow — three site zones with recognisable
    compact stockyard plans (Louta top-left, Benguerir bottom-left, MPH M16
    receiving hub right) joined by the C1/C2 corridor conveyors. Same
    primitives, same view-model, larger typography-friendly layout."""
    g = SiteGeometry("GLOBAL",
                     "Global stockyard layout & flow — LOUTA · BENGUERIR · "
                     "MPH M16",
                     bounds=(0, 0, 178, 52.5), not_to_scale=True,
                     source="composite schematic (see per-site sources)")
    lou = _louta_compact(sc, ox=33, oy=25.5)
    ben = benguerir_geometry(sc, ox=1, oy=1.5, compact=True)
    m16 = _m16_section(sc, ox=112, oy=1.0, compact=True)
    for part in (lou, ben, m16):
        g.piles += part.piles
        g.rails += part.rails
        g.machines += part.machines
        g.conveyors += part.conveyors
        g.equipment += part.equipment
        g.process_lines += part.process_lines
        g.zones += part.zones
        g.chips += part.chips
    g._display_anchor = getattr(lou, "_display_anchor", {})  # type: ignore
    g.gaps += louta_geometry(sc).gaps
    g.gaps += [gp for gp in ben.gaps + m16.gaps]
    g.zones.append(ZoneGeo("MPH M16 — Receiving / Stockyard SMS", 111, 0.6,
                           177.5, 49.0))
    _add_corridors(g, sc, m16, lou, ben)
    g.height_px = 740
    return g


def geometry_for(sc, view: str, show_future: bool = False) -> SiteGeometry:
    if view == "MPH":
        return m16_geometry(sc, show_future=show_future)
    return {"CHAIN": chain_geometry, "GLOBAL": global_geometry,
            "LOUTA": louta_geometry,
            "BENGUERIR": benguerir_geometry}[view](sc)


# ============================== geometry validation ================================
ADJ_TOL = 3.5      # schematic adjacency tolerance (drawing units)


def validate_geometry(geo: SiteGeometry) -> List[str]:
    """Checks the machine-to-pile physical configuration:
    - every rail lies between / immediately adjacent to each flanked pile;
    - flanked piles exist;
    - machine home rails exist;
    - rails carry a source + confidence statement (evidence discipline);
    - display-only process-line cards are valid, unique and non-overlapping."""
    issues: List[str] = []
    for p in geo.piles:
        if p.visual_width_factor <= 0:
            issues.append(
                f"{geo.site_id}/{p.pile_id}: visual_width_factor must be "
                "positive")
        if p.display_grade_issues:
            issues.extend(
                f"{geo.site_id}/{p.pile_id}: {issue}"
                for issue in p.display_grade_issues)
        if p.display_grade_components:
            if p.modelled:
                issues.append(
                    f"{geo.site_id}/{p.pile_id}: display grade components "
                    "are only valid for reference/display piles")
            total_fraction = sum(
                component.mass_fraction
                for component in p.display_grade_components)
            if abs(total_fraction - 1.0) > 1e-9:
                issues.append(
                    f"{geo.site_id}/{p.pile_id}: display component mass "
                    f"fractions sum to {total_fraction:g}, expected 1")
            previous = 0.0
            for component in p.display_grade_components:
                if not (0.0 <= component.frac_from < component.frac_to <= 1.0):
                    issues.append(
                        f"{geo.site_id}/{p.pile_id}/"
                        f"{component.component_id}: invalid schematic bounds")
                if abs(component.frac_from - previous) > 1e-9:
                    issues.append(
                        f"{geo.site_id}/{p.pile_id}/"
                        f"{component.component_id}: component bands are not "
                        "contiguous")
                previous = component.frac_to
        if p.display_grade_id and p.modelled:
            issues.append(f"{geo.site_id}/{p.pile_id}: display grade is "
                          "only valid for reference/display piles")
        if p.display_quality and not p.display_grade_id:
            issues.append(f"{geo.site_id}/{p.pile_id}: display quality "
                          "requires a display grade id")
        if p.display_mass_t is None:
            continue
        if p.modelled:
            issues.append(f"{geo.site_id}/{p.pile_id}: display_mass_t is "
                          "only valid for reference/display piles")
        if p.capacity_t is None or p.capacity_t <= 0:
            issues.append(f"{geo.site_id}/{p.pile_id}: display mass requires "
                          "a positive display capacity")
        elif p.display_mass_t < 0 or p.display_mass_t > p.capacity_t:
            issues.append(f"{geo.site_id}/{p.pile_id}: display mass "
                          f"{p.display_mass_t:g} outside 0..{p.capacity_t:g}")
    for r in geo.rails:
        if not r.flanks:
            issues.append(f"{geo.site_id}/{r.rail_id}: no flanked piles")
        for pid in r.flanks:
            p = geo.pile(pid)
            if p is None:
                issues.append(f"{geo.site_id}/{r.rail_id}: flanked pile "
                              f"{pid} missing from geometry")
                continue
            # adjacency: the rail must run immediately above or below the
            # pile band (between banks = adjacent to both)
            d = min(abs(r.y - p.y0), abs(r.y - p.y1))
            inside = p.y0 <= r.y <= p.y1
            depth = max(1, int(getattr(r, "flank_depth", 1)))
            if inside:
                issues.append(f"{geo.site_id}/{r.rail_id}: rail crosses "
                              f"THROUGH pile {pid}")
            elif d > ADJ_TOL + depth * (p.y1 - p.y0):
                issues.append(f"{geo.site_id}/{r.rail_id}: not adjacent to "
                              f"served pile {pid} (dy={d:.1f})")
            # horizontal span must cover the pile slot (machine can reach)
            if r.x0 > p.x0 + 0.6 or r.x1 < p.x1 - 0.6:
                issues.append(f"{geo.site_id}/{r.rail_id}: travel span does "
                              f"not cover pile {pid}")
        if not r.confidence:
            issues.append(f"{geo.site_id}/{r.rail_id}: missing metric/"
                          "topological confidence statement")
    for mg in geo.machines:
        if mg.rail_id and geo.rail(mg.rail_id) is None:
            issues.append(f"{geo.site_id}/{mg.machine_id}: home rail "
                          f"{mg.rail_id} missing")
    seen_lines = set()
    for i, line in enumerate(geo.process_lines):
        if line.line_id in seen_lines:
            issues.append(f"{geo.site_id}/{line.line_id}: duplicate process "
                          "line id")
        seen_lines.add(line.line_id)
        if line.status not in {"OPERATING", "FUTURE"}:
            issues.append(f"{geo.site_id}/{line.line_id}: unsupported process "
                          f"line status {line.status!r}")
        if line.x1 <= line.x0 or line.y1 <= line.y0:
            issues.append(f"{geo.site_id}/{line.line_id}: invalid process "
                          "line bounds")
        xmin, ymin, xmax, ymax = geo.bounds
        if (line.x0 < xmin or line.x1 > xmax or line.y0 < ymin
                or line.y1 > ymax):
            issues.append(f"{geo.site_id}/{line.line_id}: process line lies "
                          "outside geometry bounds")
        if not line.source or not line.evidence_status:
            issues.append(f"{geo.site_id}/{line.line_id}: missing process "
                          "line evidence metadata")
        for other in geo.process_lines[i + 1:]:
            separated = (line.x1 <= other.x0 or other.x1 <= line.x0
                         or line.y1 <= other.y0 or other.y1 <= line.y0)
            if not separated:
                issues.append(f"{geo.site_id}/{line.line_id}: overlaps "
                              f"process line {other.line_id}")
    return issues
