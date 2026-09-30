"""Built-in demonstration scenarios.

Domain A is a THREE-SITE value chain (report Rev D): Louta mine stockyard
feeds the MPH M16 receiving hub through corridor C1, Benguerir feeds it
through C2; the three yards are peer controlled planning sites. S3 models
that complete chain; S2 is an M16-hub-only demonstration whose corridor
arrivals are SIMULATED inputs; S1 is Domain B (M17 sulphur) alone.

Values marked CONFIRMED come from the evidence register of the project report
(Rev D, §16) / CLAUDE-CONTEXT.md. Everything else is an OBVIOUS PLACEHOLDER
tagged ASSUMED (or TBC) and editable in the Input Data page. Placeholders are
never presented as engineering facts — the Assumptions & Data Gaps page lists
every one of them.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Dict, List, Tuple

from .domain import (Assumption, BeltProfileEntry, CalendarEntry, DemandEntry,
                     Destination, Evidence, ExogenousFlow, Grade, Link, Machine,
                     MaterialProfile, Node, ObjectiveWeights, ProcessTransfer,
                     QualityParam, Scenario, Segment, SimParams, Site,
                     SpecRange, StockSpace)
from .grade_ids import (GRADE_ID_ALIASES as LEGACY_RAW_GRADE_ID_ALIASES,
                        RAW_BT_GRADE_IDS, RAW_PENDING_GRADE_ID,
                        canonicalize_scenario_grade_ids)
from .flow_config import (destination_line_operating_state,
                          set_destination_line_operating_state)

# ---- evidence shorthands ---------------------------------------------------
def CONF(src, note=""):
    return Evidence(source=src, status="CONFIRMED", confidence="H", note=note)


def DERV(src, note=""):
    return Evidence(source=src, status="DERIVED", confidence="M", note=note)


def ASSUME(note="placeholder — edit in Input Data"):
    return Evidence(source="placeholder (this MVP)", status="ASSUMED",
                    confidence="L", note=note)


def TBC(src, note=""):
    return Evidence(source=src, status="TBC", confidence="M", note=note)


PHOS_PARAMS = [QualityParam("BPL", "%", "Bone Phosphate of Lime"),
               QualityParam("Cd", "ppm", "cadmium"),
               QualityParam("MgO", "%", "magnesium oxide"),
               QualityParam("SiO2", "%", "silica"),
               QualityParam("CO2", "%", "carbon dioxide"),
               QualityParam("CaO_CO2", "ratio", "CaO/CO2 ratio"),
               QualityParam("moisture", "%", "H2O")]
SULF_PARAMS = [QualityParam("S", "%", "sulphur purity"),
               QualityParam("moisture", "%", "H2O")]

_PRO = "Q80130-MZI-M16-PR-PRO-0001 / PFD-0001[3]"
#: M16 phosphate storage-area flow sheet — the AUTHORITATIVE source for the
#: humid-yard machine/conveyor identities and their alternating relative
#: topology (verified 25-Jul-2026). Metric positions are NOT scaled.
_M16PFD = ("Q80130-MZI-M16-ME-PFD-0001[3] Rev 3, IFU, 28-Oct-2025 — "
           "Phosphate Storage Area flow sheet")
_M16PRO = "Q80130-MZI-M16-PR-PRO-0001 Rev 2, 22-May-2025, §2.1"

# ---------------------------------------------------------------------------
# M16 humid yard: four two-hangar bays with the machine lines BETWEEN them.
#
# PFD-0001[3] sequence along the yard (confirmed by tag order on the drawing):
#     M16A-A-T06 (ST01-A) · bay 1 · M16A-A-T11 (RC01-A) · bay 2 ·
#     M16A-B-T06 (ST01-B) · bay 3 · M16A-B-T11 (RC01-B) · bay 4 ·
#     M16A-C-T06 (ST01-C)   [future: bay 5 · M16A-C-T11 (RC01-C) ·
#                            bay 6 · M16A-D-T06 (ST01-D)]
#
# PROJECT-TEAM RULE (25-Jul-2026): a stacker/reclaimer that translates along a
# machine line between adjacent hangar bays serves those adjacent bays — and
# only those. No machine reaches every hangar.
#
# The PFD does NOT name the hangars: `HP-01…08` is the application's existing
# numbering, retained as an explicit USER-DIRECTED / INFERRED working
# convention pending the plot plan and the official SMS pile-ID register.
# ---------------------------------------------------------------------------
#: solver machine id -> physical tag (PFD-0001[3] equipment list). Single
#: source of the mapping; `ui.layout_geometry` re-exports it for the mimic.
M16_PHYSICAL_TAGS: Dict[str, str] = {
    "ST01-A": "M16A-A-ST01", "ST01-B": "M16A-B-ST01",
    "ST01-C": "M16A-C-ST01",
    "RC01-A": "M16A-A-RC01", "RC01-B": "M16A-B-RC01",
}
M16_BAYS: List[Tuple[str, Tuple[str, ...]]] = [
    ("BAY-1", ("HP-01", "HP-05")),
    ("BAY-2", ("HP-02", "HP-06")),
    ("BAY-3", ("HP-03", "HP-07")),
    ("BAY-4", ("HP-04", "HP-08")),
]
#: solver machine id -> (canonical machine line, served bay numbers, kind)
M16_MACHINE_LINES: Dict[str, Tuple[str, Tuple[int, ...], str]] = {
    "ST01-A": ("M16A-A-T06", (1,), "stacker"),
    "RC01-A": ("M16A-A-T11", (1, 2), "reclaimer"),
    "ST01-B": ("M16A-B-T06", (2, 3), "stacker"),
    "RC01-B": ("M16A-B-T11", (3, 4), "reclaimer"),
    "ST01-C": ("M16A-C-T06", (4,), "stacker"),
}
#: FUTURE continuation of the same sequence (display/reference only — never
#: added to Scenario.machines/spaces by this dataset)
M16_FUTURE_LINES: Dict[str, Tuple[str, Tuple[int, ...], str]] = {
    "RC01-C*": ("M16A-C-T11", (5, 6), "reclaimer"),
    "ST01-D*": ("M16A-D-T06", (6,), "stacker"),
}
M16_FUTURE_BAYS: List[Tuple[str, Tuple[str, ...]]] = [
    ("BAY-5*", ("HP-09*", "HP-11*")),
    ("BAY-6*", ("HP-10*", "HP-12*")),
]
#: reclaim stream -> hangars whose reclaimed material leaves on that stream.
#: Each stream is one reclaimer + its own T11/T11S/T12 conveyors + its own
#: clamshell/rod gate bank; the two streams stay separate to the (TBC)
#: PAP/drying confluence.
M16_RECLAIM_STREAMS: Dict[str, Tuple[str, ...]] = {
    "A": ("HP-01", "HP-05", "HP-02", "HP-06"),
    "B": ("HP-03", "HP-07", "HP-04", "HP-08"),
}
#: The CURRENT reception envelope is ONE 2,600 t/h belt. PRO-0001 §2.1.1
#: places auto sampler M16VP01 on humid-phosphate conveyor M16A-A-T01 "and the
#: Auto Sampler M16VP02 on the adjacent FUTURE conveyor", so M16A-B-T01 is a
#: future duplicate — the two corridors C1 and C2 share one current reception
#: path and cannot aggregate 5,200 t/h today.
M16_RECEPTION_GROUP = "M16A-A-T01"
M16_RECEPTION_TPH = 2600.0
M16_RECEPTION_BASIS = (
    "CURRENT reception is a single 2,600 t/h path (M16A-A-T01 → T02 → T03/T04 "
    "→ the three stacker lines): PRO-0001 §2.1.1 puts auto sampler M16VP01 on "
    "M16A-A-T01 and M16VP02 on 'the adjacent future conveyor', and "
    "PFD-0001[3] marks that adjacent line FUTURE. C1 and C2 therefore share "
    "one reception envelope — aggregate current reception is capped at "
    "2,600 t/h, never 2 × 2,600 t/h.")

M16_HP_NUMBERING_BASIS = (
    "USER-DIRECTED / INFERRED: PFD-0001[3] shows eight 125,000 t hangars in "
    "four two-hangar bays but does not name them. HP-01…08 is the "
    "application's working convention (bay 1 = HP-01/HP-05, bay 2 = "
    "HP-02/HP-06, bay 3 = HP-03/HP-07, bay 4 = HP-04/HP-08), pending the "
    "plot plan and the official SMS pile-ID mapping."
)


def m16_bay_of(space_id: str) -> int:
    """1-based bay number of an M16 hangar (0 when not an M16 hangar)."""
    for index, (_, piles) in enumerate(M16_BAYS, start=1):
        if space_id in piles:
            return index
    return 0


def m16_reachable_spaces(machine_id: str) -> List[str]:
    """Physically reachable hangars of an M16 machine (bay-adjacency rule)."""
    _, bays, _ = M16_MACHINE_LINES[machine_id]
    return [pile for index, (_, piles) in enumerate(M16_BAYS, start=1)
            if index in bays for pile in piles]


def m16_stream_of(space_id: str) -> str:
    """Reclaim stream ('A'/'B') that carries this hangar's reclaimed material."""
    for stream, piles in M16_RECLAIM_STREAMS.items():
        if space_id in piles:
            return stream
    return ""
_PFD4 = "Q80130-MZI-M16-PR-PRO-0001 / PFD-0004[3]"
_FRS = "TAKRAF FRS Q80130-MZI-M16-TAK-45000183621-DG"
#: Benguerir Beneficiation Expansion mechanical Basis of Design — the
#: AUTHORITATIVE Benguerir source since 18-Jul-2026 (replaces the former
#: unreferenced "conveying-system datasheet, doc ref TBC")
_B02 = ("Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, "
        "§§2.3.1–2.3.3 and §2.6")
_B01 = "Q80130-BEN-B01-PR-REP-0002 Rev B, 26-Jul-2023 (IFR)"
_LOU_DB = "L02 design basis / handling philosophy (doc refs TBC)"
_LOU_BOD = ("Q80130-LOU-L02-PR-BOD-0001 Rev B, 12-Apr-2023, "
            "§§1.2 and 6.1–6.3")
#: Louta Beneficiation Plant process description — the equipment-tagged
#: authoritative Louta process source (verified 25-Jul-2026).  The document's
#: own control block is inconsistent (cover REP-00002 vs body header
#: REP-00004); both numbers are recorded, neither silently preferred.
_LOU_PD = ("Louta Beneficiation Plant Process description, Rev B (IFA), "
           "25-Apr-2023, §3.1 pp. 5–8 — document number inconsistent in the "
           "source: cover Q80130-LOU-L02-PR-REP-00002 vs body header "
           "Q80130-LOU-L02-PR-REP-00004")
#: Louta Mine L01 source newly inspected 29-Jul-2026. The filename/revision
#: table says Rev 3 (15-Sep-2025), while the cover field still says Rev 2.
_LOU_L01_PD = (
    "Q80130-LOU-L01-PR-REP-0002[3].pdf, PDF p.11 — two storage stockpiles, "
    "dedicated stackers 102MG-ST-01/-02 at 2,200 t/h nominal, shared "
    "reclaimer 110MG-RC-01 and reclaim conveyor 110MG-CB-01"
)
_LOUTA_PRODUCT_CAPACITY_T = 191_512.0
_LOUTA_PRODUCT_START_MASS_T = 95_756.0
# Stable display/config keys only. The L01 source uses physical
# `102MG-SP-01/-02` tags for samplers, so the mimic renders the stockpiles as
# MINE STOCKPILE-01/-02 instead of exposing these keys as equipment tags.
_LOUTA_MINE_LANE_IDS = ("102MG-SP-01", "102MG-SP-02")
_LOUTA_MINE_DEMO_CAPACITY_T = 100_000.0
_LOUTA_MINE_DEMO_START_MASS_T = 50_000.0
_LOUTA_MINE_DISPLAY_GRADE_COMPONENTS = {
    "102MG-SP-01": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-GLOBAL"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-HIGH-CD"},
    ],
    "102MG-SP-02": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-GLOBAL"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-LOW-CD"},
    ],
}
#: Homogenized ore storage (110/120LG). The Louta BoD Rev B confirms the TOTAL
#: only; the equal two-pile split is DERIVED and the 50% start is an ASSUMED
#: demonstration baseline. These are solver StockSpace rows in S0; the
#: profile-band mix below remains an assumed display composition while the
#: optimiser uses each pile's explicit aggregate/designated grade.
_LOUTA_ORE_TOTAL_CAPACITY_T = 494_274.0
_LOUTA_ORE_LANE_IDS = ("110LG-OL1", "110LG-OL2")
_LOUTA_ORE_LANE_CAPACITY_T = _LOUTA_ORE_TOTAL_CAPACITY_T / 2      # 247,137 t
_LOUTA_ORE_LANE_START_MASS_T = _LOUTA_ORE_LANE_CAPACITY_T / 2     # 123,568.5 t
_LOUTA_ORE_DISPLAY_GRADE_COMPONENTS = {
    "110LG-OL1": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-GLOBAL"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-HIGH-CD"},
    ],
    "110LG-OL2": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-GLOBAL"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-LOW-CD"},
    ],
}
# Documented Louta washing design cases (BoD Rev B pp.19, 21, 23, 27).
# Feed is on an ore/wet basis; recovery is total merchant-product mass.
_LOUTA_WASH_CASES = {
    "GLOBAL": {"feed_tph": 584.0, "recovery": 0.6822},
    "HIGH_CD": {"feed_tph": 579.0, "recovery": 0.6883},
    "LOW_CD": {"feed_tph": 588.0, "recovery": 0.6771},
}
_LOUTA_S0_C1_TARGET_T = 8_000.0
_LOUTA_S0_LINE_TARGET_T = {
    "LOU-WASH-L1": 4_000.0,
    "LOU-WASH-L2": 4_000.0,
}
# Exact S0 process inputs that produce the configured 4,000 t recovered
# output per line under the integer, per-bucket recovery model. These are
# campaign feed requirements, not the 584/588 t/h BoD design-case rates.
_LOUTA_S0_LINE_FEED_TARGET_T = {
    "LOU-WASH-L1": 5_867.0,
    "LOU-WASH-L2": 5_914.0,
}
# Benguerir S0 uses the same raw-grade campaign cases as Louta only as an
# explicit simulation assumption.  The Benguerir documents do not provide
# line-specific recoveries or a grade-to-line/product mapping.
_BEN_WASH_CASES = {
    "BEN-WASH-L1": {
        "feed_t": 2_640.0, "output_t": 1_800.0,
        "feed_tph": _LOUTA_WASH_CASES["GLOBAL"]["feed_tph"],
        "feed_grade": "RAW-BT-GLOBAL", "output_grade": "TBT",
        "recovery": _LOUTA_WASH_CASES["GLOBAL"]["recovery"],
    },
    "BEN-WASH-L2": {
        "feed_t": 2_662.0, "output_t": 1_800.0,
        "feed_tph": _LOUTA_WASH_CASES["LOW_CD"]["feed_tph"],
        "feed_grade": "RAW-BT-LOW-CD", "output_grade": "BT",
        "recovery": _LOUTA_WASH_CASES["LOW_CD"]["recovery"],
    },
    "BEN-WASH-L3": {
        "feed_t": 2_640.0, "output_t": 1_800.0,
        "feed_tph": _LOUTA_WASH_CASES["GLOBAL"]["feed_tph"],
        "feed_grade": "RAW-BT-GLOBAL", "output_grade": "TBT",
        "recovery": _LOUTA_WASH_CASES["GLOBAL"]["recovery"],
    },
}
_BEN_PHYSICAL_PILE_CAPACITY_T = 200_000.0
_BEN_PHYSICAL_PILE_START_MASS_T = 100_000.0
_BEN_HOPPER_INTERFACE_T = 10_000.0
_BEN_PROCESS_STAGE_IDS = {
    line_id: (
        f"BEN-MINE-STACK-L{line_id[-1]}",
        f"BEN-MINE-RECLAIM-L{line_id[-1]}",
        f"BEN-RAW-STACK-L{line_id[-1]}",
        line_id,
        f"BEN-PRODUCT-STACK-L{line_id[-1]}",
    )
    for line_id in _BEN_WASH_CASES
}
# Operator-facing S0 preset selected for every fresh built-in scenario.
# Project-team request (29-Jul-2026): default to the connected full-flow
# exercise so the generated schedule gives genuine work to the broadest
# possible set of currently eligible machines.
DEFAULT_PLANNING_DEMAND_PROFILE = "FULL_FLOW"
PLANNING_DEMAND_PROFILES = {
    "BALANCED": {
        "label": "Balanced production",
        "description": (
            "MPH demand and the Louta stocked-route campaign only. "
            "Benguerir product reclaimers remain standby."
        ),
        "bg_train_product_t": 0.0,
        "c2_dispatch_t": 0.0,
        "bg_process_active": False,
        "louta_process_active": True,
    },
    "BG_TRAIN": {
        "label": "Benguerir train campaign",
        "description": (
            "Runs all three Benguerir mine-to-product washing campaigns and "
            "an assumed 12,000 t washed-product train order. Louta processing "
            "is paused so this preset stays fast and site-focused."
        ),
        "bg_train_product_t": 12_000.0,
        "c2_dispatch_t": 0.0,
        "bg_process_active": True,
        "louta_process_active": False,
    },
    "FULL_FLOW": {
        "label": "Full-flow all-machine demonstration",
        "description": (
            "Runs both mine processing chains and MPH with 24,000 t of "
            "Benguerir train demand and a 10,600 t C2 allocation. These "
            "clean demonstrated quantities exercise all 20 current "
            "solver-controlled machines across Louta, Benguerir and MPH "
            "without exceeding M16 stacking acceptance."
        ),
        "bg_train_product_t": 24_000.0,
        "c2_dispatch_t": 10_600.0,
        "bg_process_active": True,
        "louta_process_active": True,
    },
}
_LOUTA_DIRECT_MINE_FEED_T = 10_000.0
_LOUTA_PRODUCT_STACKER_S0_TPH = 1_000.0
_LOUTA_PRODUCT_GRADE_DISTRIBUTIONS = {
    "LT-P1": [
        {"component_id": "C1", "mass_fraction": 0.6, "grade_id": "TBT"},
        {"component_id": "C2", "mass_fraction": 0.4, "grade_id": "BT"},
    ],
    "LT-P2": [
        {"component_id": "C1", "mass_fraction": 0.6, "grade_id": "BT"},
        {"component_id": "C2", "mass_fraction": 0.4, "grade_id": "MT"},
    ],
}
_LOUTA_PRODUCT_COMPOSITE_QUALITY = {
    "LT-P1": {
        "BPL": 65.0,
        "Cd": 33.0,
        "MgO": 1.28,
        "SiO2": 3.22,
        "moisture": 12.0,
    },
    "LT-P2": {
        "BPL": 70.4,
        "Cd": 26.8,
        "MgO": 0.98,
        "SiO2": 2.48,
        "moisture": 12.0,
    },
}

# Corridor proximity correction (project team, 22-Jul-2026): C1/Louta is the
# nearer mine and C2/Benguerir is the farther mine.  The workspace does not
# contain the TBE/datasheet that proves the legacy 30/60 km pair, so the
# numeric reassignment below is a transparent modelling assumption pending
# route-length/effective-speed/transfer-dwell evidence.  The qualitative
# ordering is user-confirmed and regression-tested.
CORRIDOR_DISTANCE_KM = {"C1": 30, "C2": 60}
CORRIDOR_TRANSIT_BUCKETS = {"C1": 2, "C2": 4}
CORRIDOR_SPEED_MPS = 4.0
_CORRIDOR_CORRECTION = ("project-team topology correction, 22-Jul-2026; "
                        "exact route lengths/speeds TBC")
S3_SCENARIO_NAME = (
    "S3 — Three-site phosphate value chain: Louta + Benguerir → M16"
)
S2_SCENARIO_NAME = "S2 — MPH M16 hub only (simulated corridor arrivals)"
# Streamlit preserves dataclass instances in session_state across hot reloads.
# Increment this stamp whenever a material built-in S3 baseline correction must
# invalidate/migrate an already-loaded in-memory scenario.
#: One revision stamp for BOTH built-in phosphate scenarios: S2 and S3 share
#: the M16 yard, so a corrected M16 must invalidate a hot-reloaded S2 session
#: exactly as it invalidates S3.
BUILTIN_BASELINE_REVISION = (
    "2026-07-29-pap2-future-option-v24"
)
#: backwards-compatible alias (demo-bundle cache key + existing tests)
S3_BASELINE_REVISION = BUILTIN_BASELINE_REVISION

# Project-team demonstration assumption, 26-Jul-2026. Louta applies it as an
# explicit minimum process dependency in S3. Benguerir now applies the same
# dependency principle to its three executable washing campaigns.
WASHING_LINE_RESIDENCE_TIME_H = {
    "LOUTA": 0.5,
    "BENGUERIR": 0.5,
}
WASHING_LINE_RESIDENCE_TIME_BASIS = (
    "ASSUMED by project team, 26-Jul-2026: 30 min overall residence per "
    "washing line at Louta and Benguerir. Louta applies this as a minimum "
    "feed-to-product dependency; its hourly material balance may place the "
    "downstream stacking job later, never earlier. Benguerir retains the "
    "same minimum feed-to-product dependency. Documented component evidence "
    "remains separate: Louta scrubber "
    "2.5–3 min (Q80130-LOU-L02-PR-BOD-0001 Rev B §8.2.1 p.21); Benguerir "
    "scrubber 3 min (Q80130-BEN-B02-PR-BOD-0001 Rev 0 §7.1 p.19 and "
    "§7.3.1 p.25)."
)


# =============================================================================
# 1. Minimal sulphur scenario (Domain B — MPH M17)
# =============================================================================
def sulphur_minimal() -> Scenario:
    sc = Scenario(
        name="S1 — Sulphur minimal (M17)",
        description="Domain B closed-storage reclaim to melting. Confirmed: "
                    "storage sizes, RC01 1100 tph, payloaders 300 tph manual, "
                    "8 start-stops/h. Demand & qualities are ASSUMED placeholders.",
        quality_params=SULF_PARAMS)
    sc.sites = [Site("MPH", "M'zinda Phosphate Hub", "hub", evidence=CONF(_PRO),
             same_pile_concurrency_allowed=True,
             concurrency_evidence="USER_CONFIRMED (project team, 15-Jul-2026): "
                                  "stacking and reclaiming may occur "
                                  "simultaneously on the same stockpile; the "
                                  "scheduler prevents collisions/overlapping "
                                  "work zones, never same-pile concurrency "
                                  "categorically. Numerical separation limits "
                                  "TBC (demonstration values ASSUMED)")]
    sc.grades = [Grade("S-STD", "Solid sulphur (standard)",
                       {"S": 99.5, "moisture": 0.5}, evidence=ASSUME(
                           "purity/moisture placeholder — no sulphur assay on file"))]
    sc.spaces = [
        StockSpace("M17-OPEN-1", "MPH", "M17-open", "open", 100_000, 60_000,
                   "S-STD", state="mixed", x_from=0, x_to=400,
                   evidence=CONF(_PFD4, "capacity 2×100,000 t confirmed; "
                                        "initial mass ASSUMED")),
        StockSpace("M17-OPEN-2", "MPH", "M17-open", "open", 100_000, 40_000,
                   "S-STD", state="mixed", x_from=0, x_to=400,
                   evidence=CONF(_PFD4, "capacity confirmed; initial mass ASSUMED")),
        StockSpace("M17-CLOSED-1", "MPH", "M17-closed", "closed", 100_000, 55_000,
                   "S-STD", state="mixed", x_from=0, x_to=350,
                   safety_stock_t=20_000,
                   evidence=CONF(_PFD4, "capacity confirmed; initial mass & safety "
                                        "stock ASSUMED")),
    ]
    sc.machines = [
        Machine("M17-RC01", "MPH", "reclaimer", 200, 1100,
                reachable_spaces=["M17-CLOSED-1"], rail="closed-store",
                start_stop_limit_per_h=8, position_x=100,
                evidence=CONF(_PFD4, "1100 tph confirmed; min stable 200 ASSUMED")),
        Machine("M17-ST01", "MPH", "stacker", 300, 2500,
                reachable_spaces=["M17-OPEN-1", "M17-OPEN-2"], rail="open-store",
                evidence=CONF(_PFD4, "open-store stacker; tripper ≈2500 tph")),
    ]
    sc.nodes = [
        Node("SPACE:M17-CLOSED-1", "MPH", "space", "Closed store", 1, 1),
        Node("DEST:MOLTEN-S", "MPH", "dest", "Sulphur melting units", 2, 1),
    ]
    sc.links = [
        Link("L-M17-RECLAIM", "SPACE:M17-CLOSED-1", "DEST:MOLTEN-S",
             capacity_tph=1100, min_stable_tph=100,
             evidence=CONF(_PFD4, "closed reclaim ≈1100 tph + lime dosing")),
    ]
    sc.destinations = [
        Destination("MOLTEN-S", "MPH", "DEST:MOLTEN-S", priority=1,
                    demand_default_tph=800,
                    requested_grade="S-STD",
                    spec=[SpecRange("moisture", pmax=2.0, hard=False)],
                    evidence=ASSUME("melting demand 800 tph & moisture spec "
                                    "are placeholders")),
    ]
    # manual payloaders: exogenous open -> closed transfer (outside automated scope)
    sc.exogenous = [ExogenousFlow("PAYLOAD-1", "M17-CLOSED-1", 300, "S-STD",
                                  source_space="M17-OPEN-1",
                                  evidence=CONF(_PRO, "payloaders 300 tph, manual — "
                                                      "exogenous, not commanded"))]
    sc.calendars = [CalendarEntry("M17-RC01", 10, 11, "planned maintenance (demo)")]
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=24, frozen_buckets=2, seed=42)
    sc.weights = ObjectiveWeights()
    sc.assumptions = [
        Assumption("S1-A1", "Melting-unit demand = 800 tph constant", "M17/demand",
                   source="placeholder", placeholder="800 tph",
                   impact="drives whole plan", action="obtain melting feed plan"),
        Assumption("S1-A2", "Sulphur purity/moisture vector (99.5 % S / 0.5 % H2O)",
                   "M17/quality", source="placeholder", placeholder="S-STD grade",
                   impact="moisture spec compliance", action="reception assay data"),
        Assumption("S1-A3", "Initial pile masses (60/40/55 kt)", "M17/inventory",
                   source="placeholder", placeholder="see spaces table",
                   impact="autonomy calculations", action="SMS SpaceValue snapshot"),
        Assumption("S1-A4", "8 start-stops/h is sub-bucket behaviour: with 1 h "
                   "buckets the model can only penalise job switching (w5), not "
                   "enforce the intra-hour limit", "M17/constraints",
                   source="report §15.4", placeholder="w5 penalty",
                   impact="dispatcher-level constraint deferred",
                   action="enforce in sub-hourly dispatcher iteration"),
    ]
    return sc


# =============================================================================
# 2. MPH phosphate scenario (M16 humid yard)
# =============================================================================
def _phos_grades(include_shared_raw_bt: bool = False) -> List[Grade]:
    grades = [
        Grade("TBT", "TBT — demonstration quality vector; assay TBC",
              {"BPL": 63.0, "Cd": 35.0, "MgO": 1.4, "SiO2": 3.5, "moisture": 12.0},
              rock_value=65.0,
              material_class="washed",
              evidence=ASSUME(
                  "simulation-only vector; corrected to the BoD semantic "
                  "order TBT < BT < MT; numeric assays still require the "
                  "project Grade/SQP register")),
        Grade("BT", "BT — demonstration quality vector; assay TBC",
              {"BPL": 68.0, "Cd": 30.0, "MgO": 1.1, "SiO2": 2.8, "moisture": 12.0},
              rock_value=80.0,
              material_class="washed",
              evidence=ASSUME(
                  "simulation-only vector; corrected TBT < BT < MT semantic "
                  "order; numeric assay remains TBC")),
        Grade("MT", "MT — demonstration quality vector; assay TBC",
              {"BPL": 74.0, "Cd": 22.0, "MgO": 0.8, "SiO2": 2.0, "moisture": 12.0},
              rock_value=100.0, material_class="washed",
              evidence=ASSUME(
                  "simulation-only vector; corrected to the BoD semantic "
                  "order TBT < BT < MT; numeric assay remains TBC")),
        Grade(RAW_PENDING_GRADE_ID,
              "Raw/screened phosphate — chemical grade TBC",
              {}, material_class="raw_screened",
              evidence=TBC("mine assay register not received",
                           "placeholder classification only; never use the "
                           "washed TBT/BT/MT vectors for raw ore")),
    ]
    if include_shared_raw_bt:
        # Shared raw BT grade catalogue. The chemistry is currently sourced
        # from the Louta BoD, while the project team confirmed on 28-Jul-2026
        # that the same grade identities apply at Benguerir. Site-specific
        # physical properties and live pile assays stay on MaterialProfile
        # and inventory records rather than being encoded in the grade ID.
        grades += [
            Grade(
                "RAW-BT-GLOBAL", "Raw BT — Global profile",
                {"BPL": 56.06, "Cd": 21.0, "MgO": 1.49, "SiO2": 13.71,
                 "CO2": 8.21, "moisture": 15.0},
                material_class="raw_screened",
                evidence=CONF(
                    _LOU_BOD + " + project-team clarification 28-Jul-2026",
                    "§6.1.1 pp.14–15 declared average raw-ore profile; "
                    "shared Louta/Benguerir grade identity confirmed by the "
                    "project team; chemistry is the current design vector, "
                    "not a live pile assay; site moisture remains separate")),
            Grade(
                "RAW-BT-HIGH-CD", "Raw BT — High-Cd profile",
                {"BPL": 55.11, "Cd": 25.0, "MgO": 1.50, "SiO2": 16.28,
                 "CO2": 8.41, "moisture": 15.0},
                material_class="raw_screened",
                evidence=CONF(
                    _LOU_BOD + " + project-team clarification 28-Jul-2026",
                    "§6.1.2 p.14 declared average raw-ore profile; "
                    "shared Louta/Benguerir grade identity confirmed by the "
                    "project team; not a live pile assay")),
            Grade(
                "RAW-BT-LOW-CD", "Raw BT — Low-Cd profile",
                {"BPL": 58.28, "Cd": 13.0, "MgO": 1.13, "SiO2": 10.22,
                 "CO2": 8.75, "moisture": 15.0},
                material_class="raw_screened",
                evidence=CONF(
                    _LOU_BOD + " + project-team clarification 28-Jul-2026",
                    "§6.1.3 pp.14–15 declared average raw-ore profile; "
                    "shared Louta/Benguerir grade identity confirmed by the "
                    "project team; not a live pile assay")),
        ]
    return grades


def _phos_material_profiles(include_mines: bool) -> List[MaterialProfile]:
    """Declared material states; chemical gaps remain explicit, never inferred."""
    profiles = [MaterialProfile(
        "M16-WASHED-HUMID", "MPH", "M16 reception / humid storage",
        "washed", "Washed humid phosphate received from C1/C2",
        grade_ids=["TBT", "BT", "MT"],
        quality_ranges={
            "BPL": "per live grade/SQP assay — approved cut-offs TBC",
            "Cd": "per live grade/SQP assay — approved cut-offs TBC",
            "MgO": "per live grade/SQP assay — approved cut-offs TBC",
            "SiO2": "per live grade/SQP assay — approved cut-offs TBC",
            "CaO_CO2": "per reception PGNAA — cut-offs TBC",
            "moisture": "per microwave analyser — current 12% vector is ASSUMED",
        },
        physical_properties={"state": "humid washed phosphate"},
        transformation_note="M16 receives washed product; no raw/screened ore "
                            "may use this profile. Live SMS/SQMS values override "
                            "the demonstration vectors.",
        evidence=TBC("TEN-0002 [B] + SMS SpaceValue interface",
                     "parameter list/analyser location documented; grade "
                     "cut-offs and mine-specific assay profiles not received"))]
    if not include_mines:
        return profiles
    profiles += [
        MaterialProfile(
            "BEN-RAW-SCREENED", "BENGUERIR",
            "screened-product ore storage / pre-beneficiation", "raw_screened",
            "Benguerir screened mine phosphate (<15 mm)",
            grade_ids=[*RAW_BT_GRADE_IDS, RAW_PENDING_GRADE_ID],
            quality_ranges={
                "BPL": "55.11–58.28 % across shared raw BT grades; live pile assay TBC",
                "Cd": "13–25 ppm across shared raw BT grades; live pile assay TBC",
                "MgO": "1.13–1.50 % across shared raw BT grades; live pile assay TBC",
                "SiO2": "10.22–16.28 % across shared raw BT grades; live pile assay TBC",
                "CO2": "8.21–8.75 % across shared raw BT grades; live pile assay TBC",
                "CaO_CO2": "TBC — mine assay table required",
                "moisture": "10–14 % H2O",
            },
            physical_properties={"PSD": "<15 mm", "bulk_density": "1.2–1.4 t/m3",
                                 "angle_of_repose": "30–35°", "behaviour": "abrasive"},
            transformation_note="Raw/screened ore profile before washing; never "
                                "substitute the washed-product grade vector. "
                                "The shared raw-grade taxonomy applies at both "
                                "mines; no live Benguerir pile assay is loaded.",
            evidence=TBC("Table 20 + " + _B02 + " + " + _LOU_BOD,
                         "Benguerir physical state documented; project team "
                         "confirmed the shared grade taxonomy 28-Jul-2026; "
                         "current Benguerir pile assays remain absent")),
        MaterialProfile(
            "BEN-WASHED-FILTERED", "BENGUERIR",
            "after filtration / before settling", "washed",
            "Benguerir filtered washed phosphate",
            grade_ids=["TBT", "BT", "MT"],
            quality_ranges={
                "BPL": "TBC — washed-product assay table required",
                "Cd": "TBC — washed-product assay table required",
                "MgO": "TBC — washed-product assay table required",
                "SiO2": "TBC — washed-product assay table required",
                "CaO_CO2": "TBC — washed-product assay table required",
                "moisture": "23 % H2O",
            },
            physical_properties={"bulk_density": "1.2–1.4 t/m3",
                                 "angle_of_repose": "35°", "behaviour": "abrasive"},
            transformation_note="Intermediate material state; not a modelled "
                                "stock space in the MVP.",
            evidence=TBC(_B02, "physical state documented; chemical assay "
                                  "ranges and grade cut-offs not received")),
        MaterialProfile(
            "BEN-SETTLED-PRODUCT", "BENGUERIR",
            "after settling / washed-product storage", "washed",
            "Benguerir settled washed product to C2/train",
            grade_ids=["TBT", "BT", "MT"],
            quality_ranges={
                "BPL": "TBC — washed-product assay table required",
                "Cd": "TBC — washed-product assay table required",
                "MgO": "TBC — washed-product assay table required",
                "SiO2": "TBC — washed-product assay table required",
                "CaO_CO2": "TBC — washed-product assay table required",
                "moisture": "15–20 % H2O",
            },
            physical_properties={"bulk_density": "1.2–1.4 t/m3",
                                 "angle_of_repose": "35°", "behaviour": "abrasive"},
            transformation_note="Profile used by BG-P1/BG-P2. TBT/BT/MT "
                                "vectors remain demonstration placeholders "
                                "until the washed-product grade register arrives.",
            evidence=TBC(_B02, "physical state documented; chemical assay "
                                  "ranges, grade cut-offs and pile allocation TBC")),
        MaterialProfile(
            "LOU-MINE-PHOSPHATE", "LOUTA", "mine storage / pre-beneficiation",
            "raw_screened", "Louta mine phosphate",
            grade_ids=list(RAW_BT_GRADE_IDS),
            quality_ranges={
                "BPL": "55.11–58.28 % across Global/High-Cd/Low-Cd",
                "Cd": "13–25 ppm across Global/High-Cd/Low-Cd",
                "MgO": "1.13–1.50 %",
                "SiO2": "10.22–16.28 %",
                "CO2": "8.21–8.75 %",
                "CaO_CO2": "TBC — source provides CO2, not CaO/CO2",
                "moisture": "15 % (Ref 8)",
            },
            physical_properties={"F100": "10 mm", "F80": "400–550 µm",
                                 "specific_gravity": "2.9 (calculated)"},
            transformation_note="Three documented raw BT Cd-profile cases. "
                                "The §6.2 PSD-reconstituted totals remain a "
                                "separate testwork dataset.",
            evidence=CONF(_LOU_BOD, "§6.1 pp.14–15; three raw BT ore cases")),
        MaterialProfile(
            "LOU-WASHED-PRODUCT", "LOUTA", "product storage / C1 feed",
            "washed", "Louta washed phosphate product to C1",
            grade_ids=["TBT", "BT", "MT"],
            quality_ranges={
                "BPL": "68.29 % combined product",
                "Cd": "17 ppm combined product",
                "MgO": "<0.6 % combined product limit",
                "SiO2": "<4 % combined product limit",
                "CO2": "6.81 % combined product",
                "CaO_CO2": "TBC — source provides CO2, not CaO/CO2",
                "moisture": "<22 % combined product limit",
            },
            physical_properties={"specific_gravity": "3 (calculated)",
                                 "PSD": "TBC"},
            transformation_note="§6.3 is the combined wash + flotation "
                                "product, not a wash-only assay. Profile used "
                                "by LT-P1/LT-P2; mapping to TBT/BT/MT remains "
                                "a demonstration placeholder.",
            evidence=TBC(_LOU_BOD, "§6.3 p.18 combined-product vector is "
                                   "documented; operational grade mapping and "
                                   "cut-offs remain TBC")),
    ]
    return profiles


def _mph_m16(sc: Scenario, with_pseudo_corridor: bool) -> None:
    """Populate MPH M16 humid yard entities into scenario sc."""
    grades = {"HP-01": ("TBT", 80_000), "HP-02": ("BT", 90_000),
              "HP-03": ("MT", 70_000), "HP-04": ("TBT", 60_000),
              "HP-05": ("TBT", 74_000), "HP-06": ("BT", 50_000),
              "HP-07": ("BT", 0), "HP-08": ("TBT", 0)}
    for i in range(1, 9):
        sid = f"HP-{i:02d}"
        g, mass = grades[sid]
        sc.spaces.append(StockSpace(
            sid, "MPH", "M16-humid", "open", 125_000, mass, g,
            state="empty" if mass == 0 else "mixed",
            designated_grade=g if mass == 0 else "",
            x_from=0, x_to=300,
            safety_stock_t=15_000 if sid in ("HP-02", "HP-05") else 0,
            material_class="washed", profile_id="M16-WASHED-HUMID",
            evidence=CONF(_PRO, "8×125,000 t confirmed; contents/grades ASSUMED; "
                                "x-range geometry ASSUMED (no plot plan)")))
        sc.nodes.append(Node(f"SPACE:{sid}", "MPH", "space", sid, 2, i))
    all_spaces = [f"HP-{i:02d}" for i in range(1, 9)]
    # ---- machines: one machine line each, serving only its adjacent bays ----
    # PFD-0001[3] confirms three stacker lines (M16A-A/B/C-T06, 2,600 t/h) and
    # two reclaim lines (M16A-A/B-T11, 2,500 t/h) INTERLEAVED with the four
    # two-hangar bays. Reachability follows the project-team bay-adjacency
    # rule — never all-to-all.
    _rates = {"stacker": 2600.0, "reclaimer": 2500.0}
    for mid, (line, bays, kind) in M16_MACHINE_LINES.items():
        reach = m16_reachable_spaces(mid)
        bay_txt = " + ".join(f"bay {b} ({'/'.join(M16_BAYS[b - 1][1])})"
                             for b in bays)
        sc.machines.append(Machine(
            mid, "MPH", kind, 500, _rates[kind], reach, rail=line,
            position_x=150, physical_tag=M16_PHYSICAL_TAGS[mid],
            evidence=CONF(
                _M16PFD,
                f"{M16_PHYSICAL_TAGS[mid]} on machine line {line} at "
                f"{_rates[kind]:,.0f} t/h — identity, line and relative "
                f"position CONFIRMED. Physical reach applied by the "
                f"project-team bay-adjacency rule (25-Jul-2026): {bay_txt}. "
                f"Hangar numbering inside the bays is INFERRED "
                f"(see the HP-numbering assumption); metric rail positions "
                f"remain schematic — the PFD is not to scale.")))
    sc.nodes += [
        Node("TOWER:M16-REC", "MPH", "tower", "M16 reception tower", 1, 4),
        Node("DEST:PAP-1", "MPH", "dest", "PAP line 1 (wet)", 4, 3),
        Node("DEST:PAP-2", "MPH", "dest", "PAP line 2 (wet, future)", 4, 4),
        Node("DEST:DRY-FEED", "MPH", "dest", "Drying-plant feed (OOS plant)", 4, 5),
    ]
    # Two SEPARATE reclaim streams — never one merged reclaim hub.
    for stream in ("A", "B"):
        sc.nodes.append(Node(
            f"HUB:M16-RCL-{stream}", "MPH", "gate",
            f"M16A-{stream}-T11 reclaim stream → M16A-{stream}-T11S magnetic "
            f"separator → M16A-{stream}-T12 (belt scale M16A-{stream}-T12W) → "
            f"hopper + clamshell/rod gate bank M16A-{stream}-H01…H04 → "
            f"M16A-{stream}-T13", 3, 3 if stream == "A" else 5))
    for sid in all_spaces:
        bay = m16_bay_of(sid)
        stackers = [mid for mid in M16_MACHINE_LINES
                    if sid in m16_reachable_spaces(mid)
                    and M16_MACHINE_LINES[mid][2] == "stacker"]
        sc.links.append(Link(
            f"L-STK-{sid}", "TOWER:M16-REC", f"SPACE:{sid}",
            capacity_tph=2600, shared_group=M16_RECEPTION_GROUP,
            evidence=CONF(_M16PFD + " + " + _M16PRO + ".1",
                          f"reception T01/T02 → T03/T04 diverter chain → the "
                          f"stacker line(s) reaching bay {bay}: "
                          f"{', '.join(stackers)} (2,600 t/h). Which stacker "
                          f"may use this leg is enforced by machine "
                          f"reachability, not by the link. "
                          + M16_RECEPTION_BASIS)))
        stream = m16_stream_of(sid)
        sc.links.append(Link(
            f"L-RCL-{sid}", f"SPACE:{sid}", f"HUB:M16-RCL-{stream}",
            capacity_tph=2500,
            evidence=CONF(_M16PFD,
                          f"bay {bay} is reclaimed onto the {stream} stream "
                          f"(M16A-{stream}-T11, 2,500 t/h) — the A and B "
                          f"streams stay physically separate")))
    # ---- discharge equipment (CONFIRMED) vs route selection (DERIVED) ------
    # PFD-0001[3] confirms the physical chain per reclaim stream:
    #     T11 → T11S separator → T12 (belt scale T12W) → hopper M16A-A-R01
    #     with air blasters → clamshell + rod gates M16A-{s}-H01…H04 → T13.
    # It does NOT confirm that the H gates themselves select PAP versus
    # drying, so the destination choice is modelled SEPARATELY as a downstream
    # logical route/diverter abstraction with DERIVED/TBC evidence. Confirmed
    # belt ratings never make the ROUTE assignment confirmed.
    _BANK_NOTE = (
        "CONFIRMED physical discharge equipment (PFD-0001[3]): reclaim stream "
        "{s} passes M16A-{s}-T11 → T11S magnetic separator → M16A-{s}-T12 "
        "(belt scale M16A-{s}-T12W) → hopper M16A-A-R01 with air blasters "
        "M16A-{s}-R01Z…R04Z → clamshell + rod gate bank M16A-{s}-H01…H04 → "
        "outbound belt M16A-{s}-T13 at 1,500 t/h. This link models that "
        "equipment only — it carries NO destination assignment")
    _ROUTE_NOTE = (
        "DERIVED / TBC route selection, NOT confirmed equipment behaviour: "
        "neither PFD-0001[3] nor PRO-0001 states which H gate or which "
        "downstream diverter sends the {s} stream to a PAP receiving line "
        "rather than to the drying unit, and the PAP/drying route confluence "
        "is not resolvable "
        "from the available sheet. The belt RATINGS are confirmed "
        "(PAP: six parallel 1,500 t/h conveyors, 3 running + 3 future; "
        "drying: two parallel 1,299 t/h conveyors, both OUT OF SCOPE — "
        "PRO-0001 §2.1); the ASSIGNMENT of this stream to a destination is "
        "not. Mutual exclusion per bank (one destination per bucket) is "
        "DERIVED from the single outbound belt M16A-{s}-T13 — a simultaneous "
        "split across both destinations is unconfirmed. A gate/route "
        "changeover of 10 min is documented in the parameter register but is "
        "NOT MODELLED by this MVP (changeover_min = 0; no inter-bucket "
        "switching loss is enforced)")
    for stream in ("A", "B"):
        sc.nodes.append(Node(
            f"HUB:M16-T13-{stream}", "MPH", "gate",
            f"M16A-{stream}-T13 outbound belt (1,500 t/h) after gate bank "
            f"M16A-{stream}-H01…H04 — PAP/drying selection downstream is "
            f"DERIVED/TBC", 3, 2 if stream == "A" else 6))
        sc.links.append(Link(
            f"L-H-{stream}", f"HUB:M16-RCL-{stream}", f"HUB:M16-T13-{stream}",
            capacity_tph=1500, gate_id=f"M16A-{stream}-H01…H04",
            evidence=CONF(_M16PFD, _BANK_NOTE.format(s=stream))))
        sc.links += [
            Link(f"L-RTE-{stream}-PAP", f"HUB:M16-T13-{stream}", "DEST:PAP-1",
                 capacity_tph=1500, gate_position="PAP",
                 conflict_group=f"M16A-{stream}-ROUTE",
                 evidence=DERV(_M16PFD + " + " + _M16PRO,
                               _ROUTE_NOTE.format(s=stream))),
            Link(f"L-RTE-{stream}-PAP2", f"HUB:M16-T13-{stream}", "DEST:PAP-2",
                 capacity_tph=1500, available=False, gate_position="PAP-2",
                 conflict_group=f"M16A-{stream}-ROUTE",
                 evidence=DERV(
                     _M16PFD + " + " + _M16PRO
                     + " + project-team instruction 29-Jul-2026",
                     _ROUTE_NOTE.format(s=stream)
                     + ". PAP-2 is retained as a Future planning option by "
                       "default; its exact downstream identity and assignment "
                       "remain TBC."),
                 lifecycle="FUTURE"),
            Link(f"L-RTE-{stream}-DRY", f"HUB:M16-T13-{stream}",
                 "DEST:DRY-FEED", capacity_tph=1299, gate_position="DRY",
                 conflict_group=f"M16A-{stream}-ROUTE",
                 evidence=DERV(_M16PRO, _ROUTE_NOTE.format(s=stream))),
        ]
    sc.destinations += [
        Destination("PAP-1", "MPH", "DEST:PAP-1", priority=1,
                    demand_default_tph=1500,
                    requested_grade="TBT",
                    spec=[SpecRange("BPL", pmin=71.0, target=71.0, hard=False),
                          SpecRange("Cd", pmax=28.0, hard=False)],
                    evidence=CONF(_PRO, "1500 tph/line confirmed; BPL 71 from report "
                                        "worked example; Cd max 28 ppm ASSUMED")),
        Destination(
            "PAP-2", "MPH", "DEST:PAP-2", priority=1,
            demand_default_tph=1500,
            requested_grade="TBT",
            spec=[SpecRange("BPL", pmin=71.0, target=71.0, hard=False),
                  SpecRange("Cd", pmax=28.0, hard=False)],
            evidence=DERV(
                _PRO + " + project-team instruction 29-Jul-2026",
                "The documented PAP conveyor rating is 1,500 t/h per line. "
                "PAP-2 is a user-directed second receiving-line planning "
                "option with the same demand and quality template as PAP-1; "
                "its exact downstream tag and route assignment remain TBC.")),
        Destination("DRY-FEED", "MPH", "DEST:DRY-FEED", priority=2,
                    demand_default_tph=800,
                    requested_grade="TBT",
                    spec=[SpecRange("BPL", pmin=68.0, hard=False),
                          SpecRange("Cd", pmax=26.0, hard=False)],
                    evidence=ASSUME("dry→rail→SPH stream: demand 800 tph and "
                                    "Cd-constrained spec are placeholders")),
    ]
    # Keep the design demand/profile in the scenario while excluding this
    # future option from active demand and route candidates.
    set_destination_line_operating_state(sc, "PAP-2", "FUTURE")
    if with_pseudo_corridor:
        # stand-in for corridor arrivals when mines are not modelled
        sc.nodes.append(Node("SOURCE:CORRIDOR", "MPH", "source",
                             "SIMULATED corridor-arrival input (mines not modelled)",
                             0, 4))
        sc.links.append(Link("L-CORR-IN", "SOURCE:CORRIDOR", "TOWER:M16-REC",
                             capacity_tph=2600, transit_buckets=1,
                             evidence=ASSUME("aggregated corridor stand-in; real "
                                             "corridors in scenario S3")))
        for t in range(0, 6):
            sc.belt_profile.append(BeltProfileEntry(
                "L-CORR-IN", t, 2000, "TBT" if t % 2 == 0 else "BT",
                quality={}))


def mph_phosphate() -> Scenario:
    sc = Scenario(
        name="S2 — MPH M16 hub only (simulated corridor arrivals)",
        description="M16-only demonstration. Upstream corridor arrivals are "
                    "SIMULATED inputs — the Louta/Benguerir mine yards are NOT "
                    "modelled here; use S3 for the complete three-site phosphate "
                    "value chain. Confirmed: storage/machine/conveyor figures. "
                    "ASSUMED: pile contents, specs, drying intake, gate split, "
                    "incoming profile.",
        quality_params=PHOS_PARAMS)
    sc.sites = [Site("MPH", "M'zinda Phosphate Hub", "hub", evidence=CONF(_PRO),
             same_pile_concurrency_allowed=True,
             concurrency_evidence="USER_CONFIRMED (project team, 15-Jul-2026): "
                                  "stacking and reclaiming may occur "
                                  "simultaneously on the same stockpile; the "
                                  "scheduler prevents collisions/overlapping "
                                  "work zones, never same-pile concurrency "
                                  "categorically. Numerical separation limits "
                                  "TBC (demonstration values ASSUMED)")]
    sc.grades = _phos_grades()
    sc.material_profiles = _phos_material_profiles(include_mines=False)
    _mph_m16(sc, with_pseudo_corridor=True)
    sc.segments = [
        Segment("HP-02", 0, 150, 45_000, "BT", age_days=20),
        Segment("HP-02", 150, 300, 45_000, "BT", age_days=5),
        Segment("HP-05", 0, 300, 74_000, "TBT", age_days=12),
        Segment("HP-01", 0, 300, 80_000, "TBT", age_days=30),
    ]
    sc.calendars = [CalendarEntry("ST01-C", 0, 5, "planned maintenance (demo)")]
    # Same revision stamp as S3: both built-ins share the M16 yard, so a
    # corrected M16 must invalidate a hot-reloaded S2 session too.
    sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=24, frozen_buckets=2, seed=42)
    sc.weights = ObjectiveWeights()
    sc.assumptions = [
        Assumption("S2-A1", "Pile contents/grades and initial masses", "M16/inventory",
                   source="placeholder", placeholder="HP-01…08 table",
                   impact="all blending results", action="SMS SpaceValue snapshot"),
        Assumption("S2-A2", "PAP spec: BPL ≥ 71 (worked example), Cd ≤ 28 ppm",
                   "PAP/quality", source="report §15.6 example + placeholder",
                   placeholder="soft specs", impact="on-spec % KPI",
                   action="process to provide contractual feed spec"),
        Assumption("S2-A3", "Drying intake per conveyor is 1,299 t/h "
                   "CONFIRMED (PRO-0001 §2.1: two parallel conveyors to the "
                   "drying unit, each 1,299 t/h, both OUT OF SCOPE); the "
                   "DRY-FEED demand of 800 t/h remains a placeholder",
                   "drying/demand", source=_M16PRO + " + placeholder demand",
                   placeholder="one 1,299 t/h link per gate bank; demand "
                   "800 t/h", impact="dry-stream volumes",
                   action="drying-plant datasheet (OOS plant) + agreed intake "
                   "profile"),
        Assumption("S2-A4", "PAP / drying ROUTE SELECTION is a DERIVED/TBC "
                   "abstraction, not confirmed equipment behaviour. "
                   "PFD-0001[3] confirms the physical discharge chain per "
                   "reclaim stream — T11 → T11S separator → T12 (belt scale "
                   "T12W) → hopper M16A-A-R01 with air blasters → clamshell + "
                   "rod gates M16A-A-H01…H04 / M16A-B-H01…H04 → outbound belt "
                   "T13 at 1,500 t/h — and that equipment is modelled as the "
                   "CONFIRMED links L-H-A / L-H-B. It does NOT state that the "
                   "H gates themselves select PAP versus drying, nor where "
                   "the PAP and drying routes converge. The destination "
                   "choice is therefore a separate downstream logical "
                   "route/diverter abstraction (L-RTE-{A,B}-{PAP,DRY}, "
                   "evidence DERIVED). Confirmed belt RATINGS (PAP six "
                   "parallel 1,500 t/h conveyors, 3 running + 3 future; "
                   "drying two parallel 1,299 t/h conveyors, both OUT OF "
                   "SCOPE — PRO-0001 §2.1) do not make the ASSIGNMENT "
                   "confirmed. Mutual exclusion per bank (one destination per "
                   "bucket) is DERIVED from the single outbound T13 belt. The "
                   "documented 10-minute gate/route changeover is NOT "
                   "MODELLED: changeover_min = 0 on every link and no "
                   "inter-bucket switching loss is enforced.", "M16/routes",
                   status="OPEN", confidence="L",
                   source=_M16PFD + " (equipment) + " + _M16PRO
                   + " (conveyor counts/ratings); destination assignment and "
                   "confluence not stated in either",
                   placeholder="HUB:M16-RCL-{A,B} → L-H-{A,B} (CONFIRMED) → "
                   "HUB:M16-T13-{A,B} → L-RTE-{A,B}-{PAP,PAP2,DRY} "
                   "(DERIVED) + one "
                   "conflict group per bank",
                   impact="which destination each stream may feed per bucket, "
                   "the simultaneous PAP+drying mix, and any switching cost "
                   "(currently none)",
                   action="downstream PFD sheets / GA giving the "
                   "gate→conveyor→destination assignment, the confluence "
                   "point and the real changeover behaviour"),
        Assumption("S2-A8", "M16 hangar numbering inside the bays is "
                   "INFERRED. " + M16_HP_NUMBERING_BASIS + " The bay "
                   "topology itself (four two-hangar bays with the machine "
                   "lines M16A-A-T06 · M16A-A-T11 · M16A-B-T06 · M16A-B-T11 "
                   "· M16A-C-T06 interleaved between them) is CONFIRMED by "
                   "the PFD, and machine reach follows the project-team "
                   "bay-adjacency rule: ST01-A→bay 1; RC01-A→bays 1-2; "
                   "ST01-B→bays 2-3; RC01-B→bays 3-4; ST01-C→bay 4. With "
                   "Future expansion the same rule extends ST01-C to bays "
                   "4-5 and adds M16A-C-RC01 (bays 5-6) and M16A-D-ST01 "
                   "(bay 6); the future hangar identifiers HP-09*…HP-12* are "
                   "INFERRED display-only labels and are never solver "
                   "spaces.",
                   "M16/geometry", status="CONFIRMED", confidence="M",
                   source=_M16PFD + " + project-team reach rule 25-Jul-2026",
                   placeholder="M16_BAYS + M16_MACHINE_LINES → "
                   "Machine.reachable_spaces",
                   impact="which machine may stack/reclaim which hangar — "
                   "solver candidates, ASP work-area validation and the mimic",
                   action="plot plan + official SMS pile-ID register to "
                   "confirm the hangar names per bay"),
        Assumption("S2-A9", "CURRENT M16 reception is ONE shared 2,600 t/h "
                   "envelope, not 2 × 2,600 t/h. " + M16_RECEPTION_BASIS
                   + " Modelled as a shared-capacity group on every stacking "
                   "leg, so total stacking at M16 cannot exceed 2,600 t/h in "
                   "a bucket whichever corridor the material came from. "
                   "CONSEQUENCE: the S3 initial in-transit profile shows the "
                   "corridors running alternately (C1 arrivals in the buckets "
                   "C2 leaves free) instead of both at full rate in the same "
                   "bucket — the alternation is DERIVED from this envelope, "
                   "not observed dispatch history. A structural cap on "
                   "simultaneous corridor dispatch, plus any reception "
                   "buffer/interlock, still needs route evidence.",
                   "M16/routes", status="CONFIRMED", confidence="M",
                   source=_M16PRO + ".1 (auto sampler M16VP02 on the adjacent "
                   "FUTURE conveyor) + " + _M16PFD + " (FUTURE marking)",
                   placeholder="shared_group " + repr(M16_RECEPTION_GROUP)
                   + " on L-STK-HP-01…08",
                   impact="maximum stacking/reception rate, corridor "
                   "scheduling and the initial in-transit mass",
                   action="confirm the second reception line's phase, and "
                   "obtain the reception buffer/interlock rules"),
        Assumption(
            "S2-A10",
            "PAP-2 is a project-team requested second receiving-line planning "
            "option with the same 1,500 t/h / TBT demand and quality template "
            "as PAP-1. It defaults to FUTURE: its two route legs remain in the "
            "network with lifecycle FUTURE and availability false, while the "
            "configured 36,000 t/24 h demand template is retained but excluded "
            "from active planning demand. Selecting AVAILABLE activates both "
            "route legs without inventing additional M16 reclaim-bank "
            "capacity. PAP-1 + PAP-2 + DRY-FEED at their full templates request "
            "91,200 t against the current 72,000 t/day two-bank envelope, so "
            "simultaneous full service is not claimed.",
            "M16/PAP", status="ASSUMED", confidence="L",
            source="project-team instruction 29-Jul-2026 + " + _M16PRO,
            placeholder="PAP-2 / DEST:PAP-2 / L-RTE-{A,B}-PAP2; "
            "DESTINATION_LINE_OPERATING_STATE=PAP-2:FUTURE",
            impact="optional second PAP demand and outbound-bank allocation",
            action="confirm PAP-2 physical tag, commissioning phase, downstream "
            "route and the capacity configuration used when it is available"),
        Assumption("S2-A5", "Space x-geometry 0–300 m; machine travel 30 m/min; "
                   "setup 10 min", "M16/geometry", source="placeholder",
                   placeholder="uniform ranges", impact="travel/changeover times",
                   action="plot plan / GA"),
        Assumption("S2-A6", "Corridor arrivals stand-in: 2000 tph × 6 h alternating "
                   "TBT/BT", "corridor", source="placeholder",
                   placeholder="belt profile entries",
                   impact="stacking workload", action="use scenario S3 for real "
                   "corridors"),
        Assumption("S2-A7", "TBT/BT/MT numeric vectors are demonstration "
                   "quality profiles, not approved mine/site assays. Their "
                   "semantic order is corrected to TBT < BT < MT from the "
                   "BoD terminology; the numeric values remain placeholders. "
                   "Raw/screened ore has a separate empty RAW-TBC grade and "
                   "must never inherit these vectors.",
                   "phosphate/quality", source="report worked example + "
                   "parameter register",
                   placeholder="material_profiles + demo grade vectors",
                   impact="all quality/blending results",
                   action="obtain per-site raw and washed assay tables, grade "
                   "cut-offs and current SMS Material/Grade/SQP mapping"),
    ]
    return sc


# =============================================================================
# 3. End-to-end Louta + Benguerir -> MPH demonstration
# =============================================================================
def _add_benguerir_planning_chain(sc: Scenario) -> None:
    """Add the user-confirmed six-pile Benguerir planning chain.

    The physical arrangement is:

      TM1/TSM2/TSM3 hoppers -> BG-MINE-1/2 -> BG-RAW-1/2
      -> three washing lines -> BG-P1/2 -> C2 / train.

    The hopper rows and the two process buffers are finite solver interfaces,
    not additional on-ground stockpiles.  Borrowed wash rates/recoveries are
    explicitly marked as simulation assumptions.
    """
    # Replace the former isolated washing-line placeholder atomically.
    sc.nodes = [
        node for node in sc.nodes
        if node.id != "DEST:BG-WASH-L1-FEED"
    ]
    sc.destinations = [
        destination for destination in sc.destinations
        if destination.id not in set(_BEN_WASH_CASES)
    ]

    hopper_grade = {
        "BG-HOP-TM1": "RAW-BT-GLOBAL",
        "BG-HOP-TSM2": "RAW-BT-GLOBAL",
        "BG-HOP-TSM3": "RAW-BT-LOW-CD",
    }
    sc.spaces += [
        StockSpace(
            hopper_id, "BENGUERIR", "mine-interface", "closed",
            _BEN_HOPPER_INTERFACE_T, _BEN_HOPPER_INTERFACE_T, grade,
            state="reserved", designated_grade=grade, x_from=0, x_to=1,
            material_class="raw_screened", profile_id="BEN-RAW-SCREENED",
            evidence=ASSUME(
                "Finite 24 h planning handoff for the user-confirmed "
                "TM1/TSM2/TSM3 mine-hopper arrangement. B01 confirms TM1 "
                "+ TSM2 + TSM3 and documents TSM2 at about 225 m3 (TBC), "
                "1,300 t/h; this 10,000 t campaign buffer is a simulation "
                "input, not hopper live capacity or telemetry."))
        for hopper_id, grade in hopper_grade.items()
    ]
    sc.spaces += [
        StockSpace(
            "BG-MINE-1", "BENGUERIR", "mine-yard", "open",
            _BEN_PHYSICAL_PILE_CAPACITY_T,
            _BEN_PHYSICAL_PILE_START_MASS_T,
            "RAW-BT-GLOBAL", x_from=0, x_to=500, safety_stock_t=20_000,
            material_class="raw_screened", profile_id="BEN-RAW-SCREENED",
            evidence=ASSUME(
                "Project-team layout correction 28-Jul-2026: first of two "
                "additional mine-ore piles. 200,000 t capacity and 100,000 t "
                "S0 start are user-directed simulation values; this is larger "
                "than Louta's assumed 100,000 t 102MG display pile and is not "
                "claimed as a surveyed/live value.")),
        StockSpace(
            "BG-MINE-2", "BENGUERIR", "mine-yard", "open",
            _BEN_PHYSICAL_PILE_CAPACITY_T,
            _BEN_PHYSICAL_PILE_START_MASS_T,
            "RAW-BT-LOW-CD", x_from=0, x_to=500, safety_stock_t=20_000,
            material_class="raw_screened", profile_id="BEN-RAW-SCREENED",
            evidence=ASSUME("as BG-MINE-1; Low-Cd S0 campaign")),
        StockSpace(
            "BG-RAW-1", "BENGUERIR", "mine-yard", "open",
            _BEN_PHYSICAL_PILE_CAPACITY_T,
            _BEN_PHYSICAL_PILE_START_MASS_T,
            "RAW-BT-GLOBAL", x_from=0, x_to=500, safety_stock_t=20_000,
            material_class="raw_screened", profile_id="BEN-RAW-SCREENED",
            evidence=CONF(
                _B01,
                "screened-product storage promoted from reference geometry "
                "to the S0 chain at the project team's request; existing "
                "200,000 t capacity / 100,000 t start baseline preserved")),
        StockSpace(
            "BG-RAW-2", "BENGUERIR", "mine-yard", "open",
            _BEN_PHYSICAL_PILE_CAPACITY_T,
            _BEN_PHYSICAL_PILE_START_MASS_T,
            "RAW-BT-LOW-CD", x_from=0, x_to=500, safety_stock_t=20_000,
            material_class="raw_screened", profile_id="BEN-RAW-SCREENED",
            evidence=CONF(_B01, "as BG-RAW-1; Low-Cd S0 campaign")),
    ]
    for line_id, case in _BEN_WASH_CASES.items():
        suffix = line_id[-1]
        raw_grade = str(case["feed_grade"])
        product_grade = str(case["output_grade"])
        sc.spaces += [
            StockSpace(
                f"BG-SCREEN-L{suffix}-WIP", "BENGUERIR",
                "process-interface", "closed", 10_000, 0, raw_grade,
                state="empty", designated_grade=raw_grade, x_from=0, x_to=1,
                material_class="raw_screened",
                profile_id="BEN-RAW-SCREENED",
                evidence=ASSUME(
                    "zero-start transfer interface between the mine-pile "
                    "reclaimer and screened-pile stacker; not an on-ground "
                    "stockpile")),
            StockSpace(
                f"BG-WASH-L{suffix}-WIP", "BENGUERIR",
                "process-interface", "closed", 10_000, 0, product_grade,
                state="empty", designated_grade=product_grade,
                x_from=0, x_to=1, material_class="washed",
                profile_id="BEN-WASHED-FILTERED",
                evidence=ASSUME(
                    "zero-start recovered-product interface for the "
                    "configured 30-minute washing dependency; not an "
                    "on-ground stockpile")),
        ]

    sc.machines += [
        Machine(
            "BG-MINE-ST-1", "BENGUERIR", "stacker", 0, 2100,
            ["BG-MINE-1"], rail="BG-MINE-ST1-R", position_x=250,
            physical_tag="", lifecycle="SELECTED", planning_enabled=True,
            rate_design_tph=2500,
            evidence=ASSUME(
                "New mine-pile machine identity and reach are user-directed "
                "for the 28-Jul-2026 six-pile simulation. Rate reuses the "
                "Table 20 2,100/2,500 t/h humid envelope; physical tag/GA TBC.")),
        Machine(
            "BG-MINE-ST-2", "BENGUERIR", "stacker", 0, 2100,
            ["BG-MINE-2"], rail="BG-MINE-ST2-R", position_x=250,
            physical_tag="", lifecycle="SELECTED", planning_enabled=True,
            rate_design_tph=2500,
            evidence=ASSUME("as BG-MINE-ST-1 for mine pile 2")),
        Machine(
            "BG-MINE-BWR", "BENGUERIR", "reclaimer", 500, 2100,
            ["BG-MINE-1", "BG-MINE-2"], rail="BG-MINE-R",
            position_x=250, physical_tag="", lifecycle="SELECTED",
            planning_enabled=True, rate_design_tph=2500,
            evidence=ASSUME(
                "Shared mine-pile reclaimer added for a material-conserving "
                "six-pile S0 chain; user-directed topology, Table 20 rate "
                "envelope reused, physical tag/GA TBC.")),
        Machine(
            "BG-RAW-ST-1", "BENGUERIR", "stacker", 0, 2100,
            ["BG-RAW-1"], rail="BG-RAW-ST1-R", position_x=250,
            physical_tag="", lifecycle="SELECTED", planning_enabled=True,
            rate_design_tph=2500,
            evidence=CONF(
                _B01,
                "screened-product pile-1 stacker; 2,100/2,500 t/h humid "
                "Table 20 envelope, physical tag/GA TBC")),
        Machine(
            "BG-RAW-ST-2", "BENGUERIR", "stacker", 0, 2100,
            ["BG-RAW-2"], rail="BG-RAW-ST2-R", position_x=250,
            physical_tag="", lifecycle="SELECTED", planning_enabled=True,
            rate_design_tph=2500,
            evidence=CONF(_B01, "as BG-RAW-ST-1 for screened pile 2")),
        Machine(
            "BG-RAW-BWR", "BENGUERIR", "reclaimer", 500, 2100,
            ["BG-RAW-1", "BG-RAW-2"], rail="BG-RAW-R", position_x=250,
            physical_tag="", lifecycle="SELECTED", planning_enabled=True,
            rate_design_tph=2500,
            evidence=CONF(
                _B01,
                "one shared screened-product reclaimer reaches both piles; "
                "Table 20 2,100/2,500 t/h humid envelope used. The separate "
                "B02 2,600/3,120 t/h interface remains a documented conflict.")),
    ]
    # Align the solver reach matrix with the drawing and make the current ST3
    # stacking route part of the normal planning chain.
    sc.machine("RC2").reachable_spaces = ["BG-P1"]
    sc.machine("RC3").reachable_spaces = ["BG-P2"]
    sc.machine("ST3").planning_enabled = bool(
        sc.config.get("BEN_B02_OPTION_ENABLED", True))

    sc.nodes += [
        Node(f"SPACE:{space.id}", "BENGUERIR",
             "source" if space.id.startswith("BG-HOP-") else "space",
             space.id, 0, 0)
        for space in sc.spaces
        if space.site == "BENGUERIR"
        and space.id.startswith(("BG-HOP-", "BG-MINE-", "BG-RAW-",
                                 "BG-SCREEN-", "BG-WASH-"))
        and not any(node.id == f"SPACE:{space.id}" for node in sc.nodes)
    ]
    sc.nodes += [
        Node("HUB:BG-MINE-STK", "BENGUERIR", "tower",
             "TM1/TSM2/TSM3 mine-pile stacking distribution", 1, 20),
        Node("HUB:BG-MINE-RCL", "BENGUERIR", "tower",
             "shared mine-pile reclaim collection", 2, 20),
        Node("HUB:BG-RAW-STK", "BENGUERIR", "tower",
             "screened-pile stacking distribution", 3, 20),
        Node("HUB:BG-RAW-WASH", "BENGUERIR", "tower",
             "shared screened-product reclaim to washing", 4, 20),
        Node("HUB:BG-PROD-STK", "BENGUERIR", "tower",
             "ST3 washed-product stacking distribution", 5, 20),
    ]

    # Three mine hoppers feed two mine piles. TM1 and TSM2 share campaign 1;
    # TSM3 feeds campaign 2. This is an explicit S0 allocation, not an as-built
    # chute claim.
    hopper_for_line = {
        "BEN-WASH-L1": "BG-HOP-TM1",
        "BEN-WASH-L2": "BG-HOP-TSM3",
        "BEN-WASH-L3": "BG-HOP-TSM2",
    }
    mine_for_line = {
        "BEN-WASH-L1": "BG-MINE-1",
        "BEN-WASH-L2": "BG-MINE-2",
        "BEN-WASH-L3": "BG-MINE-1",
    }
    raw_for_line = {
        "BEN-WASH-L1": "BG-RAW-1",
        "BEN-WASH-L2": "BG-RAW-2",
        "BEN-WASH-L3": "BG-RAW-1",
    }
    mine_stacker_for_line = {
        "BEN-WASH-L1": "BG-MINE-ST-1",
        "BEN-WASH-L2": "BG-MINE-ST-2",
        "BEN-WASH-L3": "BG-MINE-ST-1",
    }
    raw_stacker_for_line = {
        "BEN-WASH-L1": "BG-RAW-ST-1",
        "BEN-WASH-L2": "BG-RAW-ST-2",
        "BEN-WASH-L3": "BG-RAW-ST-1",
    }

    for line_id, case in _BEN_WASH_CASES.items():
        suffix = line_id[-1]
        hopper = hopper_for_line[line_id]
        mine_pile = mine_for_line[line_id]
        raw_pile = raw_for_line[line_id]
        screen_wip = f"BG-SCREEN-L{suffix}-WIP"
        wash_wip = f"BG-WASH-L{suffix}-WIP"
        product_pile = "BG-P2" if line_id == "BEN-WASH-L2" else "BG-P1"
        stages = _BEN_PROCESS_STAGE_IDS[line_id]

        sc.links += [
            Link(
                f"L-BG-HOP-L{suffix}", f"SPACE:{hopper}",
                "HUB:BG-MINE-STK", 2100, material_class="raw",
                evidence=ASSUME("S0 campaign hopper-to-mine stacking inlet")),
            Link(
                f"L-BG-MINE-STK-L{suffix}", "HUB:BG-MINE-STK",
                f"SPACE:{mine_pile}", 2100, material_class="raw",
                evidence=ASSUME("S0 mine-pile allocation")),
            Link(
                f"L-BG-MINE-RCL-IN-L{suffix}", f"SPACE:{mine_pile}",
                "HUB:BG-MINE-RCL", 2100, material_class="raw",
                evidence=ASSUME("shared mine-pile reclaimer collection")),
            Link(
                f"L-BG-MINE-RCL-OUT-L{suffix}", "HUB:BG-MINE-RCL",
                f"SPACE:{screen_wip}", 2100, material_class="raw",
                evidence=ASSUME("line-specific screened-transfer interface")),
            Link(
                f"L-BG-SCREEN-STK-IN-L{suffix}", f"SPACE:{screen_wip}",
                "HUB:BG-RAW-STK", 2100, material_class="raw",
                evidence=ASSUME("screened-pile stacking inlet")),
            Link(
                f"L-BG-SCREEN-STK-OUT-L{suffix}", "HUB:BG-RAW-STK",
                f"SPACE:{raw_pile}", 2100, material_class="raw",
                evidence=ASSUME("S0 screened-pile allocation")),
            Link(
                f"L-BG-WASH-IN-L{suffix}", f"SPACE:{raw_pile}",
                "HUB:BG-RAW-WASH", 2100, material_class="raw",
                evidence=ASSUME("shared screened-product reclaim collection")),
            Link(
                f"L-BG-WASH-L{suffix}", "HUB:BG-RAW-WASH",
                f"SPACE:{wash_wip}", float(case["feed_tph"]),
                material_class="raw",
                evidence=ASSUME(
                    "Benguerir simulation reuses the corresponding documented "
                    "Louta wash-case feed envelope; not a Benguerir nameplate.")),
            Link(
                f"L-BG-PROD-STK-IN-L{suffix}", f"SPACE:{wash_wip}",
                "HUB:BG-PROD-STK", 2100, material_class="washed",
                evidence=ASSUME("line output to the shared ST3 inlet")),
            Link(
                f"L-BG-PROD-STK-OUT-L{suffix}", "HUB:BG-PROD-STK",
                f"SPACE:{product_pile}", 2100, material_class="washed",
                evidence=ASSUME("S0 line-to-product-pile allocation")),
        ]
        sc.process_transfers += [
            ProcessTransfer(
                f"BG-L{suffix}-MINE-STACK", mine_stacker_for_line[line_id],
                hopper, mine_pile,
                [f"L-BG-HOP-L{suffix}", f"L-BG-MINE-STK-L{suffix}"],
                operation_kind="stack", process_stage=stages[0],
                output_grade=str(case["feed_grade"]),
                purpose=f"Supply Benguerir washing campaign L{suffix}",
                evidence=ASSUME("user-directed six-pile S0 campaign")),
            ProcessTransfer(
                f"BG-L{suffix}-MINE-RECLAIM", "BG-MINE-BWR",
                mine_pile, screen_wip,
                [f"L-BG-MINE-RCL-IN-L{suffix}",
                 f"L-BG-MINE-RCL-OUT-L{suffix}"],
                operation_kind="reclaim", process_stage=stages[1],
                output_grade=str(case["feed_grade"]),
                purpose=f"Transfer L{suffix} mine ore to screened storage",
                evidence=ASSUME("user-directed six-pile S0 campaign")),
            ProcessTransfer(
                f"BG-L{suffix}-RAW-STACK", raw_stacker_for_line[line_id],
                screen_wip, raw_pile,
                [f"L-BG-SCREEN-STK-IN-L{suffix}",
                 f"L-BG-SCREEN-STK-OUT-L{suffix}"],
                operation_kind="stack", process_stage=stages[2],
                output_grade=str(case["feed_grade"]),
                depends_on_stage=stages[1], dependency_delay_min=0,
                purpose=f"Build screened pile for washing campaign L{suffix}",
                evidence=ASSUME("user-directed six-pile S0 campaign")),
            ProcessTransfer(
                f"BG-L{suffix}-WASH", "BG-RAW-BWR", raw_pile, wash_wip,
                [f"L-BG-WASH-IN-L{suffix}", f"L-BG-WASH-L{suffix}"],
                operation_kind="reclaim", process_stage=stages[3],
                output_grade=str(case["output_grade"]),
                yield_factor=float(case["recovery"]), residence_min=30,
                purpose=f"Produce configured Benguerir line L{suffix} output",
                evidence=ASSUME(
                    "Feed/recovery and raw-to-product mapping reuse a Louta "
                    "design case solely for the requested simulation.")),
            ProcessTransfer(
                f"BG-L{suffix}-PRODUCT-STACK", "ST3", wash_wip,
                product_pile,
                [f"L-BG-PROD-STK-IN-L{suffix}",
                 f"L-BG-PROD-STK-OUT-L{suffix}"],
                operation_kind="stack", process_stage=stages[4],
                output_grade=str(case["output_grade"]),
                depends_on_stage=stages[3], dependency_delay_min=30,
                purpose=f"Stack recovered line L{suffix} product",
                evidence=ASSUME(
                    "ST3 is current equipment; line-to-pile campaign mapping "
                    "and the 30-minute dependency are S0 assumptions.")),
        ]
        sc.destinations.append(Destination(
            line_id, "BENGUERIR", f"SPACE:{wash_wip}", priority=1,
            demand_default_tph=float(case["feed_t"]) / 24.0,
            requested_grade=str(case["feed_grade"]),
            demand_kind="process_feed", process_stage=line_id,
            evidence=ASSUME(
                "User-requested three-line planning scenario. Feed quantity "
                "is the integer S0 campaign needed for 1,800 t recovered "
                "output under the borrowed Louta case; not an approved order.")))

    sc.config["BEN_SIX_PILE_CHAIN_ENABLED"] = True
    sc.config["BEN_MINE_HOPPER_IDS"] = [
        "BG-HOP-TM1", "BG-HOP-TSM2", "BG-HOP-TSM3"]
    sc.config["BEN_WASH_LINE_CASE"] = {
        line_id: (
            "LOW_CD" if line_id == "BEN-WASH-L2" else "GLOBAL")
        for line_id in _BEN_WASH_CASES
    }
    sc.config["BEN_WASH_OUTPUT_GRADE_MAPPING"] = {
        line_id: str(case["output_grade"])
        for line_id, case in _BEN_WASH_CASES.items()
    }
    sc.config["BEN_WASH_OUTPUT_GRADE_MAPPING_BASIS"] = (
        "ASSUMED S0 mapping requested for simulation only; no approved "
        "Benguerir line-to-TBT/BT/MT campaign table is in the workspace.")
    sc.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"]["BENGUERIR"] = True
    process_targets = dict(
        sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}) or {})
    for line_id, case in _BEN_WASH_CASES.items():
        stages = _BEN_PROCESS_STAGE_IDS[line_id]
        for stage in stages[:3]:
            process_targets[stage] = float(case["feed_t"])
        process_targets[stages[3]] = float(case["output_t"])
        process_targets[stages[4]] = float(case["output_t"])
    sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = process_targets
    sc.config["PROCESS_NO_PASSTHROUGH_SPACES"] = list(dict.fromkeys(
        list(sc.config.get("PROCESS_NO_PASSTHROUGH_SPACES", []) or [])
        + [f"BG-SCREEN-L{i}-WIP" for i in (1, 2, 3)]
        + [f"BG-WASH-L{i}-WIP" for i in (1, 2, 3)]
    ))
    terminal = dict(sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}) or {})
    terminal.update({
        "BG-MINE-1": _BEN_PHYSICAL_PILE_START_MASS_T,
        "BG-MINE-2": _BEN_PHYSICAL_PILE_START_MASS_T,
        "BG-RAW-1": _BEN_PHYSICAL_PILE_START_MASS_T,
        "BG-RAW-2": _BEN_PHYSICAL_PILE_START_MASS_T,
        **{f"BG-SCREEN-L{i}-WIP": 0.0 for i in (1, 2, 3)},
        **{f"BG-WASH-L{i}-WIP": 0.0 for i in (1, 2, 3)},
    })
    sc.config["TERMINAL_INVENTORY_EXACT_T"] = terminal


def end_to_end(ben_b02_enabled: bool = True,
               ben_raw_to_c2: bool = False,
               ben_st3_stacking: bool = False,
               multi_grade_zones: bool = False) -> Scenario:
    """PRIMARY Domain-A scenario.

    Benguerir configuration switches (Q80130-BEN-B02-ME-BOD-0001 Rev B):
      ben_b02_enabled — includes the project-team-confirmed current B02
        washed-yard configuration (ST3**, RC2**, RC3**, …). The legacy
        switch remains for sensitivity testing; False is a non-baseline
        what-if in which the current machines stay visible as reference
        assets and the planner must not assign them jobs.
      ben_raw_to_c2 — conditional raw-ore → C2 route (BoD §2.6: requires
        modification of the existing phase-1 250 m³ hopper and/or optional
        RB9/RB10). Default False.
      ben_st3_stacking — retained only for saved-call compatibility. The
        six-pile baseline always routes the three washing campaigns through
        ST3, so this legacy flag no longer changes the planning topology.
      multi_grade_zones — EXPERIMENTAL (default False): honour
        Scenario.pile_zones inputs (none are defined here — zone/campaign
        rules are TBC, S3-A2/S3-A7). With the flag off or no zone inputs,
        behaviour and signatures are unchanged.
    """
    sc = Scenario(
        name=S3_SCENARIO_NAME,
        description="PRIMARY Domain-A scenario (report Rev D architecture): the "
                    "Louta and Benguerir mine stockyards feed the MPH M16 "
                    "receiving hub through dedicated corridors C1 (Louta, "
                    "nearer; provisional ≈30 km/≈2 h) and C2 (Benguerir, "
                    "farther; provisional ≈60 km/≈4 h), with in-transit "
                    "material, blend-while-"
                    "stacking at M16 and reclaim to PAP/drying. Benguerir "
                    "follows the B02 mechanical BoD (staged raw → washing → "
                    "washed-product yard; the B02 table is accepted as the "
                    "current configuration, with '**' retained only as "
                    "source notation; raw→C2 conditional route disabled). USES "
                    "DECLARED ASSUMPTIONS for Louta layout, pile contents "
                    "and specs — see Assumptions page.",
        quality_params=PHOS_PARAMS)
    sc.sites = [
        Site("MPH", "M'zinda Phosphate Hub", "hub", evidence=CONF(_PRO),
             same_pile_concurrency_allowed=True,
             concurrency_evidence="USER_CONFIRMED (project team, 15-Jul-2026): "
                                  "stacking and reclaiming may occur "
                                  "simultaneously on the same stockpile; the "
                                  "scheduler prevents collisions/overlapping "
                                  "work zones, never same-pile concurrency "
                                  "categorically. Numerical separation limits "
                                  "TBC (demonstration values ASSUMED)"),
        Site("BENGUERIR", "Benguerir mine + beneficiation expansion (BBE)",
             "mine",
             evidence=CONF(_B02,
                           "washed-product yard, capacities, material states "
                           "and corridor/train interfaces per the B02 "
                           "mechanical BoD; raw-yard pile count "
                           "VISIBLE_SCHEMATIC/TBC (no GA); earlier B01 "
                           "screening evidence retained upstream — process "
                           "stages connected; exact phase/tag/PFD "
                           "reconciliation TBC"),
             material_profiles={
                 "raw": {"bulk_density_t_m3": "1.2–1.4",
                         "moisture_pct": "10–14",
                         "angle_of_repose_deg": "30–35",
                         "behaviour": "abrasive"},
                 "washed_filtered": {"bulk_density_t_m3": "1.2–1.4",
                                     "moisture_pct": "23",
                                     "angle_of_repose_deg": "35",
                                     "behaviour": "abrasive"},
                 "settled_product": {"bulk_density_t_m3": "1.2–1.4",
                                     "moisture_pct": "15–20",
                                     "angle_of_repose_deg": "35",
                                     "behaviour": "abrasive"},
             }),
        Site("LOUTA", "Louta mine + beneficiation yard", "mine",
             evidence=TBC(
                 _LOU_L01_PD + " + " + _LOU_PD,
                 "L01 102MG two-dedicated-stacker/one-shared-reclaimer "
                 "topology and L02 process staging confirmed; live 102MG "
                 "state, metric reach and exact L01→L02 solver interface "
                 "remain TBC"),
             same_pile_concurrency_allowed=False,
             concurrency_evidence=(
                 "ASSUMED conservative S0 policy: Louta stack and reclaim "
                 "jobs do not share a pile in one hourly material bucket "
                 "until machine-clearance and work-zone rules are supplied."),
             exclusive_machine_groups=[
                 ["120LG-RC-01", "120LG-RC-02"],
                 ["140LG-RC-01", "140LG-RC-02"],
             ],
             exclusivity_evidence="RB4/RB5 and C1 feeder selection: the "
                                  "available design documents do not establish "
                                  "simultaneous confluence capacity, so S0 "
                                  "selects one ore reclaimer and one product "
                                  "reclaimer per bucket. Each product reclaimer "
                                  "is 3,000 t/h nominal vs C1 ≈2,600 t/h — "
                                  "EITHER may be selected to feed C1; "
                                  "simultaneous partial-rate reclaim/blending "
                                  "is allowed ONLY if the route-confluence "
                                  "configuration permits it (TBC) → exclusive "
                                  "group configured until confluence is "
                                  "confirmed. The second reclaimer is "
                                  "availability/flexibility, NOT extra "
                                  "corridor throughput."),
    ]
    sc.grades = _phos_grades(include_shared_raw_bt=True)
    sc.material_profiles = _phos_material_profiles(include_mines=True)
    _mph_m16(sc, with_pseudo_corridor=False)

    # ---- Benguerir BBE (B02 mechanical BoD) --------------------------------
    # Staged process (B02 §2.6): RAW/SCREENED MINE STORAGE → washing/
    # beneficiation → NEW WASHED-PRODUCT STORAGE → C2 corridor / train, plus a
    # CONDITIONAL raw-ore → C2 route and a raw → train route. The earlier B01
    # 2023 screening evidence is the upstream stage — process stages
    # connected; exact phase/tag/PFD reconciliation TBC.
    sc.config.update({
        "BUILTIN_BASELINE_REVISION": S3_BASELINE_REVISION,
        "BEN_B02_OPTION_ENABLED": ben_b02_enabled,
        "BEN_RAW_TO_C2_ENABLED": ben_raw_to_c2,
        "BEN_ST3_STACKING_ENABLED": ben_st3_stacking,
        "MULTI_GRADE_ZONES_ENABLED": multi_grade_zones,
        "REPERE_SECTION_COUNT": 1000,
        "REPERE_CONVENTION_BASIS": (
            "USER_CONFIRMED operational convention, 24-Jul-2026: every "
            "stockpile is addressed from R0 to R1000 (1,000 equal "
            "longitudinal intervals). The default maps R0 to x_from and "
            "R1000 to x_to; per-pile physical origin/direction remains TBC."),
        "WASHING_LINE_RESIDENCE_TIME_H": dict(
            WASHING_LINE_RESIDENCE_TIME_H),
        "WASHING_LINE_RESIDENCE_TIME_BASIS":
            WASHING_LINE_RESIDENCE_TIME_BASIS,
        "WASHING_LINE_RESIDENCE_TIME_MODELLED": {
            "LOUTA": True,
            "BENGUERIR": True,
        },
        "LOU_S0_STOCKED_ROUTE_ENABLED": True,
        "LOU_C1_STOCKED_ROUTE_TARGET_T": _LOUTA_S0_C1_TARGET_T,
        "LOU_WASH_LINE_OUTPUT_TARGET_T":
            dict(_LOUTA_S0_LINE_TARGET_T),
        "LOU_WASHING_RESIDENCE_MIN": 30,
        "LOU_WASH_LINE_CASE": {
            "LOU-WASH-L1": "GLOBAL",
            "LOU-WASH-L2": "LOW_CD",
        },
        "LOU_WASH_OUTPUT_GRADE_MAPPING": {
            "LOU-WASH-L1": "TBT",
            "LOU-WASH-L2": "BT",
        },
        "LOU_WASH_OUTPUT_GRADE_MAPPING_BASIS": (
            "ASSUMED S0 campaign mapping only. The Louta BoD documents "
            "Global/High-Cd/Low-Cd raw ore cases and one combined product "
            "characteristic, but does not map those cases to the project's "
            "TBT/BT/MT product-grade IDs."),
        "LOU_PRODUCT_STACKER_S0_RATE_TPH":
            _LOUTA_PRODUCT_STACKER_S0_TPH,
        "LOU_PRODUCT_STACKER_S0_RATE_BASIS": (
            "ASSUMED conservative S0 planning envelope, not nameplate. "
            "Neither Louta source document gives 130LG-ST-01 throughput."),
        "LOU_DIRECT_MINE_FEED_AVAILABLE_T": {
            "LT-MINE-FEED-GLOBAL": _LOUTA_DIRECT_MINE_FEED_T,
            "LT-MINE-FEED-LOW-CD": _LOUTA_DIRECT_MINE_FEED_T,
        },
        "LOU_DIRECT_MINE_FEED_BASIS": (
            "ASSUMED finite S0 handoff inventory at the documented Louta "
            "mine-transport inlet. It is not a 102MG stockpile or live SMS "
            "snapshot; the 102MG-to-110LG release route remains outside the "
            "model pending rate and route data."),
        # Legacy display fallback retained for imported snapshots that predate
        # the six-pile chain. Current built-ins bind BG-RAW-1/-2 to StockSpace
        # inventory, so this overlay is cleared by the geometry adapter.
        "BG_RAW_DISPLAY_START_MASS_T": {
            "BG-RAW-1": 100_000.0,
            "BG-RAW-2": 100_000.0,
        },
        "BG_RAW_DISPLAY_START_MASS_BASIS": (
            "legacy display fallback; project-team current-state instruction "
            "24-Jul-2026: 50% of each 200,000 t pile capacity. Current "
            "six-pile built-ins use the matching StockSpace inventory."),
        # No Benguerir pile assay, longitudinal survey or grade-to-pile map
        # was supplied.  Per the project team's 24-Jul-2026 clarification,
        # multiple grade profiles may coexist in one pile.  The explicit
        # 50/50 components below demonstrate that capability transparently;
        # their mass shares and profile mapping are ASSUMED until a
        # timestamped mine-SMS/SQMS segment snapshot is available.
        #
        # This single-grade mapping remains only as a backward-compatible
        # fallback for a pile whose component list is absent.
        "BG_RAW_DISPLAY_GRADE_IDS": {
            "BG-RAW-1": "RAW-BT-GLOBAL",
            "BG-RAW-2": "RAW-BT-GLOBAL",
        },
        "BG_RAW_DISPLAY_GRADE_CATALOG": list(RAW_BT_GRADE_IDS),
        "BG_RAW_DISPLAY_GRADE_COMPONENTS": {
            "BG-RAW-1": [
                {
                    "component_id": "C1",
                    "mass_fraction": 0.5,
                    "grade_id": "RAW-BT-GLOBAL",
                },
                {
                    "component_id": "C2",
                    "mass_fraction": 0.5,
                    "grade_id": "RAW-BT-HIGH-CD",
                },
            ],
            "BG-RAW-2": [
                {
                    "component_id": "C1",
                    "mass_fraction": 0.5,
                    "grade_id": "RAW-BT-GLOBAL",
                },
                {
                    "component_id": "C2",
                    "mass_fraction": 0.5,
                    "grade_id": "RAW-BT-LOW-CD",
                },
            ],
        },
        "BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS": (
            "Project-team clarification 24-Jul-2026: multiple grade "
            "profiles may coexist within one stockpile. The displayed "
            "50/50 mass shares and profile-to-pile mapping are ASSUMED "
            "demonstration inputs. At the 50% t0 fill, the two equal "
            "components are shown at display locations R0–R250 and "
            "R250–R500; R500–R1000 is empty. These ranges are derived from "
            "the schematic front-filled bands, not a surveyed spatial order "
            "or an SMS/SQMS segment snapshot"),
        "BG_RAW_DISPLAY_GRADE_BASIS": (
            "Project-team clarification 28-Jul-2026 confirms that the "
            "Global/High-Cd/Low-Cd raw BT grade taxonomy is shared by "
            "Benguerir and Louta. Current chemistry values are sourced from "
            "Louta BoD Q80130-LOU-L02-PR-BOD-0001 Rev B §6.1; they are not "
            "a live or pile-specific Benguerir assay. Louta moisture 15 % is not "
            "substituted for the Benguerir BoD raw-material range of "
            "10–14 %"),
        # ---- Louta 110/120LG homogenized ore storage ----------------------
        # The process description confirms exactly TWO homogenized stockpiles
        # (one stacker 110LG-ST-01 + boom conveyor 110LG-BC-01, chevron) and
        # the Louta BoD confirms the 494,274 t TOTAL without individual pile
        # capacities.  The equal split is therefore DERIVED and the 50% start
        # is an ASSUMED demonstration baseline.  The matching 110LG StockSpace
        # rows are solver inventory; the legacy ``LOU_ORE_DISPLAY_*`` keys
        # retain evidence/display metadata for imported-scenario compatibility.
        "LOU_MINE_102MG_DISPLAY_CAPACITY_T": {
            lane_id: _LOUTA_MINE_DEMO_CAPACITY_T
            for lane_id in _LOUTA_MINE_LANE_IDS
        },
        "LOU_MINE_102MG_DISPLAY_CAPACITY_BASIS": (
            "ASSUMED display-only demonstration placeholder explicitly "
            "adopted by the project team 26-Jul-2026: 100,000 t capacity "
            "per 102MG mine-storage pile. No source document confirms this "
            "capacity; it is independent of and must never be derived from "
            "the confirmed 494,274 t 110/120LG homogenized-ore total"),
        "LOU_MINE_102MG_DISPLAY_START_MASS_T": {
            lane_id: _LOUTA_MINE_DEMO_START_MASS_T
            for lane_id in _LOUTA_MINE_LANE_IDS
        },
        "LOU_MINE_102MG_DISPLAY_START_MASS_BASIS": (
            "ASSUMED static t0 demonstration baseline explicitly adopted "
            "26-Jul-2026: 50,000 t per pile = 50% of the assumed 100,000 t "
            "display capacity. Not an SMS snapshot, timestamped telemetry "
            "or solver inventory"),
        "LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS": {
            pile_id: [dict(component) for component in components]
            for pile_id, components
            in _LOUTA_MINE_DISPLAY_GRADE_COMPONENTS.items()
        },
        "LOU_MINE_102MG_DISPLAY_GRADE_BASIS": (
            "Shared raw BT grade catalogue; current chemistry values from "
            "Q80130-LOU-L02-PR-BOD-0001 Rev B, 12-Apr-2023, §6.1 "
            "pp. 14-15 (Global / High-Cd / Low-Cd). The chemistry is "
            "source-backed; its assignment to 102MG piles is not"),
        "LOU_MINE_102MG_DISPLAY_GRADE_COMPOSITION_BASIS": (
            "ASSUMED display-only mapping explicitly adopted 26-Jul-2026: "
            "102MG-SP-01 = 50% Global + 50% High-Cd; 102MG-SP-02 = 50% "
            "Global + 50% Low-Cd. Each 25,000 t band occupies a schematic "
            "R0-R250 / R250-R500 range at 50% fill; R500-R1000 is empty. "
            "Not surveyed spatial order, stacking chronology, reclaim "
            "sequence or an SMS/SQMS segment snapshot"),
        "LOU_ORE_TOTAL_CAPACITY_T": _LOUTA_ORE_TOTAL_CAPACITY_T,
        "LOU_ORE_DISPLAY_CAPACITY_T": {
            lane_id: _LOUTA_ORE_LANE_CAPACITY_T
            for lane_id in _LOUTA_ORE_LANE_IDS
        },
        "LOU_ORE_DISPLAY_CAPACITY_BASIS": (
            "Louta BoD Rev B: total homogenized ore-storage capacity "
            "494,274 t CONFIRMED; the BoD gives no individual pile "
            "capacities, so the equal two-pile split (2 × 247,137 t) is "
            "DERIVED. The two-pile count itself is CONFIRMED by the process "
            "description (§3.1.1: 110LG-ST-01 with boom conveyor "
            "110LG-BC-01 forms two homogenized stockpiles by chevron "
            "stacking)"),
        "LOU_ORE_DISPLAY_START_MASS_T": {
            lane_id: _LOUTA_ORE_LANE_START_MASS_T
            for lane_id in _LOUTA_ORE_LANE_IDS
        },
        "LOU_ORE_DISPLAY_START_MASS_BASIS": (
            "ASSUMED demonstration baseline: 50% of each DERIVED 247,137 t "
            "pile capacity = 123,568.5 t per pile (247,137 t total ore "
            "inventory at t0). Static scenario display value, not an SMS "
            "snapshot or timestamped telemetry"),
        "LOU_ORE_DISPLAY_GRADE_COMPONENTS": {
            pile_id: [dict(component) for component in components]
            for pile_id, components
            in _LOUTA_ORE_DISPLAY_GRADE_COMPONENTS.items()
        },
        "LOU_ORE_DISPLAY_GRADE_BASIS": (
            "Shared raw BT grade catalogue; current chemistry values from "
            "Q80130-LOU-L02-PR-BOD-0001 Rev B, 12-Apr-2023, §6.1 pp. 14–15 "
            "(Global / High-Cd / Low-Cd) — the same three ore cases the "
            "process description names on p. 5. Chemistry is taken verbatim, "
            "including the Louta 15 % moisture; nothing is invented"),
        "LOU_ORE_DISPLAY_GRADE_COMPOSITION_BASIS": (
            "ASSUMED display distribution (single source of this claim): "
            "110LG-OL1 = 50% Global + 50% High-Cd and 110LG-OL2 = 50% "
            "Global + 50% Low-Cd by mass, so all three documented profiles "
            "appear across the two homogenized piles. Bands are contiguous "
            "and mass-proportional at display repères (R0–R250 / R250–R500 "
            "at the 50% fill) — they are NOT surveyed spatial order, "
            "stacking chronology, a reclaim sequence or an SQMS segment "
            "snapshot. The bands are display composition only; the solver "
            "uses the model-backed pile inventory and its single aggregate/"
            "designated campaign grade"),
        # Generic display contract for modelled piles containing multiple
        # ore/product-grade profiles. These mass shares are deliberately not
        # Scenario.pile_zones: no spatial boundaries, longitudinal order or
        # reclaim-face sequence has been supplied.
        "PILE_GRADE_DISTRIBUTIONS": {
            pile_id: [dict(component) for component in components]
            for pile_id, components
            in {
                **_LOUTA_ORE_DISPLAY_GRADE_COMPONENTS,
                **_LOUTA_PRODUCT_GRADE_DISTRIBUTIONS,
            }.items()
        },
        "PILE_GRADE_DISTRIBUTION_BASIS": {
            "110LG-OL1": (
                "ASSUMED demonstration composition: 50% Global + 50% "
                "High-Cd by mass. The profiles are source-backed; the shares "
                "are schematic/non-spatial and do not define a reclaim "
                "sequence. Solver inventory is model-backed and planning "
                "uses the pile's aggregate/designated campaign grade."
            ),
            "110LG-OL2": (
                "ASSUMED demonstration composition: 50% Global + 50% "
                "Low-Cd by mass. The profiles are source-backed; the shares "
                "are schematic/non-spatial and do not define a reclaim "
                "sequence. Solver inventory is model-backed and planning "
                "uses the pile's aggregate/designated campaign grade."
            ),
            "LT-P1": (
                "ASSUMED demonstration composition: 60% TBT + 40% BT by "
                "mass. TBT/BT quality vectors and their Louta product-pile "
                "mapping remain TBC. Shares are schematic and non-spatial; "
                "they do not define longitudinal order, boundaries or a "
                "reclaim sequence."
            ),
            "LT-P2": (
                "ASSUMED demonstration composition: 60% BT + 40% MT by "
                "mass. BT/MT quality vectors and their Louta product-pile "
                "mapping remain TBC. Shares are schematic and non-spatial; "
                "they do not define longitudinal order, boundaries or a "
                "reclaim sequence."
            ),
        },
        "CORRIDOR_DISPATCH_TARGET_T": {
            "C1": _LOUTA_S0_C1_TARGET_T,
        },
        "PROCESS_STAGE_OUTPUT_TARGET_T":
            dict(_LOUTA_S0_LINE_TARGET_T),
        "PROCESS_NO_PASSTHROUGH_SPACES": [
            "LT-WASH-L1-WIP", "LT-WASH-L2-WIP",
        ],
        "TERMINAL_INVENTORY_MIN_T": {},
        "TERMINAL_INVENTORY_EXACT_T": {
            # Stocked-route replenishment restores each assumed planning lane
            # to its opening mass; it does not grow an arbitrary terminal pile.
            "110LG-OL1": _LOUTA_ORE_LANE_START_MASS_T,
            "110LG-OL2": _LOUTA_ORE_LANE_START_MASS_T,
            "LT-P1": _LOUTA_PRODUCT_START_MASS_T,
            "LT-P2": _LOUTA_PRODUCT_START_MASS_T,
            "LT-WASH-L1-WIP": 0.0,
            "LT-WASH-L2-WIP": 0.0,
        },
        "FLOW_TONNE_PENALTY": 1,
        "CAPACITY_ENVELOPE": "nominal",   # design (3,120/2,520/1,200) only by
        #                                 # an explicit envelope dataset
    })
    sc.spaces += [
        StockSpace("BG-P1", "BENGUERIR", "mine-yard", "open", 191_512, 150_000,
                   "TBT", x_from=0, x_to=500, safety_stock_t=30_000,
                   material_class="washed", profile_id="BEN-SETTLED-PRODUCT",
                   evidence=CONF(_B02,
                                 "§2.3.3: 2 washed-phosphate stockpiles × "
                                 "191,512 t (total 383,024 t); contents/"
                                 "initial masses ASSUMED; combined-yard "
                                 "theoretical autonomy ≈6.1 d nominal / "
                                 "≈5.1 d design — BoD table states 5/6 d "
                                 "(earlier datasheet's ≈10-day claim kept as "
                                 "a DOCUMENT CONFLICT); '3 qualities on 2 "
                                 "piles' comes from the earlier source only "
                                 "— operating rule TBC; washed-product "
                                 "moisture 23 % after filtration / 15–20 % "
                                 "after settling (grade-vector 12 % is a "
                                 "generic placeholder, NOT a Benguerir "
                                 "value)")),
        StockSpace("BG-P2", "BENGUERIR", "mine-yard", "open", 191_512, 140_000,
                   "BT", x_from=0, x_to=500, safety_stock_t=30_000,
                   material_class="washed", profile_id="BEN-SETTLED-PRODUCT",
                   evidence=CONF(_B02, "as BG-P1")),
    ]
    _b02_note = ("CURRENT CONFIGURATION per project-team instruction; "
                 "B02 '**' retained as source notation only"
                 if ben_b02_enabled else
                 "CURRENT CONFIGURATION disabled only for a non-baseline "
                 "sensitivity: reference asset, planner must not use it")
    sc.machines += [
        Machine("RC2", "BENGUERIR", "reclaimer", 500, 2600,
                ["BG-P1", "BG-P2"], rail="BG-RL-P1", position_x=250,
                physical_tag="RC2", lifecycle="EXISTING",
                planning_enabled=ben_b02_enabled, rate_design_tph=3120,
                evidence=CONF(_B02,
                              "§2.3.2: bucket-wheel reclaimer RC2** — "
                              f"{_b02_note}; 2,600 nominal / 3,120 design "
                              "t/h; min stable 500 t/h ASSUMED (not in the "
                              "BoD); pile reachability ASSUMED both lanes "
                              "(no GA); reclaim conveyors RL7/RL8 "
                              "(+RL9/RL9′/RL10/RL11 optional) → RL12/RL13")),
        Machine("RC3", "BENGUERIR", "reclaimer", 500, 2600,
                ["BG-P1", "BG-P2"], rail="BG-RL-P2", position_x=250,
                physical_tag="RC3", lifecycle="EXISTING",
                planning_enabled=ben_b02_enabled, rate_design_tph=3120,
                evidence=CONF(_B02,
                              "§2.3.2: bucket-wheel reclaimer RC3** — "
                              f"{_b02_note}; 2,600 nominal / 3,120 design "
                              "t/h; min stable 500 t/h ASSUMED (not in the "
                              "BoD); pile reachability ASSUMED both lanes "
                              "(no GA); reclaim conveyors RL7/RL8 "
                              "(+RL9/RL9′/RL10/RL11 optional) → RL12/RL13")),
        Machine("ST3", "BENGUERIR", "stacker", 0, 2100,
                ["BG-P1", "BG-P2"], rail="BG-SL", position_x=250,
                physical_tag="ST3", lifecycle="EXISTING",
                planning_enabled=(ben_b02_enabled and ben_st3_stacking),
                rate_design_tph=2520,
                evidence=CONF(_B02,
                              "§2.3.2: 'Twice boom stacker (ST3**)' — ONE "
                              "twin-boom machine (not two stackers), with "
                              "tripper conveyors SL5**/SL6**; "
                              f"{_b02_note}; 2,100 nominal / 2,520 design "
                              "t/h. The six-pile S0 chain plans recovered "
                              "washing output through ST3; line-to-pile "
                              "campaign mapping remains ASSUMED.")),
    ]
    sc.nodes += [
        Node("SPACE:BG-P1", "BENGUERIR", "space", "BG-P1", 0, 8),
        Node("SPACE:BG-P2", "BENGUERIR", "space", "BG-P2", 0, 9),
        Node("HUB:BG-OUT", "BENGUERIR", "tower",
             "BG product reclaim collect (RL12/RL13)", 1, 8),
        Node("HUB:C2-HEAD", "BENGUERIR", "tower",
             "C2 corridor head — ONE physical corridor", 2, 8),
        Node("SRC:BG-RAW", "BENGUERIR", "source",
             "BBE raw/screened mine storage — Table 20 confirms 400,000 t "
             "total live yard capacity; project-team instruction 22-Jul-2026 "
             "sets BG-RAW-1 = 200,000 t and BG-RAW-2 = 200,000 t capacity; "
             "project-team current-state instruction 24-Jul-2026 sets "
             "BG-RAW-1 = 100,000 t and BG-RAW-2 = 100,000 t starting "
             "mass. In the six-pile built-in these are solver-backed "
             "inventories; the demonstration uses the shared mine-neutral "
             "Global grade on BG-RAW-1 and Low-Cd on BG-RAW-2. Multiple "
             "profiles may physically coexist, but no surveyed segment "
             "sequence or live/pile-specific Benguerir assay is available. "
             "Physical tags and metric GA remain TBC; the starting masses "
             "are scenario inputs, not timestamped telemetry. This aggregate "
             "source node is retained only for the conditional raw-to-C2 "
             "route.",
             0, 7),
        Node("DEST:BG-WASH-L1-FEED", "BENGUERIR", "dest",
             "Operating washing line 1 feed interface — executable raw-yard "
             "route TBC", 1, 7),
        Node("DEST:BG-TRAIN-PROD", "BENGUERIR", "dest",
             "Train loading station — washed product", 2, 9),
        Node("DEST:BG-TRAIN-RAW", "BENGUERIR", "dest",
             "Train loading station — raw phosphate", 1, 7),
    ]
    sc.links += [
        Link("L-BG-P1-OUT", "SPACE:BG-P1", "HUB:BG-OUT", capacity_tph=2600,
             capacity_design_tph=3120, material_class="washed",
             evidence=CONF(_B02, "§2.3.2: product reclaim conveyors RL7/RL8 "
                                 "(+optional RL9/RL9′/RL10/RL11) → RL12/RL13 "
                                 "— per-pile model form; 2,600/3,120 t/h")),
        Link("L-BG-P2-OUT", "SPACE:BG-P2", "HUB:BG-OUT", capacity_tph=2600,
             capacity_design_tph=3120, material_class="washed",
             evidence=CONF(_B02, "as L-BG-P1-OUT")),
        Link("L-BG-PROD-C2", "HUB:BG-OUT", "HUB:C2-HEAD", capacity_tph=2600,
             capacity_design_tph=3120, material_class="washed",
             evidence=CONF(_B02, "§2.6: BBE feeds the MPH corridor with "
                                 "phosphate PRODUCT (2,600/3,120 t/h) from "
                                 "the new washed stockpiles")),
        Link("L-BG-RAW-C2", "SRC:BG-RAW", "HUB:C2-HEAD", capacity_tph=2600,
             capacity_design_tph=3120, material_class="raw",
             available=ben_raw_to_c2, lifecycle="OPTIONAL_PROPOSED",
             evidence=CONF(_B02,
                           "§2.6: phosphate ORE (2,600/3,120 t/h) directly "
                           "from mine storage — CONDITIONAL on modification "
                           "of the existing phase-1 250 m³ hopper and/or "
                           "optional conveyors RB9**/RB10**; activated only "
                           "by BEN_RAW_TO_C2_ENABLED (default false); "
                           "campaign/changeover behaviour TBC — no cleaning/"
                           "transition times invented")),
        Link("C2", "HUB:C2-HEAD", "TOWER:M16-REC", capacity_tph=2600,
             capacity_design_tph=3120,
             transit_buckets=CORRIDOR_TRANSIT_BUCKETS["C2"],
             evidence=TBC("capacity: " + _B02 + "; proximity: "
                          + _CORRIDOR_CORRECTION,
                          "Benguerir/C2 is FARTHER than Louta/C1 "
                          "(USER_CONFIRMED). Capacity 2,600 t/h nominal / "
                          "3,120 design is documented by B02; the existing "
                          "60 km value is provisionally reassigned to C2, "
                          "giving ≈4 h 10 ≈ 4 buckets at 4 m/s. Exact route "
                          "length, effective speed and transfer dwell remain "
                          "TBC. Raw and washed alternatives share this ONE "
                          "physical corridor envelope")),
        Link("L-BG-TRAIN-PROD", "HUB:BG-OUT", "DEST:BG-TRAIN-PROD",
             capacity_tph=1000, capacity_design_tph=1200,
             material_class="washed", shared_group="BG-TRAIN-STATION",
             evidence=CONF(_B02, "§2.6 loading station: phosphate PRODUCT "
                                 "1,000/1,200 t/h; whether the job-"
                                 "allocation layer controls train demand is "
                                 "SCOPE_TBC; one loading-station resource "
                                 "ASSUMED shared with the raw route — "
                                 "simultaneous use TBC")),
        Link("L-BG-TRAIN-RAW", "SRC:BG-RAW", "DEST:BG-TRAIN-RAW",
             capacity_tph=1000, capacity_design_tph=1200,
             material_class="raw", shared_group="BG-TRAIN-STATION",
             evidence=CONF(_B02, "§2.6 loading station: phosphate ORE "
                                 "1,000/1,200 t/h; no modelled raw inventory "
                                 "— reference route; SCOPE_TBC as "
                                 "L-BG-TRAIN-PROD")),
    ]
    sc.destinations += [
        Destination(
            "BEN-WASH-L1", "BENGUERIR", "DEST:BG-WASH-L1-FEED",
            priority=1, demand_default_tph=0.0,
            requested_grade="RAW-BT-GLOBAL",
            demand_kind="process_feed", process_stage="BEN-WASH-L1",
            evidence=TBC(
                _B02 + " + project-team operating-status instruction "
                "22-Jul-2026",
                "Line 1 is the operating washing line and uses the shared "
                "raw-grade catalogue. Default demand remains zero because "
                "the raw-yard inventory, machine and executable feed route "
                "are not yet modelled; no design capacity is invented as an "
                "order.")),
        Destination("BG-TRAIN-PROD", "BENGUERIR", "DEST:BG-TRAIN-PROD",
                    priority=3, demand_default_tph=0,
                    requested_grade="TBT",
                    evidence=ASSUME(
                        _B02 + " + project-team planning request 28-Jul-2026: "
                        "the washed-product loading interface is documented "
                        "at 1,000/1,200 t/h. Demand quantity and grade are "
                        "editable demonstration-profile inputs, not an "
                        "approved train order; the default profile applies "
                        "12,000 t at 500 t/h.")),
        Destination("BG-TRAIN-RAW", "BENGUERIR", "DEST:BG-TRAIN-RAW",
                    priority=3, demand_default_tph=0,
                    evidence=CONF(_B02, "interface documented (1,000/1,200 "
                                        "t/h); planning control SCOPE_TBC; "
                                        "raw stage carries no modelled "
                                        "inventory in this pass")),
    ]
    _add_benguerir_planning_chain(sc)

    # ---- Louta stocked-route S0 chain ---------------------------------------
    # L01 now documents 102MG-ST-01/-02, shared 110MG-RC-01 and 110MG-CB-01
    # toward beneficiation, but not the live pile state, metric reach or an
    # executable mapping into the existing L02 solver graph. Two finite
    # interface inventories therefore remain the S0 source boundary without
    # double-counting the reference-only 102MG piles.
    sc.spaces += [
        StockSpace(
            "LT-MINE-FEED-GLOBAL", "LOUTA", "mine-interface", "closed",
            _LOUTA_DIRECT_MINE_FEED_T, _LOUTA_DIRECT_MINE_FEED_T,
            "RAW-BT-GLOBAL", x_from=0, x_to=1,
            material_class="raw_screened",
            profile_id="LOU-MINE-PHOSPHATE",
            evidence=ASSUME(
                "finite S0 handoff inventory at the documented mine "
                "transport inlet; not 102MG inventory or live telemetry")),
        StockSpace(
            "LT-MINE-FEED-LOW-CD", "LOUTA", "mine-interface", "closed",
            _LOUTA_DIRECT_MINE_FEED_T, _LOUTA_DIRECT_MINE_FEED_T,
            "RAW-BT-LOW-CD", x_from=0, x_to=1,
            material_class="raw_screened",
            profile_id="LOU-MINE-PHOSPHATE",
            evidence=ASSUME("as LT-MINE-FEED-GLOBAL; Low-Cd S0 campaign")),
        StockSpace(
            "110LG-OL1", "LOUTA", "mine-yard", "open",
            _LOUTA_ORE_LANE_CAPACITY_T, _LOUTA_ORE_LANE_START_MASS_T,
            "RAW-BT-GLOBAL", x_from=0, x_to=500,
            safety_stock_t=25_000, material_class="raw_screened",
            profile_id="LOU-MINE-PHOSPHATE",
            evidence=DERV(
                _LOU_BOD + " + " + _LOU_PD,
                "two homogenized piles confirmed; equal half of the "
                "494,274 t total is derived; 50% start and pile campaign "
                "assignment are assumed")),
        StockSpace(
            "110LG-OL2", "LOUTA", "mine-yard", "open",
            _LOUTA_ORE_LANE_CAPACITY_T, _LOUTA_ORE_LANE_START_MASS_T,
            "RAW-BT-LOW-CD", x_from=0, x_to=500,
            safety_stock_t=25_000, material_class="raw_screened",
            profile_id="LOU-MINE-PHOSPHATE",
            evidence=DERV(_LOU_BOD + " + " + _LOU_PD,
                          "as 110LG-OL1; Low-Cd S0 campaign assumed")),
        # Zero-start process output buffers make the 30-minute dependency and
        # mass recovery explicit.  They are not physical stockpiles and are
        # hidden from the yard geometry.
        StockSpace(
            "LT-WASH-L1-WIP", "LOUTA", "process-interface", "closed",
            10_000, 0, "TBT", state="empty", designated_grade="TBT",
            x_from=0, x_to=1, material_class="washed",
            profile_id="LOU-WASHED-PRODUCT",
            evidence=ASSUME(
                "solver process buffer for L1 output after the adopted "
                "30-minute minimum residence; not an on-ground pile")),
        StockSpace(
            "LT-WASH-L2-WIP", "LOUTA", "process-interface", "closed",
            10_000, 0, "BT", state="empty", designated_grade="BT",
            x_from=0, x_to=1, material_class="washed",
            profile_id="LOU-WASHED-PRODUCT",
            evidence=ASSUME("as LT-WASH-L1-WIP for L2")),
    ]

    # ---- Louta L02 phosphate-PRODUCT storage (130/140LG design basis) --------
    # DATA CORRECTION 18-Jul-2026: the former LT-RC-01 2100 t/h placeholder is
    # replaced by the documented product reclaimers 140LG-RC-01/-02 (3,000 t/h
    # nominal each, L02 design basis / handling philosophy — doc refs TBC).
    # LT-P1/P2 remain the two schematic PRODUCT lanes: total product capacity
    # 383,024 t is a design-basis figure; the equal 191,512 t split and the
    # two-lane count are DERIVED/TBC (pile count configurable).
    sc.spaces += [
        StockSpace(
            "LT-P1", "LOUTA", "mine-yard", "open",
            _LOUTA_PRODUCT_CAPACITY_T, _LOUTA_PRODUCT_START_MASS_T, "TBT",
            quality=dict(_LOUTA_PRODUCT_COMPOSITE_QUALITY["LT-P1"]),
            state="mixed", x_from=0, x_to=500, safety_stock_t=25_000,
            material_class="washed", profile_id="LOU-WASHED-PRODUCT",
            evidence=DERV(
                _LOU_DB,
                "total product capacity 383,024 t confirmed (design basis); "
                "equal two-lane split DERIVED/TBC — lane count & geometry "
                "configurable; starting mass fixed at 50% of capacity for "
                "the demonstration; mixed grade distribution and composite "
                "quality ASSUMED/TBC")),
        StockSpace(
            "LT-P2", "LOUTA", "mine-yard", "open",
            _LOUTA_PRODUCT_CAPACITY_T, _LOUTA_PRODUCT_START_MASS_T, "BT",
            quality=dict(_LOUTA_PRODUCT_COMPOSITE_QUALITY["LT-P2"]),
            state="mixed", x_from=0, x_to=500, safety_stock_t=25_000,
            material_class="washed", profile_id="LOU-WASHED-PRODUCT",
            evidence=DERV(
                _LOU_DB,
                "as LT-P1 — DERIVED/TBC split; 50% starting fill, mixed "
                "grade distribution and composite quality ASSUMED/TBC")),
    ]
    sc.machines += [
        Machine(
            "110LG-ST-01", "LOUTA", "stacker", 0, 2100,
            ["110LG-OL1", "110LG-OL2"], rail="110LG-R",
            position_x=250, physical_tag="110LG-ST-01",
            lifecycle="SELECTED", planning_enabled=True,
            evidence=CONF(
                _LOU_PD,
                "p.7: one 2,100 t/h wet stacker with 110LG-TC-01 and "
                "110LG-BC-01 forms both homogenized piles by chevron")),
        Machine(
            "120LG-RC-01", "LOUTA", "reclaimer", 500, 3000,
            ["110LG-OL1"], rail="120LG-R1", position_x=250,
            physical_tag="120LG-RC-01", lifecycle="SELECTED",
            planning_enabled=True,
            evidence=DERV(
                _LOU_PD,
                "tag and 3,000 t/h wet route confirmed; OL1 pairing is the "
                "conservative applied S0 geometry pending a GA/reach matrix")),
        Machine(
            "120LG-RC-02", "LOUTA", "reclaimer", 500, 3000,
            ["110LG-OL2"], rail="120LG-R2", position_x=250,
            physical_tag="120LG-RC-02", lifecycle="SELECTED",
            planning_enabled=True,
            evidence=DERV(
                _LOU_PD,
                "tag and 3,000 t/h wet route confirmed; OL2 pairing is the "
                "conservative applied S0 geometry pending a GA/reach matrix")),
        Machine(
            "130LG-ST-01", "LOUTA", "stacker", 0,
            _LOUTA_PRODUCT_STACKER_S0_TPH,
            ["LT-P1", "LT-P2"], rail="130LG-R", position_x=250,
            physical_tag="130LG-ST-01", lifecycle="SELECTED",
            planning_enabled=True,
            evidence=ASSUME(
                "tag and shared SL11 product-stacking duty are confirmed by "
                "the process description; 1,000 t/h is an exposed S0 "
                "planning envelope because no nameplate rate is supplied")),
        Machine("140LG-RC-01", "LOUTA", "reclaimer", 500, 3000,
                ["LT-P1"], rail="140LG-RL", position_x=250,
                physical_tag="140LG-RC-01", lifecycle="SELECTED",
                evidence=DERV(_LOU_DB,
                              "tag CONFIRMED (L02 philosophy) · 3,000 t/h "
                              "nominal (design basis) · route 140LG-CV-03/RL1 "
                              "then flip gate to RL3/RL4 → C1 · pile-reach "
                              "matrix TBC (both lanes assumed reachable)")),
        Machine("140LG-RC-02", "LOUTA", "reclaimer", 500, 3000,
                ["LT-P2"], rail="140LG-RL", position_x=250,
                physical_tag="140LG-RC-02", lifecycle="SELECTED",
                evidence=DERV(_LOU_DB,
                              "tag CONFIRMED · 3,000 t/h nominal · route "
                              "140LG-CV-04/RL2 then flip gate to RL3/RL4 → "
                              "C1 · availability/flexibility — NOT additional "
                              "corridor throughput (C1 ≤ 2,600 t/h)")),
    ]
    sc.nodes += [
        Node("SPACE:LT-MINE-FEED-GLOBAL", "LOUTA", "source",
             "direct mine inlet handoff — Global case", 0, 15),
        Node("SPACE:LT-MINE-FEED-LOW-CD", "LOUTA", "source",
             "direct mine inlet handoff — Low-Cd case", 0, 16),
        Node("HUB:LT-ORE-STK", "LOUTA", "tower",
             "110LG-TC-01 / 110LG-BC-01 stacking transfer", 1, 15),
        Node("SPACE:110LG-OL1", "LOUTA", "space",
             "110LG homogenized ore pile 1", 2, 15),
        Node("SPACE:110LG-OL2", "LOUTA", "space",
             "110LG homogenized ore pile 2", 2, 16),
        Node("HUB:LT-RB45", "LOUTA", "tower",
             "RB4/RB5 common ore transfer", 3, 15),
        Node("HUB:LT-WASH-SPLIT", "LOUTA", "gate",
             "RB6/RB7 washing-line selection", 4, 15),
        Node("SPACE:LT-WASH-L1-WIP", "LOUTA", "space",
             "L1 recovered-product process buffer", 5, 14),
        Node("SPACE:LT-WASH-L2-WIP", "LOUTA", "space",
             "L2 recovered-product process buffer", 5, 16),
        Node("HUB:LT-SL11", "LOUTA", "tower",
             "shared 130LG-CV-11 / SL11 product stacking transfer", 6, 15),
        Node("SPACE:LT-P1", "LOUTA", "space",
             "LT-P1 — L02 product lane 1 (schematic abstraction)", 0, 11),
        Node("SPACE:LT-P2", "LOUTA", "space",
             "LT-P2 — L02 product lane 2 (schematic abstraction)", 0, 12),
        Node("HUB:LT-OUT", "LOUTA", "tower",
             "140LG reclaim routes RL1/RL2 → flip gates → RL3/RL4", 1, 11),
    ]
    sc.links += [
        Link("L-LT-MINE-G-IN", "SPACE:LT-MINE-FEED-GLOBAL",
             "HUB:LT-ORE-STK", capacity_tph=2100,
             material_class="raw",
             evidence=CONF(_LOU_PD,
                           "direct mine inlet to the 110LG stacking system")),
        Link("L-LT-MINE-LC-IN", "SPACE:LT-MINE-FEED-LOW-CD",
             "HUB:LT-ORE-STK", capacity_tph=2100,
             material_class="raw",
             evidence=CONF(_LOU_PD, "as Global campaign inlet")),
        Link("L-LT-STK-OL1", "HUB:LT-ORE-STK", "SPACE:110LG-OL1",
             capacity_tph=2100, material_class="raw",
             evidence=CONF(_LOU_PD,
                           "110LG-BC-01 chevron stacking to ore pile 1")),
        Link("L-LT-STK-OL2", "HUB:LT-ORE-STK", "SPACE:110LG-OL2",
             capacity_tph=2100, material_class="raw",
             evidence=CONF(_LOU_PD,
                           "110LG-BC-01 chevron stacking to ore pile 2")),
        Link("L-LT-OL1-RB", "SPACE:110LG-OL1", "HUB:LT-RB45",
             capacity_tph=3000, material_class="raw",
             evidence=CONF(_LOU_PD,
                           "120LG-RC-01 via RB1/RB2 and FG-01 to RB4/RB5")),
        Link("L-LT-OL2-RB", "SPACE:110LG-OL2", "HUB:LT-RB45",
             capacity_tph=3000, material_class="raw",
             evidence=CONF(_LOU_PD,
                           "120LG-RC-02 via RB3 and FG-04 to RB4/RB5")),
        Link("L-LT-RB45-COMMON", "HUB:LT-RB45", "HUB:LT-WASH-SPLIT",
             capacity_tph=588, material_class="raw",
             evidence=DERV(
                 _LOU_BOD + " + " + _LOU_PD,
                 "RB4/RB5 belt rating is absent; S0 conservatively uses "
                 "the maximum documented single washing-line feed, 588 t/h, "
                 "and selects one ore reclaimer per bucket")),
        Link("L-LT-RB6-L1", "HUB:LT-WASH-SPLIT",
             "SPACE:LT-WASH-L1-WIP",
             capacity_tph=_LOUTA_WASH_CASES["GLOBAL"]["feed_tph"],
             material_class="raw",
             evidence=CONF(
                 _LOU_BOD + " + " + _LOU_PD,
                 "RB6 to L1; Global-case scrubber feed 584 t/h")),
        Link("L-LT-RB7-L2", "HUB:LT-WASH-SPLIT",
             "SPACE:LT-WASH-L2-WIP",
             capacity_tph=_LOUTA_WASH_CASES["LOW_CD"]["feed_tph"],
             material_class="raw",
             evidence=CONF(
                 _LOU_BOD + " + " + _LOU_PD,
                 "RB7 to L2; Low-Cd-case scrubber feed 588 t/h")),
        Link("L-LT-WIP1-SL11", "SPACE:LT-WASH-L1-WIP",
             "HUB:LT-SL11", capacity_tph=_LOUTA_PRODUCT_STACKER_S0_TPH,
             shared_group="LOU-SL11", material_class="washed",
             evidence=ASSUME(
                 "L1 recovered product to shared SL11; 1,000 t/h S0 "
                 "planning envelope because the belt rating is absent")),
        Link("L-LT-WIP2-SL11", "SPACE:LT-WASH-L2-WIP",
             "HUB:LT-SL11", capacity_tph=_LOUTA_PRODUCT_STACKER_S0_TPH,
             shared_group="LOU-SL11", material_class="washed",
             evidence=ASSUME("as L-LT-WIP1-SL11 for L2")),
        Link("L-LT-SL11-P1", "HUB:LT-SL11", "SPACE:LT-P1",
             capacity_tph=_LOUTA_PRODUCT_STACKER_S0_TPH,
             shared_group="LOU-SL11", material_class="washed",
             evidence=ASSUME(
                 "S0 campaign maps L1 output to product planning line 1; "
                 "physical product-pile reach is not stated")),
        Link("L-LT-SL11-P2", "HUB:LT-SL11", "SPACE:LT-P2",
             capacity_tph=_LOUTA_PRODUCT_STACKER_S0_TPH,
             shared_group="LOU-SL11", material_class="washed",
             evidence=ASSUME("S0 campaign maps L2 output to line 2")),
        Link("L-LT-P1-OUT", "SPACE:LT-P1", "HUB:LT-OUT", capacity_tph=2600,
             evidence=DERV(_LOU_DB,
                           "per-pile model form of the documented reclaim "
                           "routes 140LG-CV-03/RL1 · 140LG-CV-04/RL2 + flip "
                           "gates to RL3/RL4; capacity capped at the C1 "
                           "corridor interface 2,600 t/h")),
        Link("L-LT-P2-OUT", "SPACE:LT-P2", "HUB:LT-OUT", capacity_tph=2600,
             evidence=DERV(_LOU_DB, "as L-LT-P1-OUT")),
        Link("C1", "HUB:LT-OUT", "TOWER:M16-REC", capacity_tph=2600,
             transit_buckets=CORRIDOR_TRANSIT_BUCKETS["C1"],
             evidence=TBC("capacity: TBE/PFD; proximity: "
                          + _CORRIDOR_CORRECTION,
                          "Louta/C1 is NEARER than Benguerir/C2 "
                          "(USER_CONFIRMED). Capacity 2,600 t/h remains the "
                          "documented planning envelope; the existing 30 km "
                          "value is provisionally reassigned to C1, giving "
                          "≈2 h 05 ≈ 2 buckets at 4 m/s. Exact route length, "
                          "effective speed and transfer dwell remain TBC")),
    ]

    # Explicit S0 Louta stocked-route transfers. These are process-chain
    # candidates, not demand jobs invented to exercise equipment. The visible
    # S0 policy below supplies their inventory / output purpose.
    sc.process_transfers += [
        ProcessTransfer(
            "LOU-ORE-STACK-GLOBAL", "110LG-ST-01",
            "LT-MINE-FEED-GLOBAL", "110LG-OL1",
            ["L-LT-MINE-G-IN", "L-LT-STK-OL1"],
            operation_kind="stack",
            process_stage="LOU-ORE-STACK",
            output_grade="RAW-BT-GLOBAL",
            purpose="Restore ore-line 1 inventory consumed by washing line L1",
            evidence=ASSUME(
                "S0 campaign maps the Global raw case to ore pile 1. "
                "110LG-ST-01 and its 2,100 t/h wet design rate are documented; "
                "the campaign-to-pile allocation is not.")),
        ProcessTransfer(
            "LOU-ORE-STACK-LOW-CD", "110LG-ST-01",
            "LT-MINE-FEED-LOW-CD", "110LG-OL2",
            ["L-LT-MINE-LC-IN", "L-LT-STK-OL2"],
            operation_kind="stack",
            process_stage="LOU-ORE-STACK",
            output_grade="RAW-BT-LOW-CD",
            purpose="Restore ore-line 2 inventory consumed by washing line L2",
            evidence=ASSUME(
                "S0 campaign maps the Low-Cd raw case to ore pile 2; "
                "the campaign-to-pile allocation is not documented.")),
        ProcessTransfer(
            "LOU-WASH-L1-GLOBAL", "120LG-RC-01",
            "110LG-OL1", "LT-WASH-L1-WIP",
            ["L-LT-OL1-RB", "L-LT-RB45-COMMON", "L-LT-RB6-L1"],
            operation_kind="reclaim",
            process_stage="LOU-WASH-L1",
            output_grade="TBT",
            yield_factor=_LOUTA_WASH_CASES["GLOBAL"]["recovery"],
            residence_min=30,
            purpose="Produce the configured line-L1 stocked-route allocation",
            evidence=ASSUME(
                "The Global-case 584 t/h feed and 68.22% recovery are "
                "documented. Mapping that case to L1/TBT and RC-01/pile 1, "
                "plus the 30-minute overall residence, are S0 assumptions.")),
        ProcessTransfer(
            "LOU-WASH-L2-LOW-CD", "120LG-RC-02",
            "110LG-OL2", "LT-WASH-L2-WIP",
            ["L-LT-OL2-RB", "L-LT-RB45-COMMON", "L-LT-RB7-L2"],
            operation_kind="reclaim",
            process_stage="LOU-WASH-L2",
            output_grade="BT",
            yield_factor=_LOUTA_WASH_CASES["LOW_CD"]["recovery"],
            residence_min=30,
            purpose="Produce the configured line-L2 stocked-route allocation",
            evidence=ASSUME(
                "The Low-Cd-case 588 t/h feed and 67.71% recovery are "
                "documented. Mapping that case to L2/BT and RC-02/pile 2, "
                "plus the 30-minute overall residence, are S0 assumptions.")),
        ProcessTransfer(
            "LOU-PRODUCT-STACK-L1", "130LG-ST-01",
            "LT-WASH-L1-WIP", "LT-P1",
            ["L-LT-WIP1-SL11", "L-LT-SL11-P1"],
            operation_kind="stack",
            process_stage="LOU-PRODUCT-STACK-L1",
            output_grade="TBT",
            yield_factor=1.0,
            depends_on_stage="LOU-WASH-L1",
            dependency_delay_min=30,
            purpose="Replenish product line 1 after the modelled washing delay",
            evidence=ASSUME(
                "The shared SL11/130LG-ST-01 stocked-product route is "
                "documented. L1-to-product-line-1 mapping, the 1,000 t/h "
                "planning envelope, and 30-minute dependency are S0 assumptions.")),
        ProcessTransfer(
            "LOU-PRODUCT-STACK-L2", "130LG-ST-01",
            "LT-WASH-L2-WIP", "LT-P2",
            ["L-LT-WIP2-SL11", "L-LT-SL11-P2"],
            operation_kind="stack",
            process_stage="LOU-PRODUCT-STACK-L2",
            output_grade="BT",
            yield_factor=1.0,
            depends_on_stage="LOU-WASH-L2",
            dependency_delay_min=30,
            purpose="Replenish product line 2 after the modelled washing delay",
            evidence=ASSUME(
                "As LOU-PRODUCT-STACK-L1 for the assumed L2/product-line-2 "
                "campaign.")),
    ]
    sc.destinations += [
        Destination(
            "LOU-WASH-L1", "LOUTA", "SPACE:LT-WASH-L1-WIP",
            priority=1,
            demand_default_tph=(
                _LOUTA_S0_LINE_FEED_TARGET_T["LOU-WASH-L1"]
                / sc.sim.horizon_buckets / sc.sim.dt_h),
            requested_grade="RAW-BT-GLOBAL",
            demand_kind="process_feed", process_stage="LOU-WASH-L1",
            evidence=DERV(
                _LOU_BOD + " + S0 stocked-route policy",
                "5,867 t is the exact input selected by the integer bucket "
                "model for the configured 4,000 t Global-case recovered "
                "output. It is a campaign requirement, not the documented "
                "584 t/h design-case capacity.")),
        Destination(
            "LOU-WASH-L2", "LOUTA", "SPACE:LT-WASH-L2-WIP",
            priority=1,
            demand_default_tph=(
                _LOUTA_S0_LINE_FEED_TARGET_T["LOU-WASH-L2"]
                / sc.sim.horizon_buckets / sc.sim.dt_h),
            requested_grade="RAW-BT-LOW-CD",
            demand_kind="process_feed", process_stage="LOU-WASH-L2",
            evidence=DERV(
                _LOU_BOD + " + S0 stocked-route policy",
                "5,914 t is the exact input selected by the integer bucket "
                "model for the configured 4,000 t Low-Cd-case recovered "
                "output. It is a campaign requirement, not the documented "
                "588 t/h design-case capacity.")),
    ]

    # initial in-transit material (report §15.3 eq. 5 belt_profile0)
    # CORRECTED 25-Jul-2026: the two corridors share ONE current 2,600 t/h
    # reception path (M16_RECEPTION_BASIS), so a pre-loaded profile that
    # delivers 2,600 t/h on C1 AND C2 into the same bucket describes a
    # physically impossible 5,200 t/h reception. The corridors are therefore
    # shown as having run ALTERNATELY before t0: C1 parcels arrive in the
    # buckets C2 leaves free. Belt rate stays the full 2,600 t/h; the
    # alternation is DERIVED from the shared reception envelope, not from
    # observed dispatch history (S3-A4).
    _c1_lag = CORRIDOR_TRANSIT_BUCKETS["C1"]
    _c2_lag = CORRIDOR_TRANSIT_BUCKETS["C2"]
    for t in range(_c1_lag):
        split = max(1, _c1_lag // 2)
        sc.belt_profile.append(BeltProfileEntry(
            "C1", t, 2600, "BT" if t < split else "TBT"))
    for t in range(_c1_lag, _c2_lag):          # C2 fills only the free buckets
        sc.belt_profile.append(BeltProfileEntry("C2", t, 2600, "TBT"))

    # Demand-driven mine replenishment baseline.  The terminal supply position
    # is M16 on-ground inventory plus material still travelling on C1/C2.
    # Capping that position at its t0 value means mine dispatch cannot create a
    # surplus merely to make an otherwise-idle machine look busy. S0 uses
    # explicit stocked-route targets above; it does not reward token activity.
    _m16_initial_stock_t = sum(
        space.mass_t for space in sc.spaces if space.site == "MPH")
    _initial_corridor_in_transit_t = sum(
        entry.mass_t for entry in sc.belt_profile
        if entry.link_id in {"C1", "C2"})
    sc.config.update({
        "M16_TERMINAL_SUPPLY_POSITION_POLICY":
            "not_above_initial_stock_plus_initial_in_transit",
        "M16_TERMINAL_SUPPLY_POSITION_MAX_T":
            _m16_initial_stock_t + _initial_corridor_in_transit_t,
        "M16_TERMINAL_SUPPLY_POSITION_BASIS": (
            "ASSUMED demonstration replenishment policy, 26-Jul-2026: at "
            "T=24 h, M16 on-ground inventory plus C1/C2 in-transit material "
            "must not exceed its t0 value. Late dispatch remains explicit "
            "terminal in-transit. This blocks surplus machine-activation "
            "tonnage while allowing demand/capacity-backed replenishment."),
        "MACHINE_COVERAGE_REWARD": 0,
    })

    # Benguerir process inflow is represented only by the executable
    # six-pile transfers added below; no parallel exogenous wash source.
    sc.exogenous = []
    sc.segments = [
        Segment("HP-02", 0, 150, 45_000, "BT", age_days=20),
        Segment("HP-02", 150, 300, 45_000, "BT", age_days=5),
        Segment("HP-05", 0, 300, 74_000, "TBT", age_days=12),
        Segment("BG-MINE-1", 0, 250, 100_000, "RAW-BT-GLOBAL",
                age_days=2),
        Segment("BG-MINE-2", 0, 250, 100_000, "RAW-BT-LOW-CD",
                age_days=2),
        Segment("BG-RAW-1", 0, 250, 100_000, "RAW-BT-GLOBAL",
                age_days=4),
        Segment("BG-RAW-2", 0, 250, 100_000, "RAW-BT-LOW-CD",
                age_days=4),
        Segment("BG-P1", 0, 500, 150_000, "TBT", age_days=8),
    ]
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=24, frozen_buckets=2, seed=42,
                       max_solve_s=8.0)  # deterministic-time units (~35 s wall)
    sc.weights = ObjectiveWeights()
    sc.assumptions = [
        Assumption("S3-A1", "Louta L02 product storage: total capacity "
                   "383,024 t is confirmed by the BoD. Its generic equipment "
                   "table lists ONE product reclaimer, while the later tagged "
                   "process description specifies 140LG-RC-01/-02 at "
                   "3,000 t/h nominal each; S0 uses the later tagged count. "
                   "The equal two-lane split (2 × 191,512 t), lane geometry "
                   "and RC-01→P1 / RC-02→P2 reach remain assumptions. The "
                   "demonstration starts both lanes "
                   "at 95,756 t (50% of capacity); this starting inventory "
                   "is ASSUMED, not a live SMS snapshot",
                   "LOUTA", source="L02 design basis / handling philosophy "
                   "(doc refs TBC)", placeholder="LT-P1/P2 · 140LG-RC-01/-02",
                   impact="Louta contribution is design-basis indicative",
                   action="approved GA/reach matrix, as-built equipment list "
                   "and live inventory snapshot"),
        Assumption(
            "S3-A21",
            "Louta product piles contain multiple grade profiles in the "
            "demonstration: LT-P1 = 60% TBT + 40% BT and LT-P2 = 60% BT + "
            "40% MT by mass. Aggregate StockSpace quality is the exact "
            "mass-weighted composite (LT-P1: BPL 65%, Cd 33 ppm, MgO "
            "1.28%, SiO2 3.22%, moisture 12%; LT-P2: BPL 70.4%, Cd 26.8 ppm, "
            "MgO 0.98%, SiO2 2.48%, moisture 12%). TBT/BT/MT vectors, "
            "profile-to-pile mapping and mass shares are ASSUMED/TBC. The "
            "distribution is schematic and non-spatial: no pile zones, "
            "longitudinal order, boundaries or reclaim-face sequence are "
            "asserted.",
            "LOUTA", status="ASSUMED",
            source="project-team multi-profile instruction 24-Jul-2026 + "
            "simulation-only TBT/BT/MT vectors",
            confidence="L",
            placeholder="config.PILE_GRADE_DISTRIBUTIONS + aggregate "
            "StockSpace.quality; dominant grades LT-P1=TBT, LT-P2=BT",
            impact="Louta starting quality and planning/simulation quality "
            "calculations",
            action="replace with timestamped Louta SMS/SQMS segment masses, "
            "approved product-grade vectors/mapping and surveyed spatial "
            "order before enabling solver zones"),
        Assumption("S3-A8", "110/120LG ORE-storage machine count — SOURCE "
                   "DISCREPANCY resolved in favour of the later equipment-"
                   "tagged process description: the generic Louta BoD Rev B "
                   "table lists ONE ore reclaimer, while the PFD-linked "
                   "description specifies ONE ore stacker 110LG-ST-01 "
                   "(2,100 t/h wet) and TWO bridge reclaimers "
                   "120LG-RC-01/-02 (3,000 t/h wet each). Those three assets "
                   "are current solver resources in S0. The source confirms "
                   "two piles and two reclaimers but not their physical "
                   "pairing; S0 conservatively maps RC-01→OL1 and RC-02→OL2 "
                   "and permits only one ore reclaimer per hourly bucket. "
                   "The process-description cover/body document numbers also "
                   "differ (REP-00002 vs REP-00004).",
                   "LOUTA", status="DERIVED", confidence="M",
                   source=_LOU_PD + " (used for tagged equipment count) + "
                   + _LOU_BOD + " (conflicting generic table)",
                   placeholder="Scenario machines 110LG-ST-01 and "
                   "120LG-RC-01/-02; assumed one-to-one pile reach",
                   impact="ore stacking, washing feed, route exclusivity and "
                   "machine coverage",
                   action="obtain the approved GA/reach matrix and resolve "
                   "both equipment-count and document-number discrepancies"),
        Assumption("S3-A23", "110/120LG homogenized ore storage: the Louta "
                   "BoD Rev B confirms 494,274 t TOTAL homogenized ore-"
                   "storage capacity and gives NO individual pile "
                   "capacities, so the equal two-pile split (2 × 247,137 t) "
                   "is DERIVED. The demonstration starts each pile at 50% "
                   "(123,568.5 t per pile ≈ 123.6 kt; 247,137 t total) — an "
                   "ASSUMED planning baseline, not an SMS snapshot. "
                   "Each pile shows contiguous mass-proportional bands of "
                   "the shared raw BT profiles (110LG-OL1 = 50% "
                   "Global + 50% High-Cd; 110LG-OL2 = 50% Global + 50% "
                   "Low-Cd) so all three documented ore cases appear; the "
                   "chemistry is verbatim from the BoD §6.1 but the SHARES, "
                   "band widths and pile mapping are ASSUMED. Bands are not "
                   "surveyed spatial order, stacking chronology or a reclaim "
                   "sequence. Both piles are now Scenario.spaces and participate "
                   "in the S0 mass balance; the stocked-route policy restores "
                   "each terminal mass exactly to its opening value. "
                   "The 494,274 t capacity belongs to these 110LG piles and "
                   "is NEVER attributed to the 102MG mine-storage area.",
                   "LOUTA", status="ASSUMED", confidence="L",
                   source="Louta BoD Rev B (total capacity + §6.1 profiles) + "
                   + _LOU_PD + " (two-stockpile count)",
                   placeholder="110LG-OL1/OL2 StockSpace rows + "
                   "config.LOU_ORE_DISPLAY_* presentation distribution",
                   impact="solver inventory, ore-stage jobs, KPIs and layout",
                   action="obtain individual pile capacities, a pile survey / "
                   "SQMS segment snapshot, approved reach matrix and ore "
                   "campaign rules before operational use"),
        Assumption(
            "S3-A25",
            "102MG PRE-beneficiation mine storage has an explicitly ASSUMED "
            "display-only demonstration baseline: 102MG-SP-01 and "
            "102MG-SP-02 each use a round 100,000 t placeholder capacity "
            "and a static 50,000 t starting mass (50%). SP-01 displays 50% "
            "Global + 50% High-Cd raw BT; SP-02 displays 50% Global + 50% "
            "Low-Cd, so each component is 25,000 t at schematic display "
            "repères R0-R250 / R250-R500 and R500-R1000 remains empty. "
            "Only the grade chemistry comes from the Louta BoD §6.1; the "
            "capacity, mass, shares, mapping and positions are project-team "
            "demonstration assumptions adopted 26-Jul-2026, not source "
            "facts or live SMS/SQMS data. The two lanes stay absent from "
            "Scenario.spaces, solver, inventory mass balance, jobs and KPIs, "
            "and remain static in Initial/Final views. The confirmed "
            "494,274 t capacity belongs exclusively to the later 110/120LG "
            "homogenized-ore stage and is not reused here.",
            "LOUTA", status="ASSUMED", confidence="L",
            source="project-team explicit demonstration-baseline instruction "
            "26-Jul-2026 + Louta BoD Rev B §6.1 raw profiles",
            placeholder="config.LOU_MINE_102MG_DISPLAY_* (display only)",
            impact="mimic and grade-distribution display only; no solver, KPI "
            "or plan effect",
            action="replace the placeholder capacity, t0 mass, profile shares "
            "and repère ranges with a timestamped 102MG SMS/SQMS snapshot and "
            "approved pile-capacity schedule"),
        Assumption("S3-A9", "C1 feeder selection: either 140LG-RC-01 or "
                   "-RC-02 feeds C1 (each 3,000 t/h nominal vs C1 "
                   "≈2,600 t/h); simultaneous partial-rate reclaim/blending "
                   "only if the route confluence permits it — TBC, so an "
                   "exclusive-machine group keeps at most ONE feeder active "
                   "per bucket. Configurable, not hard-coded.", "LOUTA",
                   source="L02 reclaim topology (RL1/RL2 flip gates to "
                   "RL3/RL4) — confluence TBC",
                   placeholder="Site.exclusive_machine_groups",
                   impact="second reclaimer = availability, not throughput",
                   action="confirm RL3/RL4 confluence & simultaneous-ops "
                   "policy"),
        Assumption("S3-A2", "Benguerir '3 qualities on 2 piles' comes from "
                   "the EARLIER conveying-system datasheet only (July 2026, "
                   "doc ref TBC) — the B02 BoD does not establish it; "
                   "operating rule TBC; MVP assigns one grade per pile",
                   "BENGUERIR", source="earlier conveying-system datasheet "
                   "(doc ref TBC) — NOT " + "Q80130-BEN-B02-ME-BOD-0001",
                   placeholder="BG-P1=TBT, BG-P2=BT",
                   impact="mine blending fidelity",
                   action="confirm zones/campaign rule"),
        Assumption("S3-A16", "Mine/raw-screened and washed-product quality "
                   "profiles are separate model records. The shared raw-grade "
                   "catalogue contains Global, High-Cd and Low-Cd profiles; "
                   "their current chemistry values come from the Louta BoD. "
                   "Benguerir physical/moisture states remain from B02 and "
                   "live pile assays remain separate. TBT/BT/MT vectors use "
                   "the corrected TBT < BT < MT semantic order but remain "
                   "simulation-only numeric placeholders.",
                   "phosphate/quality", source="Table 20 + " + _B02 + " + "
                   + _LOU_BOD, placeholder="Scenario.material_profiles",
                   impact="mine blending, corridor quality and PAP compliance",
                   action="obtain Benguerir raw and washed assays by pile/"
                   "stage, grade cut-offs, pile allocation and SMS Material/"
                   "Grade/SQP IDs"),
        Assumption("S3-A18", "Benguerir planning baseline configures Line 1, "
                   "Line 2 and Line 3 as OPERATING and gives each an explicit "
                   "mine-to-product campaign chain. Numeric labels remain "
                   "provisional aliases for design-source Trains A/B/C; the "
                   "available documents do not independently prove current "
                   "commissioning/as-built status. These washing trains are "
                   "distinct from upstream B01 screening lines "
                   "TM1/TSM2/TSM3.",
                   "BENGUERIR", source="project-team layout/planning "
                   "instruction, 28-Jul-2026; documentary reconciliation TBC",
                   placeholder="three executable BEN-WASH-L1/L2/L3 stages",
                   impact="campaign demand, machine schedule, material "
                   "balance and mimic state",
                   action="obtain latest commissioning/as-built status, "
                   "canonical tags and effective dates"),
        Assumption("S3-A24", "Louta washing/beneficiation configuration: TWO "
                   "PARALLEL production lines L1 and L2, 3 Mt dry/year each, "
                   "is confirmed by the process description. S0 represents "
                   "the washing boundary with explicit yield-bearing process "
                   "transfers and zero-start WIP interfaces. The demonstration "
                   "maps the documented Global case (584 t/h, 68.22% recovery) "
                   "to L1/TBT and the Low-Cd case (588 t/h, 67.71% recovery) "
                   "to L2/BT. The rates/recoveries are source facts; the "
                   "case-to-line, output-grade and campaign allocations are "
                   "project assumptions. Feed demand is exposed separately as "
                   "5,867 t Global for L1 and 5,914 t Low-Cd for L2: the exact "
                   "integer-bucket inputs for the configured 4,000 t recovered "
                   "output per line, not nameplate rates. High-Cd remains a "
                   "documented but unallocated S0 case.",
                   "LOUTA", status="ASSUMED", confidence="M",
                   source=_LOU_BOD + " + " + _LOU_PD,
                   placeholder="LOU-WASH-L1/L2 ProcessTransfer rows + "
                   "config.LOU_WASH_LINE_*",
                   impact="washing feed, recovered output, reject mass, "
                   "product stacking and C1 source allocation",
                   action="obtain the approved daily line/case campaign, "
                   "output-grade mapping, availability and wet/dry basis"),
        Assumption("S3-A26", "Project-team demonstration assumption "
                   "26-Jul-2026: overall residence time is 30 min (0.5 h) "
                   "for each Louta and Benguerir washing line. This does not "
                   "replace the documented scrubber component residence: "
                   "Louta 2.5–3 min; Benguerir 3 min. For Louta, product "
                   "stacking carries an explicit ≥30 min stage dependency after "
                   "washing execution window finishes. A cumulative matured-"
                   "mass constraint prevents same-bucket or early WIP "
                   "consumption; the native hourly material model is therefore "
                   "conservative. Benguerir applies the same explicit "
                   "30-minute dependency between each washing transfer and "
                   "its ST3 product-stacking stage.",
                   "phosphate/washing", status="ASSUMED", confidence="L",
                   source=WASHING_LINE_RESIDENCE_TIME_BASIS,
                   placeholder="WASHING_LINE_RESIDENCE_TIME_MODELLED = "
                   "{LOUTA: True, BENGUERIR: True}",
                   impact="validated minute windows and product-stack earliest "
                   "start for both mine washing chains",
                   action="confirm the line-by-line total residence and "
                   "commissioned process-control timing"),
        Assumption(
            "S3-A27",
            "Purpose-backed equipment-coverage baseline, 28-Jul-2026: the "
            "terminal M16 supply position, defined as on-ground inventory "
            "plus C1/C2 in-transit material, may not exceed its t0 value. "
            "Exact destination demand prevents over-delivery, and dispatch "
            "that has not arrived by T=24 remains visible as in-transit. "
            "MACHINE_COVERAGE_REWARD is zero: no job is generated merely to "
            "activate a row. The visible S0 stocked-route policy instead "
            "requires exactly 4,000 t recovered output from each Louta wash "
            "line and exactly 8,000 t dispatched on C1, while restoring both "
            "ore lanes and both product lanes to opening inventory. Every "
            "current no-job machine receives a structured legitimate-idle or "
            "modelling-gap disposition.",
            "planning/coverage",
            source="project request 26-Jul-2026 + mass-conservation policy",
            placeholder=(
                "M16_TERMINAL_SUPPLY_POSITION_MAX_T = initial M16 stock "
                "+ initial C1/C2 in-transit; MACHINE_COVERAGE_REWARD = 0; "
                "PROCESS_STAGE_OUTPUT_TARGET_T = 4,000 t per line; "
                "CORRIDOR_DISPATCH_TARGET_T[C1] = 8,000 t"),
            impact=(
                "meaningful stocked-route work without token movements; "
                "mine dispatch cannot exceed downstream drawdown"),
            action=(
                "replace demonstration targets with approved daily source "
                "allocation, end-stock targets and operating rotation rules")),
        Assumption(
            "S3-A28",
            "Louta S0 interface envelopes are explicit demonstration inputs. "
            "Two finite 10,000 t mine-inlet handoff buffers feed 110LG; they "
            "are not 102MG stockpiles. The shared 130LG-ST-01 product stacker "
            "uses a conservative 1,000 t/h planning rate because neither "
            "source PDF gives its nameplate. L1 is mapped to product lane 1 "
            "and L2 to lane 2; the physical product-pile count and stacker "
            "reach are not stated. The common RB4/RB5 ore routing, shared "
            "SL11 product route and RL3/RL4/C1 confluence are conservatively "
            "scheduled without simultaneous peer use.",
            "LOUTA", status="ASSUMED", confidence="L",
            source=_LOU_BOD + " + " + _LOU_PD
            + " + project S0 demonstration policy 28-Jul-2026",
            placeholder="LOU_DIRECT_MINE_FEED_AVAILABLE_T; "
            "LOU_PRODUCT_STACKER_S0_RATE_TPH; Site exclusive groups",
            impact="upstream availability, product-stack duration, machine "
            "reach and shared-route concurrency",
            action="obtain the 102/110 interface plan, 130 nameplate, approved "
            "product-pile GA/reach matrix and route/confluence interlocks"),
        Assumption(
            "S3-A29",
            "The selectable 24-hour demand profiles are ASSUMED "
            "demonstration inputs, not approved production or train orders. "
            "BALANCED retains MPH and the full Louta stocked-route campaign "
            "with zero Benguerir process/train/C2 demand. BG_TRAIN runs three "
            "1,800 t Benguerir washing-output campaigns plus 12,000 t of "
            "washed-product train demand while pausing Louta processing. "
            "FULL_FLOW activates both mine chains and adds 24,000 t of train "
            "demand plus 10,600 t on C2, a clean demonstrated combination "
            "selected as the default fleet exercise on 29-Jul-2026. It uses "
            "all 20 current solver-controlled machines across Louta, "
            "Benguerir and MPH without M16 unstacked-arrival slack. All jobs "
            "use connected stock, route and machine constraints; none are "
            "added only for visual usage.",
            "three-site demand planning", status="ASSUMED", confidence="L",
            source="project-team planning requests 28–29-Jul-2026",
            placeholder="PLANNING_DEMAND_PROFILES; default FULL_FLOW",
            impact="destination demand, C1/C2 allocation and fleet "
            "utilisation",
            action="replace with approved daily production, washing-campaign, "
                   "corridor-allocation and train-order inputs"),
        Assumption(
            "S3-A30",
            "PAP-2 is retained as a FUTURE receiving-line option by default. "
            "Its configured template matches PAP-1 (1,500 t/h, TBT, 36,000 t "
            "over 24 h), but Future state contributes zero active demand and "
            "offers no route candidates. Selecting AVAILABLE activates its "
            "two DERIVED/TBC route legs; it does not increase the confirmed "
            "two-bank M16 outbound envelope, so the full PAP-1 + PAP-2 + "
            "DRY-FEED templates cannot all be met concurrently.",
            "MPH/PAP planning", status="ASSUMED", confidence="L",
            source="project-team instruction 29-Jul-2026 + " + _M16PRO,
            placeholder="PAP-2 Future/Available scenario state",
            impact="optional final demand, service KPI and M16 reclaim schedule",
            action="confirm PAP-2 tag/phase/route and approve a feasible "
            "destination-demand combination"),
        Assumption("S3-A3", "Corridor proximity corrected by the project team "
                   "22-Jul-2026: Louta/C1 is NEARER; Benguerir/C2 is "
                   "FARTHER. The baseline provisionally reassigns the existing "
                   "30/60 km pair to C1/C2, giving C1 ≈2 h 05 → 2 buckets "
                   "and C2 ≈4 h 10 → 4 buckets (Δt = 1 h). Exact distances, "
                   "effective speeds and transfer dwell are TBC.", "corridors",
                   source=_CORRIDOR_CORRECTION,
                   placeholder="C1 30 km / 2 buckets; C2 60 km / 4 buckets",
                   impact="mine dispatch lead time and first-hours MPH arrivals",
                   action="obtain corridor TBE/datasheet or surveyed route "
                   "length + effective speed/transfer dwell"),
        Assumption("S3-A4", "Initial in-transit belt profile: both corridors "
                   "are pre-loaded at their full 2,600 t/h belt rate, but "
                   "they ALTERNATE so aggregate reception never exceeds the "
                   "single current 2,600 t/h path (S2-A9). C1 (2-bucket lag) "
                   "delivers in buckets 0–1 and C2 (4-bucket lag) in buckets "
                   "2–3, giving 5,200 t in transit on each corridor. The "
                   "earlier profile put 2,600 t/h on BOTH corridors in "
                   "buckets 0–1 (10,400 t on C2) and therefore implied a "
                   "physically impossible 5,200 t/h reception; it is "
                   "superseded. The alternation pattern is DERIVED from the "
                   "reception envelope — no live mine-SMS dispatch history "
                   "has been supplied.", "corridors",
                   source="derived from S3-A3 + S2-A9; live history TBC",
                   placeholder="belt_profile: C1 buckets 0–1, C2 buckets 2–3, "
                   "2,600 t each",
                   impact="initial in-transit mass, first-bucket stacking "
                   "workload and corridor KPIs",
                   action="mine-SMS dispatch history at go-live"),
        Assumption("S3-A5", "Benguerir train interfaces DOCUMENTED (raw "
                   "1,000/1,200 t/h + washed product 1,000/1,200 t/h, one "
                   "loading station ASSUMED shared, simultaneous use TBC); "
                   "only whether the job-allocation layer controls train "
                   "demand is SCOPE_TBC. The selectable demonstration "
                   "profiles use either 0 t, 12,000 t at 500 t/h, or 24,000 t "
                   "at 1,000 t/h of washed-product train demand. These are "
                   "ASSUMED planning orders, not source facts.",
                   "BENGUERIR", source=_B02 + " + project-team planning "
                   "request 28-Jul-2026",
                   placeholder="PLANNING_DEMAND_PROFILES; raw-train demand 0",
                   impact="reclaimer capacity competition",
                   action="replace with approved train orders and loading "
                   "windows"),
        Assumption("S3-A10", "Benguerir B02 table is the authoritative "
                   "CURRENT configuration: ST3**, RC2**, RC3**, SL5/SL6 and "
                   "the associated washed-yard arrangement. The '**' marks "
                   "are retained as source notation only, not as a pending "
                   "project option. BEN_B02_OPTION_ENABLED remains a legacy "
                   "sensitivity switch and is enabled in the baseline.",
                   "BENGUERIR", status="CONFIRMED", source=_B02,
                   placeholder="current configuration enabled in S3",
                   impact="all Benguerir reclaim capability",
                   action="none — current-state instruction received from "
                   "project team 21-Jul-2026"),
        Assumption("S3-A11", "Benguerir's three S0 washing campaigns are "
                   "ASSUMED simulation inputs because the available BoD "
                   "does not provide approved line production orders or "
                   "grade-to-line recoveries. The model borrows two Louta "
                   "design cases to produce 1,800 t per line, then routes "
                   "the recovered output through ST3 into BG-P1/BG-P2. "
                   "These are material-conserving planned jobs, not a "
                   "continuous exogenous inflow.",
                   "BENGUERIR", source="project-team simulation request "
                   "28-Jul-2026",
                   placeholder="BEN-WASH-L1/L2/L3 campaign targets",
                   impact="Benguerir upstream-machine usage and inventories",
                   action="replace feed rates, recoveries and campaign "
                   "mapping with approved washing-plant planning data"),
        Assumption("S3-A17", "BEN_ST3_STACKING_ENABLED is retained as a "
                   "legacy API/configuration field for saved-scenario "
                   "compatibility. The current six-pile baseline always "
                   "plans washing output through ST3, and the retired "
                   "exogenous BG-WASH-IN representation is inactive.",
                   "BENGUERIR",
                   source="six-pile planning baseline, 28-Jul-2026",
                   placeholder="legacy flag; no topology effect",
                   impact="none in the current built-in scenario",
                   action="remove only after saved-scenario compatibility "
                   "is no longer required"),
        Assumption("S3-A12", "Benguerir raw/screened ore-storage TOPOLOGY "
                   "USER_CONFIRMED 20-Jul-2026: 2 piles, 2 dedicated "
                   "stackers (one per pile), 1 shared reclaimer reaching "
                   "both piles (superseded the earlier 'schematic/TBC "
                   "count' framing, which is retained as history below). "
                   "Machine physical tags and metric rail positions "
                   "remain TBC — none invented; capacities/GA per Table "
                   "20 (S3-A14)", "BENGUERIR",
                   source="user-supplied Table 20 — Screened Product "
                   "Storage Area; document number/revision TBC",
                   placeholder="BG-RAW-1/-2 + BG-RAW-ST-1/-2 + "
                   "BG-RAW-BWR are solver-backed in the six-pile S0 chain",
                   impact="screened-pile inventory and stacking/reclaim "
                   "jobs are constrained by the shared-reclaimer topology",
                   action="obtain Table 20 document number/revision + "
                   "GA/metric positions + physical tags"),
        Assumption("S3-A14", "Table 20 — Screened Product Storage Area: "
                   "material screened phosphate <15 mm; 2 stockpiles, "
                   "400,000 t total live capacity, allocated as "
                   "BG-RAW-1 = 200,000 t and BG-RAW-2 = 200,000 t per "
                   "project-team instruction 22-Jul-2026; open longitudinal, chevron "
                   "stacking; 2 stackers 2,100/2,500 t/h humid each; 1 "
                   "reclaimer 2,100/2,500 t/h humid, marked 'Out of "
                   "scope' in the source package (package_scope) — "
                   "application planning ownership is SEPARATELY TBC "
                   "(planning_scope); autonomy 12 d nominal / 14 d "
                   "design as printed (not reinterpreted, not assigned "
                   "per pile)", "BENGUERIR",
                   source="user-supplied Table 20 + project-team equal-split "
                   "instruction 22-Jul-2026; document number/revision TBC",
                   placeholder="see raw-storage Machine/PileGeo entries",
                   impact="upstream ore-storage evidence baseline",
                   action="obtain the source document number/revision"),
        Assumption("S3-A19", "Project-team current-state instruction "
                   "24-Jul-2026: the upstream raw-yard starting mass is "
                   "BG-RAW-1 = 100,000 t and "
                   "BG-RAW-2 = 100,000 t (50% of the separately recorded "
                   "200,000 t-per-pile capacity). No live pile-grade snapshot "
                   "was supplied; S3-A20 separately defines an assumed "
                   "composition using the shared raw-grade catalogue. "
                   "This is the static scenario-t0 solver inventory, not a "
                   "timestamped SMS telemetry reading.", "BENGUERIR",
                   status="CONFIRMED",
                   source="project-team instruction 24-Jul-2026",
                   confidence="H",
                   placeholder="StockSpace.initial_mass_t = 100,000 t "
                   "for BG-RAW-1/-2",
                   impact="mimic shows both raw piles at 100,000/200,000 t "
                   "(50%); the same inventory constrains solver jobs and KPIs",
                   action="replace the static t0 mass and assumed composition with a "
                   "timestamped mine-SMS snapshot when commissioned"),
        Assumption(
            "S3-A20",
            "Project-team clarifications 24-Jul-2026 and 28-Jul-2026: "
            "multiple grade profiles may coexist within one stockpile. "
            "The legacy display metadata demonstrates mixed-pile composition "
            "using the shared mine-neutral raw grades: "
            "BG-RAW-1 = 50% Global BT + 50% "
            "High-Cd (mass-weighted composite BPL 55.585 %, MgO 1.495 %, "
            "CO2 8.31 %, SiO2 14.995 %, Cd 23 ppm); BG-RAW-2 = 50% Global "
            "+ 50% Low-Cd (composite BPL 57.17 %, MgO 1.31 %, CO2 8.48 %, "
            "SiO2 11.965 %, Cd 17 ppm). The 50/50 mass shares and "
            "profile-to-pile mapping are ASSUMED. At the 50% t0 fill, equal "
            "bands appear at display locations R0–R250 and R250–R500, with "
            "R500–R1000 empty. Those ranges are schematic, not surveyed "
            "spatial order; this is not a Benguerir assay, pile survey or "
            "live grade. The current solver campaigns use the explicit "
            "mine-neutral designated grade on each StockSpace until segment-"
            "level reclaim sequencing is available.",
            "BENGUERIR", status="ASSUMED",
            source=_LOU_BOD + " + project-team clarifications "
            "24-Jul-2026 and 28-Jul-2026",
            confidence="L",
            placeholder="legacy config.BG_RAW_DISPLAY_GRADE_COMPONENTS + "
            "current StockSpace.designated_grade",
            impact="job quality follows the configured StockSpace grade; "
            "mixed component bands remain informational until zone sequencing "
            "is modelled",
            action="replace with timestamped Benguerir segment assays, "
            "surveyed component masses/spatial order and approved Grade/SQP "
            "mapping"),
        Assumption("S3-A15", "Raw-storage reclaimer CAPACITY CONFLICT: "
                   "Table 20 gives 2,100 nominal / 2,500 design t/h "
                   "(humid) for the single shared reclaimer, marked 'Out "
                   "of scope'; the B02 expansion mechanical BoD gives "
                   "2,600 nominal / 3,120 design t/h for the raw bucket-"
                   "wheel reclaimer/RB interface. Whether B02 upgrades, "
                   "replaces or supplements the Table 20 reclaimer is "
                   "TBC — treated as a phase/equipment reconciliation "
                   "issue, NOT resolved by inference; a raw→C2 "
                   "demonstration at 2,600 t/h assumes the B02 expansion "
                   "interface is selected and the Table 20 bottleneck "
                   "has been resolved", "BENGUERIR",
                   source="Table 20 vs " + _B02,
                   placeholder="L-BG-RAW-C2 capacity_tph=2600 (assumes "
                   "B02 interface selected)",
                   impact="raw dispatch rate above 2,100 t/h is "
                   "conditional on an unconfirmed upgrade",
                   action="equipment list / PFD proving equivalence or "
                   "supersession"),
        Assumption("S3-A13", "Reclaimer minimum stable rate 500 t/h at "
                   "Benguerir (and mines generally) is ASSUMED — not stated "
                   "in the B02 BoD", "BENGUERIR", source="placeholder",
                   placeholder="500 t/h", impact="min-rate constraints",
                   action="machine supplier data"),
        Assumption("S3-A6", "MPH pile contents / PAP & DRY specs / drying intake — "
                   "same placeholders as scenario S2. The M16 bay topology, "
                   "machine lines, bay-adjacency reachability and the two "
                   "separate clamshell gate banks are the corrected "
                   "PFD-0001[3] configuration shared with S2 (S2-A4/S2-A8).",
                   "MPH",
                   source="placeholder contents + " + _M16PFD,
                   placeholder="see S2 assumptions",
                   impact="blend results indicative",
                   action="SpaceValue snapshot + feed specs + plot plan"),
        Assumption("S3-A7", "Planning treats MPH space quality as constant over "
                   "the horizon (twin snapshot); stacking-induced quality change "
                   "is applied by the simulator, not the plan LP. Multi-grade "
                   "pile zoning is accepted as EXPLICIT inputs only "
                   "(Scenario.pile_zones behind MULTI_GRADE_ZONES_ENABLED, "
                   "default OFF — no zone data exists for any real pile)",
                   "engine",
                   source="MVP design", placeholder="—",
                   impact="plan/sim quality may diverge slightly on rebuilt piles",
                   action="iterate: re-plan refreshes the snapshot; for zoning, "
                   "obtain zone boundaries, grade-compatibility/campaign rules "
                   "and reclaim-face sequencing before enabling the flag"),
        Assumption(
            "S3-A22",
            "Operational stockpile locations use repères. Every modelled "
            "stockpile is divided into 1,000 equal longitudinal intervals "
            "bounded by R0 and R1000; jobs carry an explicit range such as "
            "R400–R600 and machines are located at a current repère. Metres "
            "remain canonical internally for YardMaster/ASP StartX/StopX, "
            "travel, work areas and separation. The default maps R0=x_from "
            "and R1000=x_to; the physical R0 end and direction must be "
            "confirmed per pile before operational handover.",
            "all stockyards", status="CONFIRMED",
            source="project-team operating clarification 24-Jul-2026 + "
            "Innomotics FRS Start/End Position and 1-m inventory basis",
            confidence="H",
            placeholder="StockSpace.repere_at_x_from=0; "
            "repere_at_x_to=1000",
            impact="operator-fixed jobs, job lists, mimic work ranges and "
            "machine-location labels",
            action="confirm the physical R0 end/direction for every pile "
                   "and whether the SMS/PLC ICD expects metres or plant repères"),
    ]
    apply_planning_demand_profile(
        sc,
        DEFAULT_PLANNING_DEMAND_PROFILE
        if ben_b02_enabled else "BALANCED",
    )
    return sc


def apply_planning_demand_profile(
        sc: Scenario, profile_id: str) -> Dict[str, object]:
    """Apply one explicit 24-hour demonstration demand bundle.

    Profiles change demand and connected-stage targets only. They never
    create machine-specific jobs or override the optimizer's equipment
    selection.
    """
    try:
        profile = PLANNING_DEMAND_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown planning demand profile: {profile_id}") from exc
    horizon_h = sc.sim.horizon_buckets * sc.sim.dt_h
    if horizon_h <= 0:
        raise ValueError("planning horizon must be positive")

    bg_process_active = bool(profile.get("bg_process_active", False))
    louta_process_active = bool(
        profile.get("louta_process_active", True))
    louta_feed_targets = {
        line_id: (target if louta_process_active else 0.0)
        for line_id, target in _LOUTA_S0_LINE_FEED_TARGET_T.items()
    }
    louta_output_targets = {
        stage: (target if louta_process_active else 0.0)
        for stage, target in _LOUTA_S0_LINE_TARGET_T.items()
    }
    quantities_t = {
        "PAP-1": 36_000.0,
        "PAP-2": 36_000.0,
        "DRY-FEED": 19_200.0,
        "BG-TRAIN-PROD": float(profile["bg_train_product_t"]),
        "BG-TRAIN-RAW": 0.0,
        "LOU-WASH-L1": louta_feed_targets["LOU-WASH-L1"],
        "LOU-WASH-L2": louta_feed_targets["LOU-WASH-L2"],
    }
    quantities_t.update({
        line_id: float(case["feed_t"]) if bg_process_active else 0.0
        for line_id, case in _BEN_WASH_CASES.items()
    })
    for destination in sc.destinations:
        if destination.id in quantities_t:
            destination.demand_default_tph = (
                quantities_t[destination.id] / horizon_h)
    requested_grades = {
        "PAP-1": "TBT",
        "PAP-2": "TBT",
        "DRY-FEED": "TBT",
        "BG-TRAIN-PROD": "TBT",
        "BG-TRAIN-RAW": "RAW-BT-GLOBAL",
        "LOU-WASH-L1": "RAW-BT-GLOBAL",
        "LOU-WASH-L2": "RAW-BT-LOW-CD",
    }
    requested_grades.update({
        line_id: str(case["feed_grade"])
        for line_id, case in _BEN_WASH_CASES.items()
    })
    for destination in sc.destinations:
        if destination.id in requested_grades:
            destination.requested_grade = requested_grades[destination.id]

    # A profile is a complete demand bundle, so stale per-bucket overrides
    # from another profile must not leak into it. Benguerir process campaigns
    # use integer per-bucket entries so their displayed horizon quantity is
    # exactly the same quantity enforced by the tonne-integer solver.
    sc.demand = []
    if bg_process_active:
        for line_id, case in _BEN_WASH_CASES.items():
            quantity_t = int(round(float(case["feed_t"])))
            base_t, extra = divmod(quantity_t, sc.sim.horizon_buckets)
            sc.demand.extend(
                DemandEntry(
                    line_id, bucket,
                    (base_t + (1 if bucket < extra else 0)) / sc.sim.dt_h)
                for bucket in range(sc.sim.horizon_buckets)
            )
    corridor_targets = dict(
        sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {}) or {})
    louta_corridor_target = (
        _LOUTA_S0_C1_TARGET_T if louta_process_active else 0.0)
    corridor_targets["C1"] = louta_corridor_target
    corridor_targets["C2"] = float(profile["c2_dispatch_t"])
    sc.config["CORRIDOR_DISPATCH_TARGET_T"] = corridor_targets
    sc.config["LOU_S0_STOCKED_ROUTE_ENABLED"] = louta_process_active
    sc.config["LOU_C1_STOCKED_ROUTE_TARGET_T"] = louta_corridor_target
    sc.config["LOU_WASH_LINE_OUTPUT_TARGET_T"] = louta_output_targets
    process_targets = dict(
        sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}) or {})
    process_targets.update(louta_output_targets)
    for line_id, case in _BEN_WASH_CASES.items():
        stages = _BEN_PROCESS_STAGE_IDS[line_id]
        feed_target = float(case["feed_t"]) if bg_process_active else 0.0
        output_target = (
            float(case["output_t"]) if bg_process_active else 0.0)
        for stage in stages[:3]:
            process_targets[stage] = feed_target
        process_targets[stages[3]] = output_target
        process_targets[stages[4]] = output_target
    sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = process_targets
    sc.config["BEN_S0_PROCESS_ACTIVE"] = bg_process_active
    sc.config["LOU_S0_PROCESS_ACTIVE"] = louta_process_active
    sc.config["ACTIVE_PLANNING_DEMAND_PROFILE"] = profile_id
    sc.config["PLANNING_DEMAND_PROFILE_BASIS"] = (
        "ASSUMED demonstration scenarios requested by the project team on "
        "28-Jul-2026. They exercise real connected routes but are not "
        "approved production or train orders.")
    return dict(profile)


# S3 first and runtime-default: it is the scenario aligned with the report's
# primary Domain-A architecture. S2 stays available as a fast M16-only
# diagnostic with simulated corridor arrivals.
BUILTIN_SCENARIOS = {
    S3_SCENARIO_NAME: end_to_end,
    "S2 — MPH M16 hub only (simulated corridor arrivals)": mph_phosphate,
    "S1 — Sulphur minimal (M17)": sulphur_minimal,
}


def get_scenario(name: str) -> Scenario:
    return BUILTIN_SCENARIOS[name]()


#: link-id prefixes that belong to the M16 yard plumbing. The whole subset is
#: rebuilt on migration: it is generated data (no operator edits are expected
#: there) and a half-corrected graph is exactly the failure mode to avoid.
M16_LINK_PREFIXES = ("L-STK-", "L-RCL-", "L-H-", "L-RTE-")
#: nodes/links that earlier revisions created and that must NOT survive
M16_OBSOLETE_NODES = ("HUB:M16-RECLAIM",)
M16_OBSOLETE_LINKS = ("L-H-PAP-A", "L-H-PAP-B", "L-H-DRY-A", "L-H-DRY-B",
                      "L-H-A-PAP", "L-H-A-DRY", "L-H-B-PAP", "L-H-B-DRY")


def m16_topology_fingerprint(sc: Scenario) -> Dict[str, object]:
    """Comparable M16 topology of any scenario (fresh, migrated or imported).

    Used by the regression tests to prove that a built-in, a migrated session
    and the exported sample data describe the SAME M16 yard.
    """
    machines = {
        m.id: (m.kind, m.rail, m.physical_tag, tuple(m.reachable_spaces),
               m.rate_min_tph, m.rate_max_tph)
        for m in sc.machines if m.id in M16_MACHINE_LINES
    }
    links = {
        l.id: (l.src, l.dst, l.capacity_tph, l.shared_group, l.conflict_group,
               l.gate_id, l.gate_position, l.changeover_min,
               l.evidence.status)
        for l in sc.links
        if l.id.startswith(M16_LINK_PREFIXES)
    }
    nodes = sorted(n.id for n in sc.nodes
                   if n.id.startswith(("HUB:M16-", "TOWER:M16-", "DEST:PAP",
                                       "DEST:DRY")))
    destinations = sorted(
        (destination.id, destination.node_id)
        for destination in sc.destinations
        if destination.id in {"PAP-1", "PAP-2", "DRY-FEED"}
    )
    spaces = {sp.id: (sp.capacity_t, sp.mass_t, sp.grade)
              for sp in sc.spaces if sp.id in
              {p for _, piles in M16_BAYS for p in piles}}
    return {"machines": machines, "links": links, "nodes": nodes,
            "destinations": destinations, "spaces": spaces}


def _migrate_m16(sc: Scenario, fresh: Scenario) -> bool:
    """Rebuild the M16 machines, nodes, links and evidence of a loaded
    scenario from a freshly built one. Returns True when anything changed."""
    before = m16_topology_fingerprint(sc)
    # Preserve an operator-selected PAP-2 state across later baseline
    # migrations. A session predating PAP-2 has no state and receives the safe
    # Future default.
    pap2_state = "FUTURE"
    if any(destination.id == "PAP-2" for destination in sc.destinations):
        try:
            pap2_state = destination_line_operating_state(sc, "PAP-2")
        except (StopIteration, ValueError):
            pap2_state = "FUTURE"
    # ---- machines: reach matrix, machine line, tags, rates, evidence -------
    fresh_machines = {m.id: m for m in fresh.machines
                      if m.id in M16_MACHINE_LINES}
    for machine in sc.machines:
        source = fresh_machines.get(machine.id)
        if source is None:
            continue
        machine.kind = source.kind
        machine.rail = source.rail
        machine.physical_tag = source.physical_tag
        machine.reachable_spaces = list(source.reachable_spaces)
        machine.rate_min_tph = source.rate_min_tph
        machine.rate_max_tph = source.rate_max_tph
        machine.evidence = source.evidence
    # ---- nodes: drop the obsolete merged hub, add the corrected hubs -------
    sc.nodes = [n for n in sc.nodes if n.id not in M16_OBSOLETE_NODES]
    present_nodes = {n.id for n in sc.nodes}
    for node in fresh.nodes:
        if node.site == "MPH" and node.id.startswith(
                ("HUB:M16-RCL-", "HUB:M16-T13-", "DEST:PAP-2")) and \
                node.id not in present_nodes:
            sc.nodes.append(deepcopy(node))
    # ---- links: rebuild the whole M16 subset -------------------------------
    loaded_link_state = {
        link.id: (link.available, link.lifecycle) for link in sc.links
        if link.id.startswith(M16_LINK_PREFIXES)
    }
    sc.links = [l for l in sc.links
                if not l.id.startswith(M16_LINK_PREFIXES)
                and l.id not in M16_OBSOLETE_LINKS]
    refreshed_links = [
        deepcopy(link) for link in fresh.links
        if link.id.startswith(M16_LINK_PREFIXES)
    ]
    for link in refreshed_links:
        if link.id in loaded_link_state:
            link.available, link.lifecycle = loaded_link_state[link.id]
    sc.links += refreshed_links
    # ---- destinations: refreshed evidence/specs ----------------------------
    fresh_dests = {d.id: d for d in fresh.destinations
                   if d.id in ("PAP-1", "PAP-2", "DRY-FEED")}
    present_dests = {destination.id for destination in sc.destinations}
    for dest in sc.destinations:
        source = fresh_dests.get(dest.id)
        if source is not None:
            dest.node_id = source.node_id
            dest.spec = list(source.spec)
            dest.requested_grade = source.requested_grade
            dest.evidence = source.evidence
    sc.destinations.extend(
        deepcopy(destination)
        for destination_id, destination in fresh_dests.items()
        if destination_id not in present_dests
    )
    set_destination_line_operating_state(sc, "PAP-2", pap2_state)
    return m16_topology_fingerprint(sc) != before


def _migrate_louta_process_chain(sc: Scenario, fresh: Scenario) -> bool:
    """Replace the generated Louta planning subset with the current baseline.

    Revision v17 promoted the 110/120/130 stages from reference geometry to an
    explicit material chain. A hot Streamlit session built before that change
    otherwise keeps only LT-P1/P2 and the 140 reclaimers, producing a half-new
    UI over an old solver graph. Built-in topology is generated data, so this
    subset is rebuilt atomically; imported scenarios never call this helper.
    """
    fresh_spaces = {space.id: space for space in fresh.spaces
                    if space.site == "LOUTA"}
    fresh_machines = {machine.id: machine for machine in fresh.machines
                      if machine.site == "LOUTA"}
    fresh_nodes = {node.id: node for node in fresh.nodes
                   if node.site == "LOUTA"}
    fresh_links = {link.id: link for link in fresh.links
                   if link.id.startswith("L-LT-")}
    fresh_transfers = {
        transfer.id: transfer for transfer in fresh.process_transfers
        if transfer.machine in fresh_machines
    }
    fresh_destinations = {
        destination.id: destination for destination in fresh.destinations
        if destination.site == "LOUTA"
        and getattr(destination, "demand_kind", "delivery") == "process_feed"
    }

    def fingerprint(scenario: Scenario):
        return (
            repr(sorted(
                (space.id, space.capacity_t, space.mass_t, space.grade,
                 space.material_class, space.profile_id)
                for space in scenario.spaces if space.id in fresh_spaces)),
            repr(sorted(
                (machine.id, machine.kind, machine.rate_min_tph,
                 machine.rate_max_tph, tuple(machine.reachable_spaces),
                 machine.planning_enabled)
                for machine in scenario.machines
                if machine.id in fresh_machines)),
            repr(sorted(
                (node.id, node.kind, node.label)
                for node in scenario.nodes if node.id in fresh_nodes)),
            repr(sorted(
                (link.id, link.src, link.dst, link.capacity_tph,
                 link.shared_group, link.conflict_group)
                for link in scenario.links if link.id in fresh_links)),
            repr(sorted(
                (transfer.id, transfer.machine, transfer.source_space,
                 transfer.target_space, tuple(transfer.path),
                 transfer.process_stage, transfer.output_grade,
                 transfer.yield_factor, transfer.residence_min,
                 transfer.depends_on_stage, transfer.dependency_delay_min)
                for transfer in getattr(scenario, "process_transfers", [])
                if transfer.id in fresh_transfers)),
            repr(sorted(
                (destination.id, destination.node_id, destination.priority,
                 destination.demand_default_tph,
                 destination.requested_grade,
                 getattr(destination, "demand_kind", "delivery"),
                 getattr(destination, "process_stage", ""))
                for destination in scenario.destinations
                if destination.id in fresh_destinations)),
        )

    before = fingerprint(sc)
    sc.spaces = [
        space for space in sc.spaces if space.id not in fresh_spaces
    ] + [deepcopy(space) for space in fresh_spaces.values()]
    sc.machines = [
        machine for machine in sc.machines if machine.id not in fresh_machines
    ] + [deepcopy(machine) for machine in fresh_machines.values()]
    sc.nodes = [
        node for node in sc.nodes if node.id not in fresh_nodes
    ] + [deepcopy(node) for node in fresh_nodes.values()]
    sc.links = [
        link for link in sc.links if link.id not in fresh_links
    ] + [deepcopy(link) for link in fresh_links.values()]
    sc.process_transfers = [
        transfer for transfer in getattr(sc, "process_transfers", [])
        if transfer.id not in fresh_transfers
    ] + [deepcopy(transfer) for transfer in fresh_transfers.values()]
    sc.destinations = [
        destination for destination in sc.destinations
        if destination.id not in fresh_destinations
    ] + [
        deepcopy(destination) for destination in fresh_destinations.values()
    ]

    fresh_site = next(site for site in fresh.sites if site.id == "LOUTA")
    sc.sites = [
        deepcopy(fresh_site) if site.id == "LOUTA" else site
        for site in sc.sites
    ]
    if not any(site.id == "LOUTA" for site in sc.sites):
        sc.sites.append(deepcopy(fresh_site))

    # Copy the complete Louta policy family plus the generic solver controls it
    # synchronises with. These keys did not exist in the prior built-in
    # baseline, so retaining a partial mixture is not a meaningful user edit.
    policy_keys = {
        key for key in fresh.config if key.startswith("LOU_")
    } | {
        "CORRIDOR_DISPATCH_TARGET_T",
        "PROCESS_STAGE_OUTPUT_TARGET_T",
        "PROCESS_NO_PASSTHROUGH_SPACES",
        "TERMINAL_INVENTORY_MIN_T",
        "TERMINAL_INVENTORY_EXACT_T",
        "FLOW_TONNE_PENALTY",
        "WASHING_LINE_RESIDENCE_TIME_MODELLED",
        "ACTIVE_PLANNING_DEMAND_PROFILE",
        "PLANNING_DEMAND_PROFILE_BASIS",
    }
    for key in policy_keys:
        if key in fresh.config:
            sc.config[key] = deepcopy(fresh.config[key])
    return fingerprint(sc) != before


def _migrate_benguerir_process_chain(
        sc: Scenario, fresh: Scenario) -> bool:
    """Atomically replace the generated six-pile Benguerir planning subset."""
    fresh_spaces = {
        space.id: space for space in fresh.spaces
        if space.site == "BENGUERIR"
    }
    fresh_machines = {
        machine.id: machine for machine in fresh.machines
        if machine.site == "BENGUERIR"
    }
    fresh_nodes = {
        node.id: node for node in fresh.nodes
        if node.site == "BENGUERIR"
    }
    fresh_links = {
        link.id: link for link in fresh.links
        if link.id.startswith("L-BG-")
    }
    fresh_transfers = {
        transfer.id: transfer for transfer in fresh.process_transfers
        if transfer.machine in fresh_machines
    }
    fresh_destinations = {
        destination.id: destination for destination in fresh.destinations
        if destination.site == "BENGUERIR"
    }

    def fingerprint(scenario: Scenario):
        return (
            repr(sorted(
                (space.id, space.capacity_t, space.mass_t, space.grade,
                 space.material_class, space.profile_id)
                for space in scenario.spaces
                if space.site == "BENGUERIR")),
            repr(sorted(
                (machine.id, machine.kind, machine.rate_min_tph,
                 machine.rate_max_tph, tuple(machine.reachable_spaces),
                 machine.planning_enabled)
                for machine in scenario.machines
                if machine.site == "BENGUERIR")),
            repr(sorted(
                (node.id, node.kind, node.label)
                for node in scenario.nodes
                if node.site == "BENGUERIR")),
            repr(sorted(
                (link.id, link.src, link.dst, link.capacity_tph,
                 link.shared_group, link.conflict_group)
                for link in scenario.links
                if link.id.startswith("L-BG-"))),
            repr(sorted(
                (transfer.id, transfer.machine, transfer.source_space,
                 transfer.target_space, tuple(transfer.path),
                 transfer.process_stage, transfer.output_grade,
                 transfer.yield_factor, transfer.residence_min,
                 transfer.depends_on_stage, transfer.dependency_delay_min)
                for transfer in getattr(scenario, "process_transfers", [])
                if transfer.machine in fresh_machines)),
            repr(sorted(
                (destination.id, destination.node_id, destination.priority,
                 destination.demand_default_tph,
                 destination.requested_grade,
                 getattr(destination, "demand_kind", "delivery"),
                 getattr(destination, "process_stage", ""))
                for destination in scenario.destinations
                if destination.site == "BENGUERIR")),
        )

    before = fingerprint(sc)
    old_machine_ids = {
        machine.id for machine in sc.machines
        if machine.site == "BENGUERIR"
    }
    old_space_ids = {
        space.id for space in sc.spaces
        if space.site == "BENGUERIR"
    }
    loaded_machine_availability = {
        machine.id: machine.available for machine in sc.machines
        if machine.site == "BENGUERIR"
    }
    loaded_link_availability = {
        link.id: link.available for link in sc.links
        if link.id.startswith("L-BG-")
    }

    sc.spaces = [
        space for space in sc.spaces if space.site != "BENGUERIR"
    ] + [deepcopy(space) for space in fresh_spaces.values()]
    refreshed_machines = [
        deepcopy(machine) for machine in fresh_machines.values()
    ]
    for machine in refreshed_machines:
        if machine.id in loaded_machine_availability:
            machine.available = loaded_machine_availability[machine.id]
    sc.machines = [
        machine for machine in sc.machines
        if machine.site != "BENGUERIR"
    ] + refreshed_machines
    sc.nodes = [
        node for node in sc.nodes if node.site != "BENGUERIR"
    ] + [deepcopy(node) for node in fresh_nodes.values()]
    refreshed_links = [deepcopy(link) for link in fresh_links.values()]
    for link in refreshed_links:
        if link.id in loaded_link_availability:
            link.available = loaded_link_availability[link.id]
    sc.links = [
        link for link in sc.links if not link.id.startswith("L-BG-")
    ] + refreshed_links
    sc.process_transfers = [
        transfer for transfer in getattr(sc, "process_transfers", [])
        if transfer.machine not in old_machine_ids
        and transfer.source_space not in old_space_ids
        and transfer.target_space not in old_space_ids
    ] + [deepcopy(transfer) for transfer in fresh_transfers.values()]
    sc.destinations = [
        destination for destination in sc.destinations
        if destination.site != "BENGUERIR"
    ] + [
        deepcopy(destination) for destination in fresh_destinations.values()
    ]
    sc.demand = [
        entry for entry in sc.demand
        if entry.dest_id not in fresh_destinations
    ] + [
        deepcopy(entry) for entry in fresh.demand
        if entry.dest_id in fresh_destinations
    ]

    fresh_site = next(
        site for site in fresh.sites if site.id == "BENGUERIR")
    sc.sites = [
        deepcopy(fresh_site) if site.id == "BENGUERIR" else site
        for site in sc.sites
    ]
    if not any(site.id == "BENGUERIR" for site in sc.sites):
        sc.sites.append(deepcopy(fresh_site))

    policy_keys = {
        key for key in fresh.config
        if key.startswith(("BEN_", "BG_"))
    } | {
        "CORRIDOR_DISPATCH_TARGET_T",
        "PROCESS_STAGE_OUTPUT_TARGET_T",
        "PROCESS_NO_PASSTHROUGH_SPACES",
        "TERMINAL_INVENTORY_MIN_T",
        "TERMINAL_INVENTORY_EXACT_T",
        "FLOW_TONNE_PENALTY",
        "WASHING_LINE_RESIDENCE_TIME_MODELLED",
        "ACTIVE_PLANNING_DEMAND_PROFILE",
        "PLANNING_DEMAND_PROFILE_BASIS",
    }
    for key in policy_keys:
        if key in fresh.config:
            sc.config[key] = deepcopy(fresh.config[key])
    return fingerprint(sc) != before


def migrate_loaded_builtin(sc: Scenario) -> str:
    """Bring a hot-reloaded built-in S3 object to the current baseline.

    Streamlit retains the old ``Scenario`` instance after source files change,
    which can otherwise combine the corrected ``nearer/farther`` labels with
    obsolete corridor delays, stale display evidence or an obsolete Louta
    starting-inventory baseline. This deliberately refreshes the C1/C2 delay
    evidence and dependent belt profile when needed, the affected assumptions
    and Benguerir raw-yard evidence, and the built-in Louta pile distribution,
    mass and aggregate quality. Unrelated operator edits are kept.

    Returns ``"solver"`` when delayed-link or Louta solver inputs changed,
    ``"metadata"`` for display/evidence-only refreshes, and ``""`` when
    already current. Imported scenarios are not passed to this helper by the
    UI and therefore remain user-controlled.
    """
    if sc.config.get("BUILTIN_BASELINE_REVISION") == BUILTIN_BASELINE_REVISION:
        return ""
    if sc.name not in (S3_SCENARIO_NAME, S2_SCENARIO_NAME):
        return ""
    grade_ids_changed = canonicalize_scenario_grade_ids(sc)
    # Hot reload can retain instances of the previous Destination dataclass.
    # Reconstruct them with the current schema so new defaulted fields also
    # survive JSON/table export; preserve every pre-existing operator value.
    destination_schema_changed = any(
        not hasattr(destination, "demand_kind")
        or not hasattr(destination, "process_stage")
        for destination in sc.destinations
    )
    sc.destinations = [
        Destination(
            id=destination.id,
            site=destination.site,
            node_id=destination.node_id,
            priority=destination.priority,
            spec=deepcopy(destination.spec),
            demand_default_tph=destination.demand_default_tph,
            requested_grade=getattr(destination, "requested_grade", ""),
            evidence=deepcopy(destination.evidence),
            demand_kind=getattr(destination, "demand_kind", "delivery"),
            process_stage=getattr(destination, "process_stage", ""),
        )
        for destination in sc.destinations
    ]

    # ---- S2: the M16 yard is its whole scope --------------------------------
    if sc.name == S2_SCENARIO_NAME:
        fresh_s2 = mph_phosphate()
        m16_changed = _migrate_m16(sc, fresh_s2)
        raw_ids = {
            RAW_PENDING_GRADE_ID,
            *LEGACY_RAW_GRADE_ID_ALIASES,
            "TBT", "BT", "MT",
        }
        sc.grades = [grade for grade in sc.grades
                     if grade.id not in raw_ids] + [
            deepcopy(grade) for grade in fresh_s2.grades
            if grade.id in {RAW_PENDING_GRADE_ID, "TBT", "BT", "MT"}
        ]
        refreshed_profiles = {
            profile.id: deepcopy(profile)
            for profile in fresh_s2.material_profiles
        }
        sc.material_profiles = [
            refreshed_profiles.get(profile.id, profile)
            for profile in sc.material_profiles
        ]
        refreshed = {a.id for a in fresh_s2.assumptions}
        by_id = {a.id: a for a in fresh_s2.assumptions}
        migrated = [by_id.get(a.id, a) for a in sc.assumptions]
        present = {a.id for a in sc.assumptions}
        migrated += [a for a in fresh_s2.assumptions if a.id not in present]
        sc.assumptions = migrated
        sc.description = fresh_s2.description
        sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
        del refreshed
        # A stale S2 plan has legacy Job objects without the validated minute
        # execution overlay and was solved with the former overdelivery rule.
        # Any S2 baseline-revision migration must therefore invalidate it.
        return "solver"

    fresh = end_to_end(
        ben_b02_enabled=bool(sc.config.get("BEN_B02_OPTION_ENABLED", True)),
        ben_raw_to_c2=bool(sc.config.get("BEN_RAW_TO_C2_ENABLED", False)),
        ben_st3_stacking=bool(sc.config.get("BEN_ST3_STACKING_ENABLED", False)),
        multi_grade_zones=bool(sc.config.get("MULTI_GRADE_ZONES_ENABLED", False)),
    )
    louta_process_changed = _migrate_louta_process_chain(sc, fresh)
    benguerir_process_changed = _migrate_benguerir_process_chain(sc, fresh)
    solver_policy_changed = any(
        sc.config.get(key) != fresh.config.get(key)
        for key in (
            "M16_TERMINAL_SUPPLY_POSITION_POLICY",
            "M16_TERMINAL_SUPPLY_POSITION_MAX_T",
            "MACHINE_COVERAGE_REWARD"))
    m16_changed = _migrate_m16(sc, fresh)
    fresh_links = {link.id: link for link in fresh.links
                   if link.id in {"C1", "C2"}}
    loaded_links = {link.id: link for link in sc.links
                    if link.id in {"C1", "C2"}}
    corridor_changed = any(
        lid not in loaded_links or
        loaded_links[lid].transit_buckets != fresh_links[lid].transit_buckets
        for lid in fresh_links
    )
    # the corrected alternating in-transit profile (S3-A4/S2-A9) must be
    # applied even when the LAGS themselves are unchanged
    belt_changed = sorted(
        (e.link_id, e.arrive_bucket, e.mass_t) for e in sc.belt_profile
        if e.link_id in {"C1", "C2"}) != sorted(
        (e.link_id, e.arrive_bucket, e.mass_t) for e in fresh.belt_profile
        if e.link_id in {"C1", "C2"})
    if corridor_changed or belt_changed:
        for lid, source in fresh_links.items():
            if lid in loaded_links:
                loaded_links[lid].transit_buckets = source.transit_buckets
                loaded_links[lid].evidence = source.evidence
        sc.belt_profile = [
            entry for entry in sc.belt_profile
            if entry.link_id not in {"C1", "C2"}
        ] + [
            entry for entry in fresh.belt_profile
            if entry.link_id in {"C1", "C2"}
        ]
        sc.description = fresh.description

    metadata_assumptions = {
        "S3-A1", "S3-A6", "S3-A8", "S3-A11", "S3-A14", "S3-A16",
        "S3-A17", "S3-A18", "S3-A19",
        "S3-A20", "S3-A21", "S3-A22", "S3-A23", "S3-A24", "S3-A25",
        "S3-A26", "S3-A27", "S3-A28", "S3-A29", "S3-A30",
    }
    replacement_assumptions = {
        assumption.id: assumption
        for assumption in fresh.assumptions
        if assumption.id in (
            metadata_assumptions | {"S3-A3", "S3-A4"}
            if (corridor_changed or belt_changed) else metadata_assumptions)
    }
    present = set()
    migrated_assumptions = []
    for assumption in sc.assumptions:
        replacement = replacement_assumptions.get(assumption.id)
        migrated_assumptions.append(replacement or assumption)
        present.add(assumption.id)
    migrated_assumptions.extend(
        assumption for aid, assumption in replacement_assumptions.items()
        if aid not in present
    )
    sc.assumptions = migrated_assumptions
    fresh_raw = next((node for node in fresh.nodes if node.id == "SRC:BG-RAW"),
                     None)
    loaded_raw = next((node for node in sc.nodes if node.id == "SRC:BG-RAW"),
                      None)
    if fresh_raw is not None and loaded_raw is not None:
        loaded_raw.label = fresh_raw.label
    refreshed_grade_ids = {
        RAW_PENDING_GRADE_ID,
        *RAW_BT_GRADE_IDS,
        *LEGACY_RAW_GRADE_ID_ALIASES,
        "TBT", "BT", "MT",
    }
    old_grade_vectors = {
        grade.id: (dict(grade.quality), grade.rock_value)
        for grade in sc.grades if grade.id in {"TBT", "BT", "MT"}
    }
    sc.grades = [g for g in sc.grades if g.id not in refreshed_grade_ids] + [
        deepcopy(g) for g in fresh.grades
        if g.id in {
            RAW_PENDING_GRADE_ID, *RAW_BT_GRADE_IDS, "TBT", "BT", "MT",
        }
    ]
    quality_grades_changed = old_grade_vectors != {
        grade.id: (dict(grade.quality), grade.rock_value)
        for grade in sc.grades if grade.id in {"TBT", "BT", "MT"}
    }
    if not any(p.name == "CO2" for p in sc.quality_params):
        sc.quality_params.extend(
            p for p in fresh.quality_params if p.name == "CO2"
        )
    refreshed_profiles = {
        p.id: p for p in fresh.material_profiles
        if p.id in {
            "BEN-RAW-SCREENED",
            "LOU-MINE-PHOSPHATE",
            "LOU-WASHED-PRODUCT",
        }
    }
    sc.material_profiles = [
        refreshed_profiles.get(p.id, p) for p in sc.material_profiles
    ]
    present_profile_ids = {p.id for p in sc.material_profiles}
    sc.material_profiles.extend(
        p for pid, p in refreshed_profiles.items()
        if pid not in present_profile_ids
    )
    sc.config["BG_RAW_DISPLAY_START_MASS_T"] = dict(
        fresh.config["BG_RAW_DISPLAY_START_MASS_T"])
    sc.config["BG_RAW_DISPLAY_START_MASS_BASIS"] = fresh.config[
        "BG_RAW_DISPLAY_START_MASS_BASIS"]
    sc.config["BG_RAW_DISPLAY_GRADE_IDS"] = dict(
        fresh.config["BG_RAW_DISPLAY_GRADE_IDS"])
    sc.config["BG_RAW_DISPLAY_GRADE_CATALOG"] = list(
        fresh.config["BG_RAW_DISPLAY_GRADE_CATALOG"])
    sc.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"] = {
        pile_id: [dict(component) for component in components]
        for pile_id, components in
        fresh.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"].items()
    }
    sc.config["BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS"] = fresh.config[
        "BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS"]
    sc.config.pop("BG_RAW_DISPLAY_GRADE_ZONES", None)
    sc.config.pop("BG_RAW_DISPLAY_GRADE_ZONE_BASIS", None)
    sc.config["BG_RAW_DISPLAY_GRADE_BASIS"] = fresh.config[
        "BG_RAW_DISPLAY_GRADE_BASIS"]
    sc.config.pop("BG_RAW_DISPLAY_GRADE_ALTERNATIVES", None)
    # Louta 110/120LG homogenized ore-storage evidence/display metadata.
    # The fresh baseline also migrates the matching StockSpace rows and
    # process transfers below; these keys only preserve compatible labels,
    # assumed composition and source-basis text for hot-reloaded sessions.
    for _key in ("LOU_MINE_102MG_DISPLAY_CAPACITY_T",
                 "LOU_MINE_102MG_DISPLAY_START_MASS_T"):
        sc.config[_key] = dict(fresh.config[_key])
    for _key in ("LOU_MINE_102MG_DISPLAY_CAPACITY_BASIS",
                 "LOU_MINE_102MG_DISPLAY_START_MASS_BASIS",
                 "LOU_MINE_102MG_DISPLAY_GRADE_BASIS",
                 "LOU_MINE_102MG_DISPLAY_GRADE_COMPOSITION_BASIS"):
        sc.config[_key] = fresh.config[_key]
    sc.config["LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS"] = {
        pile_id: [dict(component) for component in components]
        for pile_id, components
        in fresh.config["LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS"].items()
    }
    sc.config["LOU_ORE_TOTAL_CAPACITY_T"] = fresh.config[
        "LOU_ORE_TOTAL_CAPACITY_T"]
    for _key in ("LOU_ORE_DISPLAY_CAPACITY_T", "LOU_ORE_DISPLAY_START_MASS_T"):
        sc.config[_key] = dict(fresh.config[_key])
    for _key in ("LOU_ORE_DISPLAY_CAPACITY_BASIS",
                 "LOU_ORE_DISPLAY_START_MASS_BASIS",
                 "LOU_ORE_DISPLAY_GRADE_BASIS",
                 "LOU_ORE_DISPLAY_GRADE_COMPOSITION_BASIS"):
        sc.config[_key] = fresh.config[_key]
    sc.config["LOU_ORE_DISPLAY_GRADE_COMPONENTS"] = {
        pile_id: [dict(component) for component in components]
        for pile_id, components
        in fresh.config["LOU_ORE_DISPLAY_GRADE_COMPONENTS"].items()
    }
    sc.config["PILE_GRADE_DISTRIBUTIONS"] = {
        pile_id: [dict(component) for component in components]
        for pile_id, components
        in fresh.config["PILE_GRADE_DISTRIBUTIONS"].items()
    }
    sc.config["PILE_GRADE_DISTRIBUTION_BASIS"] = dict(
        fresh.config["PILE_GRADE_DISTRIBUTION_BASIS"])
    sc.config["REPERE_SECTION_COUNT"] = fresh.config["REPERE_SECTION_COUNT"]
    sc.config["REPERE_CONVENTION_BASIS"] = fresh.config[
        "REPERE_CONVENTION_BASIS"]
    sc.config["WASHING_LINE_RESIDENCE_TIME_H"] = dict(
        fresh.config["WASHING_LINE_RESIDENCE_TIME_H"])
    sc.config["WASHING_LINE_RESIDENCE_TIME_BASIS"] = fresh.config[
        "WASHING_LINE_RESIDENCE_TIME_BASIS"]
    sc.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"] = deepcopy(
        fresh.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"])
    sc.config.pop("M16_TERMINAL_SUPPLY_POSITION_TARGET_T", None)
    for key in (
            "M16_TERMINAL_SUPPLY_POSITION_POLICY",
            "M16_TERMINAL_SUPPLY_POSITION_MAX_T",
            "M16_TERMINAL_SUPPLY_POSITION_BASIS",
            "MACHINE_COVERAGE_REWARD"):
        sc.config[key] = fresh.config[key]
    for space in sc.spaces:
        if not hasattr(space, "repere_at_x_from"):
            space.repere_at_x_from = 0
        if not hasattr(space, "repere_at_x_to"):
            space.repere_at_x_to = 1000

    fresh_louta_spaces = {
        space.id: space for space in fresh.spaces
        if space.id in {"LT-P1", "LT-P2"}
    }
    loaded_louta_spaces = {
        space.id: space for space in sc.spaces
        if space.id in {"LT-P1", "LT-P2"}
    }
    louta_changed = any(
        pile_id not in loaded_louta_spaces
        or loaded_louta_spaces[pile_id].capacity_t != source.capacity_t
        or loaded_louta_spaces[pile_id].mass_t != source.mass_t
        or loaded_louta_spaces[pile_id].grade != source.grade
        or loaded_louta_spaces[pile_id].quality != source.quality
        or loaded_louta_spaces[pile_id].state != source.state
        for pile_id, source in fresh_louta_spaces.items()
    )
    for pile_id, source in fresh_louta_spaces.items():
        target = loaded_louta_spaces.get(pile_id)
        if target is None:
            sc.spaces.append(source)
            continue
        target.capacity_t = source.capacity_t
        target.mass_t = source.mass_t
        target.grade = source.grade
        target.quality = dict(source.quality)
        target.state = source.state
    sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
    return ("solver" if (grade_ids_changed
                         or quality_grades_changed
                         or destination_schema_changed
                         or corridor_changed or belt_changed or louta_changed
                         or louta_process_changed
                         or benguerir_process_changed or m16_changed
                         or solver_policy_changed)
            else "metadata")
