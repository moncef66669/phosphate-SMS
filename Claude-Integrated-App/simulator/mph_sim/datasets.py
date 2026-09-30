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
from typing import Dict, List, Optional, Tuple

from .domain import (Assumption, BeltProfileEntry, CalendarEntry, DemandEntry,
                     Destination, Evidence, ExogenousFlow, Grade, Link, Machine,
                     MaterialProfile, Node, ObjectiveWeights, ProcessTransfer,
                     QualityParam, Scenario, Segment, SimParams, Site,
                     SpecRange, StockSpace)
from .grade_ids import (GRADE_ID_ALIASES as LEGACY_RAW_GRADE_ID_ALIASES,
                        MERCHANT_GRADE_FAMILIES,
                        RAW_BT_GRADE_IDS, RAW_GRADE_IDS,
                        RAW_PENDING_GRADE_ID, WASHED_GRADE_BY_RAW_AND_FAMILY,
                        WASHED_GRADE_IDS, WASHED_SOURCE_GRADE,
                        canonicalize_scenario_grade_ids)
from .flow_config import (destination_line_operating_state,
                          set_destination_line_operating_state)
from .campaigns import (CAMPAIGN_POLICY_BASIS, CAMPAIGN_POLICY_KEY,
                        SHORT_PARCEL_PENALTY_DEFAULT,
                        SHORT_PARCEL_PENALTY_KEY,
                        default_campaign_policy_config)
from .objective_config import OPERATIONAL_PENALTY_DEFAULTS, SCHEDULING_DEFAULTS
from .prewash_blending import default_config as default_prewash_blending_config

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
               QualityParam("SiO2_R", "%", "reactive silica"),
               QualityParam("F", "%", "fluorine"),
               QualityParam("P2O5_SC", "%", "P2O5 S,C (source-table assay)"),
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
_B02_PROCESS = "Q80130-BEN-B02-PR-BOD-0001[0], §5.2.1"
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
# A small 10 kt operating buffer per product pile is the integrated S0
# planning baseline. It lets day 1 start while keeping the weekly order larger
# than opening product stock, so washing/stacking cannot be bypassed.
_LOUTA_PRODUCT_START_MASS_T = 10_000.0
# Stable display/config keys only. The L01 source uses physical
# `102MG-SP-01/-02` tags for samplers, so the mimic renders the stockpiles as
# MINE STOCKPILE-01/-02 instead of exposing these keys as equipment tags.
_LOUTA_MINE_LANE_IDS = ("102MG-SP-01", "102MG-SP-02")
_LOUTA_MINE_DEMO_CAPACITY_T = 100_000.0
_LOUTA_MINE_DEMO_START_MASS_T = 50_000.0
_LOUTA_MINE_DISPLAY_GRADE_COMPONENTS = {
    "102MG-SP-01": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-HIGH-CD"},
    ],
    "102MG-SP-02": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT"},
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
         "grade_id": "RAW-BT"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-HIGH-CD"},
    ],
    "110LG-OL2": [
        {"component_id": "C1", "mass_fraction": 0.5,
         "grade_id": "RAW-BT"},
        {"component_id": "C2", "mass_fraction": 0.5,
         "grade_id": "RAW-BT-LOW-CD"},
    ],
}
# Documented Louta washing design cases (BoD Rev B pp.19, 21, 23, 27).
# Feed is on an ore/wet basis; recovery is total merchant-product mass.
_LOUTA_WASH_CASES = {
    "BT": {"feed_tph": 584.0, "recovery": 0.6822},
    "HIGH_CD": {"feed_tph": 579.0, "recovery": 0.6883},
    "LOW_CD": {"feed_tph": 588.0, "recovery": 0.6771},
}
_LOUTA_S0_C1_TARGET_T = 8_000.0
#: One mine hopper feeds one washing line at 8,000 t/day, at both sites
#: (project-team ruling 30-Sep-2026). Louta's two hoppers therefore deliver
#: 16,000 t/day into two lines of 8,000 t/day, exactly as Benguerir's three
#: hoppers match its three lines. Keeping arrivals and line draw on the same
#: per-hopper rule is what stops raw stock either starving a line or piling up
#: with no downstream draw until it spills.
_LOUTA_LINE_DAILY_FEED_T = 8_000.0
# Exact S0 process inputs per line. These are campaign feed requirements, not
# the 584/588 t/h BoD design-case rates.
_LOUTA_S0_LINE_FEED_TARGET_T = {
    "LOU-WASH-L1": _LOUTA_LINE_DAILY_FEED_T,
    "LOU-WASH-L2": _LOUTA_LINE_DAILY_FEED_T,
}
# Recovered output follows the feed through each line's documented BoD case,
# so the pair cannot drift apart when the feed assumption is revised.
_LOUTA_S0_LINE_TARGET_T = {
    "LOU-WASH-L1": round(
        _LOUTA_LINE_DAILY_FEED_T * _LOUTA_WASH_CASES["BT"]["recovery"]),
    "LOU-WASH-L2": round(
        _LOUTA_LINE_DAILY_FEED_T * _LOUTA_WASH_CASES["LOW_CD"]["recovery"]),
}
# Benguerir B02 grade yields are documented dry-feed-solids -> TSM
# coefficients.  The physical solver additionally needs moisture at each side
# of beneficiation; 12 % raw and 17.5 % washed product are explicit scenario
# assumptions (the latter is the midpoint of the documented 15--20 % range),
# not measurements.  There is one coefficient per shared grade; no cut-size
# variant is represented in the planning model.
_BEN_B02_BENEFICIATION_YIELD = {
    "MT": 0.7573,
    "BT": 0.6663,
    "TBT": 0.6264,
}
_BEN_B02_RAW_MOISTURE_PCT = 12.0
_BEN_B02_PRODUCT_MOISTURE_PCT = 17.5
_BEN_B02_DRY_FEED_TPH = 671.0
_BEN_B02_PHYSICAL_FEED_TPH = (
    _BEN_B02_DRY_FEED_TPH
    / (1.0 - _BEN_B02_RAW_MOISTURE_PCT / 100.0)
)

# B02 supplies the MT chemistry. RAW-TBT uses the project-team assay
# supplied on 19-Sep-2026 for BPL, Cd, MgO, SiO2 and CO2. Its reported 5% H2O
# is deliberately excluded: the existing 10--14% planning range and 12%
# midpoint remain authoritative for model mass conversion. The "BT"
# screening-yield case is deliberately absent because the source does not
# provide a complete chemistry vector for it.
_BEN_B02_RAW_GRADE_QUALITY = {
    "RAW-MT": {
        "BPL": 58.93, "Cd": 13.0, "MgO": 1.10, "SiO2": 10.13,
        "CO2": 6.54, "moisture": _BEN_B02_RAW_MOISTURE_PCT,
    },
    "RAW-TBT": {
        "BPL": 54.10, "Cd": 16.0, "MgO": 1.52, "SiO2": 14.39,
        "CO2": 8.32, "moisture": _BEN_B02_RAW_MOISTURE_PCT,
    },
}

# Provisional washed-product chemistry.  The BT rows are the historic
# TBT/BT/MT demonstration vectors.  High-/Low-Cd rows preserve the measured
# raw-feed Cd ratios (25/21 and 13/21 respectively), while BPL, MgO and SiO2
# receive modest, directionally consistent adjustments.  These are explicit
# ASSUMED simulation vectors, not laboratory predictions or approved SQPs.
# Cd keeps its raw lineage: HIGH-CD = family x 25/21 and LOW-CD = family x
# 13/21, the RAW-BT-HIGH-CD / RAW-BT-LOW-CD ratios against RAW-BT.
#
# CO2 added and Cd/MgO rebased 28-Sep-2026. The washed vectors previously
# carried NO CO2 assay at all, while PAP's G10 contract requires CO2 in
# [6.0, 6.5] and a missing parameter fails qualification outright - so no
# grade the mines produce could ever be received at PAP, and M16 was servable
# only from its opening stock. Cd and MgO are rebased into the G10 window for
# the same reason (old Cd 13.6-41.7 against an 18 ppm cap; old MgO 0.6-1.5
# against 0.8). BPL and SiO2 are unchanged - they already qualified. The
# TBT < BT < MT quality order is preserved throughout.
_WASHED_CD_VARIANT_QUALITY = {
    "TBT-HIGH-CD": {
        "BPL": 62.0, "Cd": 17.86, "MgO": 0.80, "SiO2": 4.10,
        "CO2": 6.48, "moisture": 12.0,
    },
    "TBT-LOW-CD": {
        "BPL": 64.5, "Cd": 9.29, "MgO": 0.72, "SiO2": 2.90,
        "CO2": 6.42, "moisture": 12.0,
    },
    "BT-HIGH-CD": {
        "BPL": 67.0, "Cd": 14.88, "MgO": 0.72, "SiO2": 3.40,
        "CO2": 6.30, "moisture": 12.0,
    },
    "BT-LOW-CD": {
        "BPL": 69.5, "Cd": 7.74, "MgO": 0.62, "SiO2": 2.20,
        "CO2": 6.20, "moisture": 12.0,
    },
    "MT-HIGH-CD": {
        "BPL": 73.0, "Cd": 11.90, "MgO": 0.62, "SiO2": 2.60,
        "CO2": 6.15, "moisture": 12.0,
    },
    "MT-LOW-CD": {
        "BPL": 75.5, "Cd": 6.19, "MgO": 0.52, "SiO2": 1.40,
        "CO2": 6.05, "moisture": 12.0,
    },
}


def _ben_b02_physical_output_factor(grade_id: str) -> float:
    """Default raw-wet physical -> washed-wet physical B02 factor."""
    return (
        (1.0 - _BEN_B02_RAW_MOISTURE_PCT / 100.0)
        * _BEN_B02_BENEFICIATION_YIELD[grade_id]
        / (1.0 - _BEN_B02_PRODUCT_MOISTURE_PCT / 100.0)
    )

# Benguerir S0 line-to-grade and raw-campaign allocations remain simulation
# assumptions.  ``recovery`` is retained only as a backward-compatible legacy
# fallback; explicitly marked B02 transfers use the grade mass profile above.
#: Daily physical feed per Benguerir washing line. Each of the three lines
#: processes 8,000 t/day, so the site draws 24,000 t/day -- the matched
#: balance against Benguerir mine arrivals (project-team ruling 30-Sep-2026).
#: The earlier 2,695 t/day left the line running ~3.5 h and consumed barely a
#: third of the arriving mine tonnage; the surplus had nowhere to go, spilled,
#: and because spill outranks customer service in the objective it drove
#: weekly service to 0.00%. At 762.5 t/h physical the line now runs ~10.5 h.
_BEN_LINE_DAILY_FEED_T = 8_000.0
_BEN_WASH_CASES = {
    "BEN-WASH-L1": {
        "feed_t": _BEN_LINE_DAILY_FEED_T,
        # Output follows the feed through the declared B02 mass profile, so the
        # two can never drift apart when the feed assumption is revised.
        "output_t": round(
            _BEN_LINE_DAILY_FEED_T * _ben_b02_physical_output_factor("TBT")),
        "feed_tph": _BEN_B02_PHYSICAL_FEED_TPH,
        "feed_grade": "RAW-TBT", "output_grade": "TBT",
        "recovery": _ben_b02_physical_output_factor("TBT"),
    },
    "BEN-WASH-L2": {
        "feed_t": _BEN_LINE_DAILY_FEED_T,
        "output_t": round(
            _BEN_LINE_DAILY_FEED_T * _ben_b02_physical_output_factor("BT")),
        "feed_tph": _BEN_B02_PHYSICAL_FEED_TPH,
        "feed_grade": "RAW-BT-LOW-CD", "output_grade": "BT-LOW-CD",
        "recovery": _ben_b02_physical_output_factor("BT"),
    },
    # L3 shares BG-MINE-1 and BG-RAW-1 with L1 ("TM1 and TSM2 share campaign 1"
    # below), so both lines necessarily carry the SAME raw grade -- a shared
    # pile cannot hold two declared grades. Giving L3 its own feed grade left
    # its demand with no matching process transfer (NO_PROCESS_FEED_ROUTE).
    #
    # The open inconsistency is upstream, not here: mine arrivals fill L3's
    # hopper BG-HOP-TSM2 with RAW-BT and RAW-BT-HIGH-CD, which campaign 1
    # cannot wash. Resolving that needs either a campaign-aware arrival split
    # or a genuinely grade-agnostic chain, not a per-line grade override.
    "BEN-WASH-L3": {
        "feed_t": _BEN_LINE_DAILY_FEED_T,
        "output_t": round(
            _BEN_LINE_DAILY_FEED_T * _ben_b02_physical_output_factor("TBT")),
        "feed_tph": _BEN_B02_PHYSICAL_FEED_TPH,
        "feed_grade": "RAW-TBT", "output_grade": "TBT",
        "recovery": _ben_b02_physical_output_factor("TBT"),
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
            "Seven daily MPH demands and the Louta stocked-route campaign. "
            "Benguerir product reclaimers remain standby."
        ),
        "legacy_demand_model": True,
        "bg_train_product_t": 0.0,
        "c1_dispatch_t": 8_000.0,
        "c2_dispatch_t": 0.0,
        "bg_process_active": False,
        "louta_process_active": True,
    },
    "BG_TRAIN": {
        "label": "Benguerir train campaign",
        "description": (
            "Runs all three Benguerir mine-to-product washing campaigns and "
            "an assumed 12,000 t/day washed-product train order. Louta "
            "processing is paused so this preset stays site-focused."
        ),
        "legacy_demand_model": True,
        "bg_train_product_t": 12_000.0,
        "c1_dispatch_t": 0.0,
        "c2_dispatch_t": 0.0,
        "bg_process_active": True,
        "louta_process_active": False,
    },
    "FULL_FLOW": {
        "label": "Full-flow all-machine demonstration",
        "description": (
            "Runs both mine processing chains and MPH for seven days with "
            "24,000 t/day of Benguerir train demand and a 10,600 t/day C2 "
            "allocation. Week-end operating floors stop the week being served "
            "by draining opening stock, so both mines must replenish through "
            "washing whenever their source qualities allow it."
        ),
        "legacy_demand_model": True,
        # Train order rescaled 28-Sep-2026: 24,000 -> 2,300 t/day.
        #
        # Benguerir's three wash lines produce 1,800 t each per campaign day
        # and the BEN-WASH calendar fires on 3 of 7 days, so the site makes
        # 5,400 x 3 = 16,200 t of washed product per week. BG-P1/P2 open at
        # 10,000 t each against a 30,000 t safety stock, so they start BELOW
        # safety and cannot legitimately be drawn down either. The old
        # 24,000 t/day (168,000 t/week) therefore asked for ~10x sustainable
        # production; the planner delivered 33,981 t, which was 94% of
        # everything physically obtainable, and the resulting "20% service"
        # was an artefact of the order, not a scheduling failure. It alone
        # accounted for ~70% of all unmet demand in the preset.
        #
        # 2,300 t/day = 16,100 t/week, just under weekly washing output so
        # the product piles can also recover toward their safety level.
        # Raising the BEN-WASH campaign calendar beyond 3 days is the
        # alternative lever if a larger train order is actually wanted.
        "bg_train_product_t": 2_300.0,
        # Corridor call-offs raised 28-Sep-2026 (8,000 -> 16,000 and
        # 10,600 -> 21,000 t/day). The previous pair asked the corridors for
        # ~18,600 t/day against a physical 2,600 t/h (~62,400 t/day) shared
        # reception path, so upstream product never reached M16 and both mine
        # chains idled. Measured: service 59.90% -> 64.23%, machines 16 -> 18
        # of 20. Doubling again (24,000/31,000) gains nothing - the shared
        # reception path binds there - so this pair sits deliberately below it.
        "c1_dispatch_t": 16_000.0,
        "c2_dispatch_t": 21_000.0,
        "bg_process_active": True,
        "louta_process_active": True,
        # Washing-line feed is a ``process_feed`` destination, which
        # service_first excludes from the objective by default. Left unpriced,
        # the mines had no reason to run their own wash lines: worth +1.57
        # points of service, and it is what makes ore reclaim run at all.
        "service_first_includes_process_feed": True,
        # Absorbed from the former BLEND_SHOWCASE preset (27-Sep-2026), which
        # was otherwise numerically identical to this one: same demand, service,
        # unmet, machines and destinations to the tonne. It is what installs the
        # MPH chevron blending matrix, so without it the complete preset
        # produced no blending jobs at all, and it also raises the per-window
        # wall guard from 40 s to 55 s.
        "blend_showcase": True,
        # Week-end operating floors are implemented and SOFT (a penalised
        # ``replenishment`` level below service, quality and continuity - see
        # TERMINAL_INVENTORY_MIN_SOFT_SPACES), but left OFF here.
        #
        # Measured 27-Sep-2026: with floors on, the replenishment pass reports
        # before=19,743 t, after=19,743 t, accepted=False - it cannot recover a
        # single tonne without giving up served demand, because the week is
        # corridor-supply bound rather than policy bound. Enabling it therefore
        # buys no restocking and costs two machines of coverage (15/20 -> 13/20).
        # Turn it on once corridor inflow can actually sustain both.
        "operating_stock_floors": False,
    },
    "BLEND_SHOWCASE": {
        "label": "Blending showcase",
        "description": (
            "Uses the full-flow demand calendar and exposes the shared MPH "
            "blending matrix catalogue for a demand-driven chevron campaign."
        ),
        "legacy_demand_model": True,
        "bg_train_product_t": 24_000.0,
        "c1_dispatch_t": 8_000.0,
        "c2_dispatch_t": 10_600.0,
        "bg_process_active": True,
        "louta_process_active": True,
        "blend_showcase": True,
    },
}
_LEGACY_PLANNING_DEMAND_PROFILE_ALIASES = {
    "INTEGRATED_WEEKLY": DEFAULT_PLANNING_DEMAND_PROFILE,
}

# Seven explicit daily campaigns.  The factors deliberately sum to seven for
# final units, so their weekly total remains seven times the documented daily
# demonstration quantity while still exercising the day-by-day editor.  Mine
# washing uses the original finite demonstration campaign calendar.
_WEEKLY_DEMAND_FACTORS: Dict[str, Tuple[float, ...]] = {
    "PAP-1": (1.00, 0.90, 1.10, 1.00, 0.95, 1.05, 1.00),
    "PAP-2": (1.00, 0.90, 1.10, 1.00, 0.95, 1.05, 1.00),
    # Weighted to the duty split: the four G10 days carry 1.54 each
    # (4 x 1.54 x 4,157 = 25,607 t/week) and the three BG-precursor days
    # 0.28 each (3 x 0.28 x 4,157 = 3,492 t/week). Still sums to 7.00, so the
    # weekly total remains seven times the daily quantity.
    "DRY-FEED": (1.54, 0.28, 0.28, 1.54, 0.28, 1.54, 1.54),
    "BG-TRAIN-PROD": (1.00, 0.75, 1.25, 0.00, 1.50, 1.00, 1.50),
    "BG-TRAIN-RAW": (0.00,) * 7,
    # Both washing plants run EVERY day (project team, 28-Sep-2026).
    #
    # The previous calendars - Benguerir 3 days of 7, Louta 1 of 7 - were a
    # demonstration artefact (ASSUMPTIONS.md S3-A27, "campaign inputs, not
    # BoD design-case rates"), not a plant constraint: nothing in the
    # equipment, feed stock or manning requires a line to stand idle six days
    # a week. They capped site output at 16,200 t/week (Benguerir) and
    # 8,000 t/week (Louta), and because synchronize_weekly_demand_targets
    # makes the C1 corridor target equal Louta's recovered output, the Louta
    # calendar alone was what throttled C1 and left its ore reclaimers and
    # product stacker with nothing to do.
    #
    # Daily operation gives 37,800 t/week at Benguerir and 56,000 t/week at
    # Louta: 93,800 t/week against 83,700 t/week of final demand, so the week
    # becomes demand-limited rather than supply-starved for the first time.
    "BEN-WASH-L1": (1.00,) * 7,
    "BEN-WASH-L2": (1.00,) * 7,
    "BEN-WASH-L3": (1.00,) * 7,
    "LOU-WASH-L1": (1.00,) * 7,
    "LOU-WASH-L2": (1.00,) * 7,
}

_WEEKLY_DEMAND_GRADES: Dict[str, Tuple[str, ...]] = {
    "PAP-1": ("G10",) * 7,
    "PAP-2": ("G10",) * 7,
    # The dryer serves both routes: PAP's G10 specification and the humid
    # precursors of the BG train-export profiles (§ _dry_feed_precursor_grades).
    "DRY-FEED": ("G10", "BG-22-H", "BG10-H", "G10",
                 "BG11-H", "G10", "G10"),
    "BG-TRAIN-PROD": (
        "TBT", "BT-LOW-CD", "TBT", "BT-LOW-CD",
        "TBT", "BT-LOW-CD", "BT-LOW-CD"),
}
_LOUTA_DIRECT_MINE_FEED_T = 10_000.0
_LOUTA_PRODUCT_STACKER_S0_TPH = 1_000.0
_LOUTA_PRODUCT_GRADE_DISTRIBUTIONS = {
    "LT-P1": [
        {"component_id": "C1", "mass_fraction": 0.6, "grade_id": "TBT"},
        {"component_id": "C2", "mass_fraction": 0.4, "grade_id": "BT"},
    ],
    "LT-P2": [
        {"component_id": "C1", "mass_fraction": 0.6,
         "grade_id": "BT-LOW-CD"},
        {"component_id": "C2", "mass_fraction": 0.4,
         "grade_id": "MT-LOW-CD"},
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
        "BPL": 71.9,
        "Cd": 16.59,
        "MgO": 0.78,
        "SiO2": 1.88,
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
BUILTIN_BASELINE_REVISION = "2026-09-23-restore-demand-presets-v44"
#: backwards-compatible alias (demo-bundle cache key + existing tests)
S3_BASELINE_REVISION = BUILTIN_BASELINE_REVISION

OPERATIONAL_OBJECTIVE_CONFIG_KEYS = (
    "CONTINUITY_POLICIES",
    *OPERATIONAL_PENALTY_DEFAULTS,
    "ENERGY_OBJECTIVE_ENABLED",
    "OPERATIONAL_OBJECTIVE_BASIS",
    CAMPAIGN_POLICY_KEY,
    SHORT_PARCEL_PENALTY_KEY,
    "CAMPAIGN_POLICY_BASIS",
    "PARCEL_CONSOLIDATION_ENABLED",
    "PARCEL_CONSOLIDATION_WALL_TIME_S",
    "PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S",
    "PARCEL_CONSOLIDATION_BASIS",
    *SCHEDULING_DEFAULTS,
    "PLANNING_PROCESS_STAGE_TARGETS",
    "PLANNING_CORRIDOR_TARGETS",
)

# ASSUMED demonstration continuous-feed service fraction, 13-Sep-2026.
#
# With ``minimum_tph == 0`` a feed gap was defined as "less than ONE physical
# tonne delivered in a demand-active hour".  One tonne is also the integer
# activity floor of the mass model, so the cheapest possible way to earn a
# perfect continuous-feed KPI was to deliver a single token tonne every hour
# and leave the rest of the order unmet.  That is exactly the incentive that
# produced repeated 1 t / 1 minute runs.
#
# This fraction is applied to the bucket's OWN requested mass, so it states
# only "a share of what was asked for" and does NOT assert a plant-approved
# minimum stable feed rate in t/h — that evidence is still outstanding.  Set
# it to 0.0 to restore the historic absolute-tonne definition, or replace it
# with an approved ``minimum_tph`` once the receiving plants publish one.
CONTINUOUS_FEED_MIN_SERVICE_FRACTION = 0.5
CONTINUOUS_FEED_MIN_SERVICE_FRACTION_BASIS = (
    "ASSUMED, 13-Sep-2026: a demand-active hour counts as served only when "
    "at least 50 % of that hour's own requested tonnes arrive. Demand-derived "
    "on purpose: it replaces the one-tonne token test without asserting an "
    "unapproved minimum stable feed rate. Replace with the receiving plants' "
    "approved minimum stable feed rate (CONTINUITY_POLICIES.minimum_tph) when "
    "that evidence is issued."
)


def _install_operational_objective(
        sc: Scenario, continuous_destinations: List[str]) -> None:
    """Install explicit, auditable S0 policies on one built-in scenario."""
    sc.config["CONTINUITY_POLICIES"] = {
        destination.id: {
            "mode": (
                "continuous_feed"
                if destination.id in set(continuous_destinations) else
                "flexible_process_campaign"
                if destination.demand_kind == "process_feed" else
                "batch_delivery"
            ),
            # Zero retains the evidence-neutral no-feed definition (<1 t in
            # an active bucket). A plant-approved minimum stable feed remains
            # required before partial-rate delivery is called interruption.
            "minimum_tph": 0.0,
            # ... but the absolute one-tonne rule rewarded token deliveries,
            # so continuous-feed destinations additionally require a share of
            # their OWN requested hourly mass. See
            # CONTINUOUS_FEED_MIN_SERVICE_FRACTION_BASIS.
            "minimum_service_fraction": (
                CONTINUOUS_FEED_MIN_SERVICE_FRACTION
                if destination.id in set(continuous_destinations) else 0.0),
            # No hard outage ceiling has yet been approved. Duration and
            # episode frequency are nevertheless penalised and reported.
            "max_gap_min": 0,
        }
        for destination in sc.destinations
    }
    sc.config.update(OPERATIONAL_PENALTY_DEFAULTS)
    sc.config.update(SCHEDULING_DEFAULTS)
    if sc.name in (S2_SCENARIO_NAME, S3_SCENARIO_NAME):
        sc.config["OBJECTIVE_MODE"] = "service_first"
    # Campaign / parcel / minimum-run policy: the scheduling-model answer to
    # job fragmentation. Every value is ASSUMED for demonstration and
    # adjustable per machine kind and per machine.
    sc.config[CAMPAIGN_POLICY_KEY] = default_campaign_policy_config()
    sc.config[SHORT_PARCEL_PENALTY_KEY] = SHORT_PARCEL_PENALTY_DEFAULT
    sc.config["CAMPAIGN_POLICY_BASIS"] = CAMPAIGN_POLICY_BASIS
    sc.config["ENERGY_OBJECTIVE_ENABLED"] = False
    sc.config["OPERATIONAL_OBJECTIVE_BASIS"] = (
        "ASSUMED/uncalibrated S0 priorities: continuous-feed gap duration "
        "and episodes plus exact scheduled equipment starts, stops and "
        "activity-group changeovers are active. Work/link energy is "
        "implemented but opt-in until meter-derived kWh/t coefficients are "
        "approved. Equipment efficiency and wear are represented only by "
        "scheduled transition/active-time proxies; condition, load and RUL "
        "telemetry are not yet available."
    )

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
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=24, frozen_buckets=2,
                       horizon_minutes=1440, seed=42)
    sc.weights = ObjectiveWeights()
    _install_operational_objective(sc, ["MOLTEN-S"])
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
                   "buckets the model counts and penalises exact scheduled "
                   "bucket-boundary starts, stops and group changes, but cannot "
                   "enforce an intra-hour 8-event limit", "M17/constraints",
                   source="report §15.4", placeholder="w5 event penalties",
                   impact="dispatcher-level constraint deferred",
                   action="enforce in sub-hourly dispatcher iteration"),
    ]
    return sc


# =============================================================================
# 2. MPH phosphate scenario (M16 humid yard)
# =============================================================================
TRAIN_EXPORT_GRADE_IDS = ("BG-22", "BG10", "BG11", "BG-AD")


def _train_export_grades() -> List[Grade]:
    """Hard receiving profiles transcribed from the Gantour lab table."""
    source = (
        "User-provided Gantour laboratory train-export profile table, "
        "17-Sep-2026"
    )
    note = (
        "Transcribed receiving profile. Limits are hard and selected by the "
        "BG-TRAIN-PROD demand campaign. Source '<' limits are stored verbatim "
        "but implemented as inclusive numeric maxima because SpecRange has "
        "no strict-bound operator."
    )
    common = {
        "material_class": "washed",
        "is_specification": True,
        "receiving_destinations": ["BG-TRAIN-PROD"],
    }
    return [
        Grade(
            "BG-22", "BG-22 train export profile", **common,
            receiving_spec=[
                SpecRange("BPL", 68.0, 70.0, hard=True),
                SpecRange("CO2", 5.0, 6.5, hard=True),
                SpecRange("SiO2", pmax=4.5, hard=True),
                SpecRange("moisture", pmax=3.5, hard=True),
                SpecRange("MgO", 0.45, 0.65, hard=True),
                SpecRange("Cd", pmax=7.0, hard=True),
            ],
            source_quality_ranges={
                "P2O5": "31.1–32.0 %", "BPL": "68.0–70.0 %",
                "CO2": "5.0–6.50 %", "SiO2": "< 4.5 %",
                "H2O": "≤ 3.50 %", "MgO": "0.45–0.65 %",
                "Cd": "< 7 ppm",
            },
            evidence=CONF(
                source, note + " P2O5 is retained as source metadata; BPL "
                "is the active phosphate constraint to avoid duplicating an "
                "equivalent assay.")),
        Grade(
            "BG10", "BG10 train export profile", **common,
            receiving_spec=[
                SpecRange("BPL", 65.0, 67.5, hard=True),
                SpecRange("CO2", 5.0, 6.5, hard=True),
                SpecRange("SiO2", 3.5, 7.0, hard=True),
                SpecRange("moisture", pmax=3.5, hard=True),
                SpecRange("MgO", 0.45, 0.65, hard=True),
                SpecRange("Cd", 7.0, 9.0, hard=True),
            ],
            source_quality_ranges={
                "P2O5": "29.7–30.9 %", "BPL": "65.0–67.5 %",
                "CO2": "5.0–6.50 %", "SiO2": "3.5–7.00 %",
                "H2O": "≤ 3.50 %", "MgO": "0.45–0.65 %",
                "Cd": "7–9 ppm",
            },
            evidence=CONF(
                source, note + " P2O5 is retained as source metadata; BPL "
                "is the active phosphate constraint to avoid duplicating an "
                "equivalent assay.")),
        Grade(
            "BG11", "BG11 train export profile", **common,
            receiving_spec=[
                SpecRange("BPL", 67.0, 69.0, hard=True),
                SpecRange("SiO2", 5.0, 10.0, hard=True),
                SpecRange("SiO2_R", 2.8, 3.6, hard=True),
                SpecRange("F", 3.2, 4.2, hard=True),
            ],
            source_quality_ranges={
                "BPL": "67–69 %", "SiO2 T": "5–10 %",
                "SiO2 R": "2.8–3.6 %", "F": "3.2–4.2 %",
            },
            evidence=CONF(
                source, note + " SiO2 T maps to total SiO2; SiO2 R and F "
                "remain distinct required assays.")),
        Grade(
            "BG-AD", "BG AD train export profile — Ravensdown New Zealand",
            **common,
            receiving_spec=[
                SpecRange("BPL", 64.0, 65.0, hard=True),
                SpecRange("CO2", pmax=5.5, hard=True),
                SpecRange("SiO2", 6.0, 8.5, hard=True),
                SpecRange("MgO", pmax=0.53, hard=True),
                SpecRange("Cd", 8.0, 12.0, hard=True),
                SpecRange("moisture", pmax=3.0, hard=True),
                SpecRange("P2O5_SC", 8.79, 8.93, hard=True),
            ],
            source_quality_ranges={
                "BPL": "64.00–65.00 %", "CO2": "≤ 5.50 %",
                "SiO2": "6.00–8.50 %", "MgO": "≤ 0.53 %",
                "Cd": "8.0–12 ppm", "H2O": "≤ 3.00 %",
                "P2O5 S,C": "8.79–8.93 %",
            },
            evidence=CONF(
                source, note + " 'P2O5 S,C' is a distinct P2O5_SC assay; "
                "its analytical definition remains TBC.")),
    ]


def synchronize_train_export_grades(sc: Scenario) -> bool:
    """Restore the code-defined train receiving contracts when applicable.

    Receiving specifications are model contracts, not operator-managed stock
    grades. This makes hot Streamlit sessions and legacy saved catalogues
    self-healing without installing Benguerir train concepts in scenarios that
    do not contain the BG-TRAIN-PROD destination.
    """
    if not any(destination.id == "BG-TRAIN-PROD"
               for destination in sc.destinations):
        return False

    expected = _train_export_grades()
    expected_by_id = {grade.id: grade for grade in expected}
    current_by_id = {
        grade.id: grade for grade in sc.grades
        if grade.id in expected_by_id
    }
    changed = (
        set(current_by_id) != set(expected_by_id)
        or any(current_by_id[grade_id] != expected_by_id[grade_id]
               for grade_id in expected_by_id
               if grade_id in current_by_id)
    )
    if changed:
        sc.grades = [
            grade for grade in sc.grades
            if grade.id not in expected_by_id
        ] + [deepcopy(grade) for grade in expected]

    required_params = {"SiO2_R", "F", "P2O5_SC"}
    installed_params = {param.name for param in sc.quality_params}
    missing_params = required_params - installed_params
    if missing_params:
        sc.quality_params.extend(
            deepcopy(param) for param in PHOS_PARAMS
            if param.name in missing_params
        )
        changed = True
    return changed


#: M16 humid stock grades, indexed by the receiving profile each one targets.
#: PAP (G10) and the BG train profiles were previously unservable from M16
#: because the seeded TBT/BT/MT vectors carry no CO2 assay at all and sit far
#: above every Cd ceiling.  M16 holds *received washed product*, so its stock
#: legitimately carries post-beneficiation chemistry rather than mine values.
M16_HUMID_STOCK_GRADE_IDS = ("HWP-22", "HWP-10", "HWP-11")


def _m16_humid_stock_grades() -> List[Grade]:
    """Humid washed product held at M16, dimensioned to its two customers.

    Each vector is placed inside a real receiving window rather than near a
    bound, so blending across piles has room to move before a spec is broken.
    Every family here is PAP-capable by design.  BG-AD is deliberately *not*
    covered: it caps CO2 at 5.5% where G10 requires at least 6.0%, so a pile
    dedicated to it could never serve PAP and its tonnage would only be
    reachable on the single BG-AD demand day (project-team ruling 25-Sep-2026,
    measured at ~4 points of weekly service).
    """
    note = (
        "Demonstration vector positioned inside the stated receiving window; "
        "M16 holds received washed product, so post-beneficiation chemistry "
        "is the physically correct basis. Numeric assays still require the "
        "project Grade/SQP register.")
    # No beneficiation lineage is declared: this is product *received* at M16,
    # not a yield of a named raw grade here.  The mass-profile triplet is left
    # entirely unset rather than carrying an invented yield — validation
    # accepts all-None and rejects a partially configured triplet.
    common = {"material_class": "washed"}
    return [
        Grade("HWP-22",
              "Humid washed product — serves PAP G10 and BG-22 export",
              {"BPL": 69.0, "Cd": 6.0, "MgO": 0.55, "SiO2": 4.0,
               "CO2": 6.2, "moisture": 12.0},
              rock_value=82.0, **common,
              evidence=ASSUME(note + " Satisfies G10 and BG-22 together.")),
        Grade("HWP-10",
              "Humid washed product — serves PAP G10 and BG10 export",
              {"BPL": 66.5, "Cd": 8.0, "MgO": 0.55, "SiO2": 5.0,
               "CO2": 6.2, "moisture": 12.0},
              rock_value=75.0, **common,
              evidence=ASSUME(note + " Satisfies G10 and BG10 together.")),
        Grade("HWP-11",
              "Humid washed product — serves PAP G10 and BG11 export",
              {"BPL": 68.0, "Cd": 8.0, "MgO": 0.55, "SiO2": 7.0,
               "CO2": 6.2, "SiO2_R": 3.2, "F": 3.7, "moisture": 12.0},
              rock_value=80.0, **common,
              evidence=ASSUME(
                  note + " Carries the SiO2_R and F assays BG11 requires; a "
                  "missing parameter fails qualification outright.")),
    ]


#: Humid precursors of the BG train profiles, selectable by DRY-FEED demand.
#: BG-AD has no precursor here: its CO2 ceiling is disjoint from PAP's G10
#: window, so no M16 family covers it (see _m16_humid_stock_grades).
DRY_FEED_PRECURSOR_GRADE_IDS = ("BG-22-H", "BG10-H", "BG11-H")


def _dry_feed_precursor_grades() -> List[Grade]:
    """BG export chemistry with the moisture bound lifted, for dryer feed.

    The dryer is fed humid material and removes water; chemistry is stored on
    a dry-solids basis and is therefore invariant across drying.  Requesting
    the export profiles verbatim would demand <=3.5% moisture from ~12% humid
    stock and make DRY-FEED permanently unservable, so each precursor keeps
    the export chemistry and drops only the moisture ceiling.  The BG profiles
    themselves are untouched and still gate BG-TRAIN-PROD.
    """
    note = (
        "Humid precursor of the transcribed Gantour export profile: identical "
        "chemistry, moisture ceiling removed because drying occurs downstream "
        "of this receiving point. Project-team ruling 25-Sep-2026.")
    common = {"material_class": "washed", "is_specification": True,
              "receiving_destinations": ["DRY-FEED"]}
    return [
        Grade("BG-22-H", "BG-22 humid precursor — dryer feed", **common,
              receiving_spec=[
                  SpecRange("BPL", 68.0, 70.0, hard=True),
                  SpecRange("CO2", 5.0, 6.5, hard=True),
                  SpecRange("SiO2", pmax=4.5, hard=True),
                  SpecRange("MgO", 0.45, 0.65, hard=True),
                  SpecRange("Cd", pmax=7.0, hard=True),
              ], evidence=ASSUME(note)),
        Grade("BG10-H", "BG10 humid precursor — dryer feed", **common,
              receiving_spec=[
                  SpecRange("BPL", 65.0, 67.5, hard=True),
                  SpecRange("CO2", 5.0, 6.5, hard=True),
                  SpecRange("SiO2", 3.5, 7.0, hard=True),
                  SpecRange("MgO", 0.45, 0.65, hard=True),
                  SpecRange("Cd", 7.0, 9.0, hard=True),
              ], evidence=ASSUME(note)),
        Grade("BG11-H", "BG11 humid precursor — dryer feed", **common,
              receiving_spec=[
                  SpecRange("BPL", 67.0, 69.0, hard=True),
                  SpecRange("SiO2", 5.0, 10.0, hard=True),
                  SpecRange("SiO2_R", 2.8, 3.6, hard=True),
                  SpecRange("F", 3.2, 4.2, hard=True),
              ], evidence=ASSUME(note)),
    ]


def _phos_grades(include_shared_raw_bt: bool = False) -> List[Grade]:
    grades = [
        Grade("TBT", "TBT — demonstration quality vector; assay TBC",
              {"BPL": 63.0, "Cd": 15.0, "MgO": 0.78, "SiO2": 3.5,
               "CO2": 6.45, "moisture": 12.0},
              rock_value=65.0,
              material_class="washed",
              beneficiation_yield=_BEN_B02_BENEFICIATION_YIELD["TBT"],
              raw_moisture_pct=_BEN_B02_RAW_MOISTURE_PCT,
              product_moisture_pct=_BEN_B02_PRODUCT_MOISTURE_PCT,
              source_grade_id="RAW-BT",
              evidence=ASSUME(
                  "simulation-only vector; corrected to the BoD semantic "
                  "order TBT < BT < MT; numeric assays still require the "
                  "project Grade/SQP register. Benguerir B02 beneficiation "
                  "yield is documented; 12% raw moisture and 17.5% wet-"
                  "product moisture are planning assumptions.")),
        Grade("BT", "BT — demonstration quality vector; assay TBC",
              {"BPL": 68.0, "Cd": 12.5, "MgO": 0.68, "SiO2": 2.8,
               "CO2": 6.25, "moisture": 12.0},
              rock_value=80.0,
              material_class="washed",
              beneficiation_yield=_BEN_B02_BENEFICIATION_YIELD["BT"],
              raw_moisture_pct=_BEN_B02_RAW_MOISTURE_PCT,
              product_moisture_pct=_BEN_B02_PRODUCT_MOISTURE_PCT,
              source_grade_id="RAW-BT",
              evidence=ASSUME(
                  "simulation-only vector; corrected TBT < BT < MT semantic "
                  "order; numeric assay remains TBC. Benguerir B02 "
                  "beneficiation yield is documented; 12% raw moisture and "
                  "17.5% wet-product moisture are planning assumptions.")),
        Grade("MT", "MT — demonstration quality vector; assay TBC",
              {"BPL": 74.0, "Cd": 10.0, "MgO": 0.58, "SiO2": 2.0,
               "CO2": 6.10, "moisture": 12.0},
              rock_value=100.0, material_class="washed",
              beneficiation_yield=_BEN_B02_BENEFICIATION_YIELD["MT"],
              raw_moisture_pct=_BEN_B02_RAW_MOISTURE_PCT,
              product_moisture_pct=_BEN_B02_PRODUCT_MOISTURE_PCT,
              source_grade_id="RAW-BT",
              evidence=ASSUME(
                  "simulation-only vector; corrected to the BoD semantic "
                  "order TBT < BT < MT; numeric assay remains TBC. "
                  "Benguerir B02 beneficiation yield is documented; 12% raw "
                  "moisture and 17.5% wet-product moisture are planning "
                  "assumptions.")),
        Grade(RAW_PENDING_GRADE_ID,
              "Raw/screened phosphate — chemical grade TBC",
              {}, material_class="raw_screened",
              evidence=TBC("mine assay register not received",
                           "placeholder classification only; never use the "
                           "washed TBT/BT/MT vectors for raw ore")),
    ] + _m16_humid_stock_grades() + _dry_feed_precursor_grades()

    for family in MERCHANT_GRADE_FAMILIES:
        family_grade = next(grade for grade in grades
                            if grade.id == family)
        for raw_grade in RAW_BT_GRADE_IDS[1:]:
            washed_grade = WASHED_GRADE_BY_RAW_AND_FAMILY[
                raw_grade][family]
            source_label = (
                "High-Cd" if raw_grade.endswith("HIGH-CD") else "Low-Cd")
            grades.append(Grade(
                washed_grade,
                f"{family} — {source_label} washed profile",
                dict(_WASHED_CD_VARIANT_QUALITY[washed_grade]),
                rock_value=family_grade.rock_value,
                material_class="washed",
                source_grade_id=raw_grade,
                beneficiation_yield=_BEN_B02_BENEFICIATION_YIELD[family],
                raw_moisture_pct=_BEN_B02_RAW_MOISTURE_PCT,
                product_moisture_pct=_BEN_B02_PRODUCT_MOISTURE_PCT,
                evidence=ASSUME(
                    "Provisional washed chemistry preserving the raw Cd "
                    "lineage. Cd follows the raw-profile ratio relative "
                    "to BT; BPL, MgO and SiO2 are modest ordered "
                    "simulation adjustments. The family yield/moisture "
                    "basis is inherited; all assay values require the "
                    "approved washed-product grade register.")))
    if include_shared_raw_bt:
        # Shared mine-neutral raw-grade catalogue.  The three B02 rows add
        # documented MT / TBT chemistry without changing the
        # current BT/High-Cd/Low-Cd campaign routes.  Site-specific
        # physical properties and live pile assays stay on MaterialProfile
        # and inventory records rather than being encoded in the grade ID.
        grades += [
            Grade(
                "RAW-MT", "Raw MT — nominal profile",
                dict(_BEN_B02_RAW_GRADE_QUALITY["RAW-MT"]),
                material_class="raw_screened",
                evidence=CONF(
                    _B02_PROCESS,
                    "§5.2.1 raw-feed chemistry; shared mine-neutral grade "
                    "identity. The displayed 12% moisture is the planning "
                    "midpoint of the documented 10–14% range, not a live "
                    "pile measurement.")),
            Grade(
                "RAW-BT", "BT",
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
            Grade(
                "RAW-TBT", "Raw TBT — nominal profile",
                dict(_BEN_B02_RAW_GRADE_QUALITY["RAW-TBT"]),
                material_class="raw_screened",
                evidence=Evidence(
                    source="project-team supplied TBT assay, 19-Sep-2026; "
                           + _B02_PROCESS + " moisture basis",
                    status="CONFIRMED", confidence="M",
                    note="BPL/Cd/MgO/SiO2/CO2 use the supplied TBT assay. "
                         "Its 5% H2O value is intentionally excluded; the "
                         "displayed 12% moisture remains the midpoint of the "
                         "documented 10–14% planning range.")),
        ]
        grades += _train_export_grades()
    return grades


def _phos_material_profiles(include_mines: bool) -> List[MaterialProfile]:
    """Declared material states; chemical gaps remain explicit, never inferred."""
    washed_grade_ids = list(WASHED_GRADE_IDS)
    profiles = [MaterialProfile(
        "M16-WASHED-HUMID", "MPH", "M16 reception / humid storage",
        "washed", "Washed humid phosphate received from C1/C2",
        # The HWP-* families are M16's own received-product stock: washed
        # chemistry carrying the CO2 assay that PAP's G10 window requires.
        grade_ids=washed_grade_ids + list(M16_HUMID_STOCK_GRADE_IDS),
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
            grade_ids=[*RAW_GRADE_IDS, RAW_PENDING_GRADE_ID],
            quality_ranges={
                "BPL": "54.10–58.93 % across the shared raw catalogue; live pile assay TBC",
                "Cd": "13–25 ppm across the shared raw catalogue; live pile assay TBC",
                "MgO": "1.10–1.52 % across the shared raw catalogue; live pile assay TBC",
                "SiO2": "10.13–16.28 % across the shared raw catalogue; live pile assay TBC",
                "CO2": "6.54–8.75 % across the shared raw catalogue; live pile assay TBC",
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
            grade_ids=list(WASHED_GRADE_IDS),
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
            grade_ids=list(WASHED_GRADE_IDS),
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
            transformation_note="Profile used by BG-P1/BG-P2. Merchant "
                                "family and raw Cd lineage are separate; all "
                                "washed vectors remain demonstration placeholders "
                                "until the washed-product grade register arrives.",
            evidence=TBC(_B02, "physical state documented; chemical assay "
                                  "ranges, grade cut-offs and pile allocation TBC")),
        MaterialProfile(
            "LOU-MINE-PHOSPHATE", "LOUTA", "mine storage / pre-beneficiation",
            "raw_screened", "Louta mine phosphate",
            grade_ids=list(RAW_GRADE_IDS),
            quality_ranges={
                "BPL": "54.10–58.93 % across the shared raw catalogue",
                "Cd": "13–25 ppm across the shared raw catalogue",
                "MgO": "1.10–1.52 %",
                "SiO2": "10.13–16.28 %",
                "CO2": "6.54–8.75 %",
                "CaO_CO2": "TBC — source provides CO2, not CaO/CO2",
                "moisture": "15 % (Ref 8)",
            },
            physical_properties={"F100": "10 mm", "F80": "400–550 µm",
                                 "specific_gravity": "2.9 (calculated)"},
            transformation_note="Shared mine-neutral raw catalogue. The "
                                "BT/High-Cd/Low-Cd vectors come from the "
                                "Louta BoD; MT/TBT chemistry comes "
                                "from B02. Active pile allocation remains "
                                "unchanged.",
            evidence=CONF(_LOU_BOD + " + " + _B02_PROCESS,
                          "Louta §6.1 raw BT cases plus B02 §5.2.1 raw "
                          "chemistry; catalogue IDs are mine-neutral")),
        MaterialProfile(
            "LOU-WASHED-PRODUCT", "LOUTA", "product storage / C1 feed",
            "washed", "Louta washed phosphate product to C1",
            grade_ids=list(WASHED_GRADE_IDS),
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
    # Opening stock sized to HALF the weekly M16 draw (project team,
    # 28-Sep-2026): PAP-1 38,500 t + DRY-FEED 134,400 t = 172,900 t/week, so
    # ~86,500 t on the ground. The former 424,000 t was ~2.5 WEEKS of cover,
    # which is why no corridor delivery or mine campaign was ever needed and
    # most of the fleet stood idle - the yard could serve the week without
    # them. The bulk sits in HP-01 as instructed; HP-02 and HP-05 carry just
    # over their 15,000 t safety levels so they are not born in breach, and
    # the remainder is spread thinly so both reclaim groups have something.
    grades = {"HP-01": ("HWP-22", 52_000), "HP-02": ("HWP-10", 16_000),
              "HP-03": ("HWP-11", 1_000), "HP-04": ("HWP-22", 1_000),
              "HP-05": ("HWP-10", 16_000), "HP-06": ("HWP-10", 500),
              "HP-07": ("HWP-22", 0), "HP-08": ("HWP-10", 0)}
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
            mid, "MPH", kind, 0, _rates[kind], reach, rail=line,
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
        # Priority is an objective WEIGHT on unmet demand, not a rank: higher
        # is served first and 0 disables the term. PAP carried 1 against
        # DRY-FEED 2 and BG-TRAIN 3, so the hub's principal customer was the
        # LOWEST-weighted receiver and the planner correctly abandoned it -
        # measured 0 of 38,500 t while drying took 80%. Reordered 28-Sep-2026
        # to PAP 3 > drying 2 > train export 1.
        Destination("PAP-1", "MPH", "DEST:PAP-1", priority=3,
                    demand_default_tph=1500,
                    requested_grade="G10",
                    spec=[],
                    evidence=CONF(
                        _PRO,
                        "1500 tph/line confirmed; requested grade G10 and "
                        "its hard receiving intervals are provisional "
                        "project-model inputs")),
        Destination(
            "PAP-2", "MPH", "DEST:PAP-2", priority=3,
            demand_default_tph=1500,
            requested_grade="G10",
            spec=[],
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
    # G10 is PAP's permanent receiving specification. The washing toggle
    # controls treatment availability, never the PAP demand grade.
    from .washing import synchronize_pap
    synchronize_pap(sc)
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
        Segment("HP-02", 0, 150, 8_000, "HWP-10", age_days=20),
        Segment("HP-02", 150, 300, 8_000, "HWP-10", age_days=5),
        Segment("HP-05", 0, 300, 16_000, "HWP-10", age_days=12),
        Segment("HP-01", 0, 300, 52_000, "HWP-22", age_days=30),
    ]
    sc.calendars = [CalendarEntry("ST01-C", 0, 5, "planned maintenance (demo)")]
    # Same revision stamp as S3: both built-ins share the M16 yard, so a
    # corrected M16 must invalidate a hot-reloaded S2 session too.
    sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
    # Fresh S2 must already match the shared migration schema. Otherwise its
    # first navigation after solving installs this disabled policy and
    # mistakes that missing default for a solver-input upgrade.
    sc.config["CHEVRON_STACKING"] = _default_chevron_stacking_config()
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=24, frozen_buckets=2,
                       horizon_minutes=1440, seed=42)
    sc.weights = ObjectiveWeights()
    _install_operational_objective(
        sc, ["PAP-1", "PAP-2", "DRY-FEED"])
    sc.assumptions = [
        Assumption("S2-A1", "Pile contents/grades and initial masses", "M16/inventory",
                   source="placeholder", placeholder="HP-01…08 table",
                   impact="all blending results", action="SMS SpaceValue snapshot"),
        Assumption(
            "S2-A2",
            "PAP-1 and PAP-2 demand only the G10 receiving specification. "
            "Its current six assay intervals are hard but provisional model "
            "inputs, not an approved plant standard.",
            "PAP/quality", source="project-team requirement + placeholder",
            placeholder="provisional hard G10 intervals",
            impact="qualified PAP service and unmet demand",
            action="process to provide the approved contractual G10 spec"),
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
                   "documented 10-minute gate/route changeover LOSS is NOT "
                   "MODELLED: group switches are counted and penalised, but "
                   "changeover_min = 0 on every link and no switching capacity "
                   "loss is enforced.", "M16/routes",
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
            "option with the same 1,500 t/h / G10 demand and quality template "
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
        "BG-HOP-TM1": "RAW-TBT",
        "BG-HOP-TSM2": "RAW-TBT",
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
            "RAW-TBT", x_from=0, x_to=500, safety_stock_t=20_000,
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
            "RAW-TBT", x_from=0, x_to=500, safety_stock_t=20_000,
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
            "BG-MINE-BWR", "BENGUERIR", "reclaimer", 0, 2100,
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
            "BG-RAW-BWR", "BENGUERIR", "reclaimer", 0, 2100,
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
                f"SPACE:{wash_wip}", 2100.0,
                material_class="raw",
                evidence=ASSUME(
                    "Physical raw-feed route envelope aligned with the "
                    "documented 2,100 t/h screened-product reclaimer. The "
                    "separate B02 671 dry-solids t/h process ceiling is "
                    "converted dynamically on ProcessTransfer.")),
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
                mass_conversion="beneficiation",
                dry_feed_capacity_tph=_BEN_B02_DRY_FEED_TPH,
                purpose=f"Produce configured Benguerir line L{suffix} output",
                evidence=ASSUME(
                    "B02 documents the output-grade beneficiation yields. "
                    "Line-to-grade/raw-campaign mapping and moisture defaults "
                    "remain simulation assumptions.")),
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

    # The shared raw-product reclaim header can route either screened pile to
    # each washing line.  Keep every alternative explicit so enabling the
    # pre-washing blend policy cannot invent a route.  The original
    # same-pile transfers above remain the no-blend baseline.
    benguerir_cross_feed = {
        "BEN-WASH-L1": ("BG-RAW-2", "L-BG-WASH-IN-L2"),
        "BEN-WASH-L2": ("BG-RAW-1", "L-BG-WASH-IN-L1"),
        "BEN-WASH-L3": ("BG-RAW-2", "L-BG-WASH-IN-L2"),
    }
    for line_id, (source_space, inlet_link) in benguerir_cross_feed.items():
        suffix = line_id[-1]
        case = _BEN_WASH_CASES[line_id]
        sc.process_transfers.append(ProcessTransfer(
            f"BG-L{suffix}-WASH-BLEND-ALT", "BG-RAW-BWR",
            source_space, f"BG-WASH-L{suffix}-WIP",
            [inlet_link, f"L-BG-WASH-L{suffix}"],
            operation_kind="reclaim", process_stage=line_id,
            output_grade=str(case["output_grade"]),
            yield_factor=float(case["recovery"]), residence_min=30,
            mass_conversion="beneficiation",
            dry_feed_capacity_tph=_BEN_B02_DRY_FEED_TPH,
            purpose=(f"Blend alternate screened source into Benguerir "
                     f"washing campaign L{suffix}"),
            evidence=ASSUME(
                "Pre-washing feed-blend alternative through the existing "
                "shared reclaim header. Share limits are commissioning "
                "inputs; transfer-gate sequencing and plant matrices TBC.")))

    sc.config["BEN_SIX_PILE_CHAIN_ENABLED"] = True
    sc.config["BEN_MINE_HOPPER_IDS"] = [
        "BG-HOP-TM1", "BG-HOP-TSM2", "BG-HOP-TSM3"]
    sc.config["BEN_WASH_LINE_CASE"] = {
        line_id: (
            "LOW_CD" if line_id == "BEN-WASH-L2" else "TBT")
        for line_id in _BEN_WASH_CASES
    }
    sc.config["BEN_WASH_OUTPUT_GRADE_MAPPING"] = {
        line_id: str(case["output_grade"])
        for line_id, case in _BEN_WASH_CASES.items()
    }
    sc.config["BEN_WASH_OUTPUT_GRADE_MAPPING_BASIS"] = (
        "ASSUMED S0 merchant-family mapping for simulation only. Raw Cd "
        "lineage is preserved explicitly in each washed grade ID; no "
        "approved Benguerir washed-product grade register is available.")
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


def _alternative_mph_blend_matrix() -> Dict[str, object]:
    """Editable dry-feed example using other existing washed grade inputs.

    With the built-in equal-moisture assays, BT 40% + MT 60% gives BPL 71.6
    and Cd 25.2 ppm. This is a software example, not an approved plant recipe
    or a G10 certificate. Actual receiving checks still use active assays.
    Adding the recipe does not create BT/MT corridor supply or a rehandling
    route from existing MPH stock; supply eligibility remains a solver input.
    """
    return {
        "id": "MPH-HUMID-02",
        "name": "BT / MT dry-feed example",
        "components": [
            {"grade_id": "BT", "target_fraction": 0.40},
            {"grade_id": "MT", "target_fraction": 0.60},
        ],
    }


def _default_chevron_stacking_config() -> Dict[str, object]:
    """Return a fresh copy of the built-in M16 pile-blending policy."""
    return {
        "model_version": 7,
        "example_catalog_revision": 2,
        "quantity_mode": "DEMAND_DRIVEN",
        "min_input_grade_t": 500.0,
        "stacking_traverse_speed_m_min": None,
        "enabled": True,
        "priority": 1,
        "global_mode": "OPTIMIZED",
        "matrix_catalog": [{
            "id": "MPH-HUMID-01",
            "name": "MPH humid-phosphate matrix 1",
            "components": [
                {"grade_id": "TBT", "target_fraction": 0.5},
                {"grade_id": "BT-LOW-CD", "target_fraction": 0.5},
            ],
        }, _alternative_mph_blend_matrix()],
        "optimized_share_bounds": [
            {"grade_id": "TBT", "min_fraction": 0.0,
             "max_fraction": 1.0},
            {"grade_id": "BT-LOW-CD", "min_fraction": 0.0,
             "max_fraction": 1.0},
        ],
        "proposed_recipes_by_space": {},
        "proposed_matrix_by_space": {},
        "proposed_campaigns_by_space": {},
        "campaign_states_by_space": {},
        "enabled_spaces": [f"HP-{index:02d}" for index in range(1, 9)],
        "point_labels": {
            f"HP-{index:02d}": f"M16 humid-phosphate stockpile {index}"
            for index in range(1, 9)
        },
    }


def apply_all_enabled_blending_defaults(sc: Scenario) -> None:
    """Apply the explicitly requested stockpile defaults once, not on reruns.

    Callers own the one-time migration guard. Existing physical campaign
    state, catalogue rows, component bounds, assays and inventories survive.
    Operator edits after this revision must not be overwritten on navigation.
    """
    policy = deepcopy(sc.config.get("CHEVRON_STACKING", {}) or {})
    if policy:
        policy["enabled_spaces"] = sorted(
            space.id for space in sc.spaces
            if space.site == "MPH" and space.area == "M16-humid")
        policy["enabled"] = bool(policy["enabled_spaces"])
        policy["global_mode"] = "OPTIMIZED"
        sc.config["CHEVRON_STACKING"] = policy
    prewash = deepcopy(sc.config.get("PRE_WASHING_BLENDING", {}) or {})
    if prewash:
        prewash["enabled"] = True
        for point in prewash.get("points", {}).values():
            rows = list(point.get("optimized_components", point.get("components", [])))
            rows += [row for matrix in point.get("matrix_catalog", [])
                     for row in matrix.get("components", [])]
            point["enabled_source_spaces"] = sorted({
                str(row["source_space"]) for row in rows if row.get("source_space")})
            point["enabled_process_stages"] = list(point.get("process_stages", []))
            point["share_mode"] = "OPTIMIZED"
            point["allowed_treatment_modes"] = ["simple", "full"]
        sc.config["PRE_WASHING_BLENDING"] = prewash
    sc.config["BLENDING_DEFAULTS_REVISION"] = 1


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
        "WASHED_GRADE_SOURCE_MAPPING": dict(WASHED_SOURCE_GRADE),
        "WASHED_GRADE_BY_RAW_AND_FAMILY": deepcopy(
            WASHED_GRADE_BY_RAW_AND_FAMILY),
        "WASHED_GRADE_MAPPING_BASIS": (
            "ASSUMED simulation taxonomy: merchant family and raw Cd "
            "profile are independent dimensions. Existing TBT/BT/MT IDs "
            "are the BT variants for compatibility."),
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
        # Withdrawn with the stocked-route pins above (27-Sep-2026). Louta is
        # now driven by demand and week-end operating floors like Benguerir,
        # not by fixed campaign quantities.
        "LOU_S0_STOCKED_ROUTE_ENABLED": False,
        "LOU_WASHING_RESIDENCE_MIN": 30,
        "LOU_WASH_LINE_CASE": {
            "LOU-WASH-L1": "BT",
            "LOU-WASH-L2": "LOW_CD",
        },
        "LOU_WASH_OUTPUT_GRADE_MAPPING": {
            "LOU-WASH-L1": "TBT",
            "LOU-WASH-L2": "BT-LOW-CD",
        },
        "LOU_WASH_OUTPUT_GRADE_MAPPING_BASIS": (
            "ASSUMED S0 campaign mapping only. The Louta BoD documents "
            "BT/High-Cd/Low-Cd raw ore cases and one combined product "
            "characteristic. The Low-Cd suffix preserves feed lineage; the "
            "numeric washed assay remains provisional."),
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
            "BG-RAW-1": "RAW-BT",
            "BG-RAW-2": "RAW-BT",
        },
        "BG_RAW_DISPLAY_GRADE_CATALOG": list(RAW_GRADE_IDS),
        "BG_RAW_DISPLAY_GRADE_COMPONENTS": {
            "BG-RAW-1": [
                {
                    "component_id": "C1",
                    "mass_fraction": 0.5,
                    "grade_id": "RAW-BT",
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
                    "grade_id": "RAW-BT",
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
            "BT/High-Cd/Low-Cd raw BT grade taxonomy is shared by "
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
            "pp. 14-15 (BT / High-Cd / Low-Cd). The chemistry is "
            "source-backed; its assignment to 102MG piles is not"),
        "LOU_MINE_102MG_DISPLAY_GRADE_COMPOSITION_BASIS": (
            "ASSUMED display-only mapping explicitly adopted 26-Jul-2026: "
            "102MG-SP-01 = 50% BT + 50% High-Cd; 102MG-SP-02 = 50% "
            "BT + 50% Low-Cd. Each 25,000 t band occupies a schematic "
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
            "(BT / High-Cd / Low-Cd) — the same three ore cases the "
            "process description names on p. 5. Chemistry is taken verbatim, "
            "including the Louta 15 % moisture; nothing is invented"),
        "LOU_ORE_DISPLAY_GRADE_COMPOSITION_BASIS": (
            "ASSUMED display distribution (single source of this claim): "
            "110LG-OL1 = 50% BT + 50% High-Cd and 110LG-OL2 = 50% "
            "BT + 50% Low-Cd by mass, so all three documented profiles "
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
                "ASSUMED demonstration composition: 50% BT + 50% "
                "High-Cd by mass. The profiles are source-backed; the shares "
                "are schematic/non-spatial and do not define a reclaim "
                "sequence. Solver inventory is model-backed and planning "
                "uses the pile's aggregate/designated campaign grade."
            ),
            "110LG-OL2": (
                "ASSUMED demonstration composition: 50% BT + 50% "
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
                "ASSUMED demonstration composition: 60% BT-LOW-CD + 40% "
                "MT-LOW-CD by mass. Low-Cd quality vectors and their Louta "
                "product-pile mapping remain TBC. Shares are schematic and non-spatial; "
                "they do not define longitudinal order, boundaries or a "
                "reclaim sequence."
            ),
        },
        # M16 blending is created while a humid-phosphate pile is constructed.
        # Each input grade remains a separate stack job; its physical tonnes
        # are constrained to the configured recipe. Reclaim then uses the
        # recipe-weighted average quality of that completed campaign.
        # Version 5 supports either an operator matrix or solver-selected
        # shares. The helper also gives hot-session migration a safe source of
        # defaults even when a test/import wrapper replaces a scenario builder.
        "CHEVRON_STACKING": _default_chevron_stacking_config(),
        # Enabled S0 commissioning model: each washing line/day draws both
        # physically routeable pre-wash stockpiles within broad 5--95 %
        # bounds. These are optimization limits, not an approved plant matrix.
        "PRE_WASHING_BLENDING": default_prewash_blending_config(enabled=True),
        # The Louta S0 stocked-route pins are REMOVED (27-Sep-2026).
        #
        # They were an ASSUMED project-team demonstration policy of
        # 28-Jul-2026 (ASSUMPTIONS.md S3-A27), absent from every design PDF,
        # which fixed C1 dispatch at exactly 8,000 t/week, each washing line
        # at exactly 4,000 t, and forced the ore lanes to END the week at
        # their opening mass. Together those made Louta a closed loop whose
        # throughput no demand signal could change: measured across four
        # levers (downstream demand, PAP-2, campaign calendar, pricing the
        # process feed) the chain stayed at 1 of 5 machines every time.
        # S3-A27's own remediation asks for exactly this - "replace
        # demonstration targets with approved daily source allocation/end-stock
        # policy; retain demand-only mode when disabled". Week-end operating
        # floors (``operating_stock_floors``) now supply the replenishment
        # reason instead, without pinning throughput.
        "PROCESS_NO_PASSTHROUGH_SPACES": [
            "LT-WASH-L1-WIP", "LT-WASH-L2-WIP",
        ],
        "TERMINAL_INVENTORY_MIN_T": {},
        "TERMINAL_INVENTORY_EXACT_T": {
            # Work in progress must not be stranded inside a washing line at
            # the horizon end. This is physical housekeeping, not the
            # withdrawn stocked-route policy.
            "LT-WASH-L1-WIP": 0.0,
            "LT-WASH-L2-WIP": 0.0,
        },
        "FLOW_TONNE_PENALTY": 1,
        "CAPACITY_ENVELOPE": "nominal",   # design (3,120/2,520/1,200) only by
        #                                 # an explicit envelope dataset
    })
    sc.spaces += [
        StockSpace("BG-P1", "BENGUERIR", "mine-yard", "open", 191_512, 10_000,
                   "TBT", x_from=0, x_to=500, safety_stock_t=30_000,
                   state="mixed",
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
        StockSpace("BG-P2", "BENGUERIR", "mine-yard", "open", 191_512, 10_000,
                   "BT-LOW-CD", x_from=0, x_to=500, safety_stock_t=30_000,
                   state="mixed",
                   material_class="washed", profile_id="BEN-SETTLED-PRODUCT",
                   evidence=CONF(_B02, "as BG-P1")),
    ]
    _b02_note = ("CURRENT CONFIGURATION per project-team instruction; "
                 "B02 '**' retained as source notation only"
                 if ben_b02_enabled else
                 "CURRENT CONFIGURATION disabled only for a non-baseline "
                 "sensitivity: reference asset, planner must not use it")
    sc.machines += [
        Machine("RC2", "BENGUERIR", "reclaimer", 0, 2600,
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
        Machine("RC3", "BENGUERIR", "reclaimer", 0, 2600,
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
             "inventories; the calibrated G10 demonstration uses nominal "
             "RAW-TBT on BG-RAW-1 and Low-Cd on BG-RAW-2. Multiple "
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
            requested_grade="RAW-BT",
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
                    # Ranked below PAP (3) and drying (2): see the PAP-1 note.
                    priority=1, demand_default_tph=0,
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
            "RAW-BT", x_from=0, x_to=1,
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
            "RAW-BT", x_from=0, x_to=500,
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
            10_000, 0, "BT-LOW-CD", state="empty",
            designated_grade="BT-LOW-CD",
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
            state="mixed",
            quality=dict(_LOUTA_PRODUCT_COMPOSITE_QUALITY["LT-P1"]),
            x_from=0, x_to=500, safety_stock_t=25_000,
            material_class="washed", profile_id="LOU-WASHED-PRODUCT",
            evidence=DERV(
                _LOU_DB,
                "total product capacity 383,024 t confirmed (design basis); "
                "equal two-lane split DERIVED/TBC — lane count & geometry "
                "configurable; integrated demonstration starts at 10,000 t so "
                "demand exercises washing; grade distribution and composite "
                "quality ASSUMED/TBC")),
        StockSpace(
            "LT-P2", "LOUTA", "mine-yard", "open",
            _LOUTA_PRODUCT_CAPACITY_T, _LOUTA_PRODUCT_START_MASS_T,
            "BT-LOW-CD", state="mixed",
            quality=dict(_LOUTA_PRODUCT_COMPOSITE_QUALITY["LT-P2"]),
            x_from=0, x_to=500, safety_stock_t=25_000,
            material_class="washed", profile_id="LOU-WASHED-PRODUCT",
            evidence=DERV(
                _LOU_DB,
                "as LT-P1 — DERIVED/TBC split; 10,000 t demonstration start, "
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
            "120LG-RC-01", "LOUTA", "reclaimer", 0, 3000,
            ["110LG-OL1"], rail="120LG-R1", position_x=250,
            physical_tag="120LG-RC-01", lifecycle="SELECTED",
            planning_enabled=True,
            evidence=DERV(
                _LOU_PD,
                "tag and 3,000 t/h wet route confirmed; OL1 pairing is the "
                "conservative applied S0 geometry pending a GA/reach matrix")),
        Machine(
            "120LG-RC-02", "LOUTA", "reclaimer", 0, 3000,
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
        Machine("140LG-RC-01", "LOUTA", "reclaimer", 0, 3000,
                ["LT-P1"], rail="140LG-RL", position_x=250,
                physical_tag="140LG-RC-01", lifecycle="SELECTED",
                evidence=DERV(_LOU_DB,
                              "tag CONFIRMED (L02 philosophy) · 3,000 t/h "
                              "nominal (design basis) · route 140LG-CV-03/RL1 "
                              "then flip gate to RL3/RL4 → C1 · pile-reach "
                              "matrix TBC (both lanes assumed reachable)")),
        Machine("140LG-RC-02", "LOUTA", "reclaimer", 0, 3000,
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
             "direct mine inlet handoff — BT case", 0, 15),
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
             evidence=CONF(_LOU_PD, "as BT campaign inlet")),
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
             capacity_tph=_LOUTA_WASH_CASES["BT"]["feed_tph"],
             material_class="raw",
             evidence=CONF(
                 _LOU_BOD + " + " + _LOU_PD,
                 "RB6 to L1; BT-case scrubber feed 584 t/h")),
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
            output_grade="RAW-BT",
            purpose="Restore ore-line 1 inventory consumed by washing line L1",
            evidence=ASSUME(
                "S0 campaign maps the BT raw case to ore pile 1. "
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
            yield_factor=_LOUTA_WASH_CASES["BT"]["recovery"],
            residence_min=30,
            purpose="Produce the configured line-L1 stocked-route allocation",
            evidence=ASSUME(
                "The BT-case 584 t/h feed and 68.22% recovery are "
                "documented. Mapping that case to L1/TBT and RC-01/pile 1, "
                "plus the 30-minute overall residence, are S0 assumptions.")),
        ProcessTransfer(
            "LOU-WASH-L2-LOW-CD", "120LG-RC-02",
            "110LG-OL2", "LT-WASH-L2-WIP",
            ["L-LT-OL2-RB", "L-LT-RB45-COMMON", "L-LT-RB7-L2"],
            operation_kind="reclaim",
            process_stage="LOU-WASH-L2",
            output_grade="BT-LOW-CD",
            yield_factor=_LOUTA_WASH_CASES["LOW_CD"]["recovery"],
            residence_min=30,
            purpose="Produce the configured line-L2 stocked-route allocation",
            evidence=ASSUME(
                "The Low-Cd-case 588 t/h feed and 67.71% recovery are "
                "documented. Mapping that case to L2/BT and RC-02/pile 2, "
                "with explicit Low-Cd product lineage, "
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
            output_grade="BT-LOW-CD",
            yield_factor=1.0,
            depends_on_stage="LOU-WASH-L2",
            dependency_delay_min=30,
            purpose="Replenish product line 2 after the modelled washing delay",
            evidence=ASSUME(
                "As LOU-PRODUCT-STACK-L1 for the assumed L2/product-line-2 "
                "campaign.")),
    ]

    # Both ore reclaim routes meet at the RB4/RB5 common header before the
    # washing-line split. Expose the cross-feed combinations explicitly;
    # OL1 remains controlled by RC-01 and OL2 by RC-02.
    sc.process_transfers += [
        ProcessTransfer(
            "LOU-WASH-L1-LOW-CD-BLEND-ALT", "120LG-RC-02",
            "110LG-OL2", "LT-WASH-L1-WIP",
            ["L-LT-OL2-RB", "L-LT-RB45-COMMON", "L-LT-RB6-L1"],
            operation_kind="reclaim", process_stage="LOU-WASH-L1",
            output_grade="TBT", residence_min=30,
            purpose="Blend ore-pile 2 into the washing-line L1 campaign",
            evidence=ASSUME(
                "Cross-feed through the documented common RB4/RB5 header "
                "and line-selection split; operating sequence and approved "
                "blend matrix TBC.")),
        ProcessTransfer(
            "LOU-WASH-L2-GLOBAL-BLEND-ALT", "120LG-RC-01",
            "110LG-OL1", "LT-WASH-L2-WIP",
            ["L-LT-OL1-RB", "L-LT-RB45-COMMON", "L-LT-RB7-L2"],
            operation_kind="reclaim", process_stage="LOU-WASH-L2",
            output_grade="BT-LOW-CD", residence_min=30,
            purpose="Blend ore-pile 1 into the washing-line L2 campaign",
            evidence=ASSUME(
                "Cross-feed through the documented common RB4/RB5 header "
                "and line-selection split; operating sequence and approved "
                "blend matrix TBC.")),
    ]
    sc.destinations += [
        Destination(
            "LOU-WASH-L1", "LOUTA", "SPACE:LT-WASH-L1-WIP",
            priority=1,
            demand_default_tph=(
                _LOUTA_S0_LINE_FEED_TARGET_T["LOU-WASH-L1"]
                / sc.sim.horizon_buckets / sc.sim.dt_h),
            requested_grade="RAW-BT",
            demand_kind="process_feed", process_stage="LOU-WASH-L1",
            evidence=DERV(
                _LOU_BOD + " + S0 stocked-route policy",
                "5,864 t is the exact daily input under the shared yield "
                "model for the configured 4,000 t BT-case recovered "
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
                "5,908 t is the exact daily input under the shared yield "
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
            "the end of the 168 h weekly horizon, M16 on-ground inventory "
            "plus C1/C2 in-transit material "
            "must not exceed its t0 value. Late dispatch remains explicit "
            "terminal in-transit. This blocks surplus machine-activation "
            "tonnage while allowing demand/capacity-backed replenishment."),
        "MACHINE_COVERAGE_REWARD": 0,
        # A monolithic 168-hour CP-SAT model cannot reliably produce an
        # incumbent within an interactive planning budget.  Solve seven
        # state-coupled civil-day windows instead; demand, contingencies and
        # jobs remain hourly and use their absolute week bucket/date.
        "WEEKLY_ROLLING_SOLVE_ENABLED": True,
        "WEEKLY_TOTAL_WALL_TIME_S": 84.0,
        "WEEKLY_WINDOW_WALL_TIME_S": 12.0,
        # Cd-profile variants remain distinct chemical grades, but M16 stores
        # them in the matching merchant-family pile.  Without this explicit
        # physical compatibility, Louta/Benguerir low-Cd product could enter
        # C1/C2 yet would have no legal receiving stockpile at M16.
        "STORAGE_GRADE_COMPATIBILITY": {
            "TBT-HIGH-CD": ["TBT"],
            "TBT-LOW-CD": ["TBT"],
            "BT-HIGH-CD": ["BT"],
            "BT-LOW-CD": ["BT"],
            "MT-HIGH-CD": ["MT"],
            "MT-LOW-CD": ["MT"],
        },
    })

    # Benguerir process inflow is represented only by the executable
    # six-pile transfers added below; no parallel exogenous wash source.
    sc.exogenous = []
    sc.segments = [
        Segment("HP-02", 0, 150, 8_000, "HWP-10", age_days=20),
        Segment("HP-02", 150, 300, 8_000, "HWP-10", age_days=5),
        Segment("HP-05", 0, 300, 16_000, "HWP-10", age_days=12),
        Segment("BG-MINE-1", 0, 250, 100_000, "RAW-TBT",
                age_days=2),
        Segment("BG-MINE-2", 0, 250, 100_000, "RAW-BT-LOW-CD",
                age_days=2),
        Segment("BG-RAW-1", 0, 250, 100_000, "RAW-TBT",
                age_days=4),
        Segment("BG-RAW-2", 0, 250, 100_000, "RAW-BT-LOW-CD",
                age_days=4),
        Segment("BG-P1", 0, 500, 10_000, "TBT", age_days=8),
        Segment("BG-P2", 0, 500, 10_000, "BT-LOW-CD", age_days=8),
    ]
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=168, frozen_buckets=2,
                       mixed_resolution_enabled=False,
                       horizon_minutes=10_080, seed=42,
                       max_solve_s=4.0)  # seven-day tactical grid, hourly jobs
    sc.weights = ObjectiveWeights()
    sc.assumptions = [
        Assumption("S3-A1", "Louta L02 product storage: total capacity "
                   "383,024 t is confirmed by the BoD. Its generic equipment "
                   "table lists ONE product reclaimer, while the later tagged "
                   "process description specifies 140LG-RC-01/-02 at "
                   "3,000 t/h nominal each; S0 uses the later tagged count. "
                   "The equal two-lane split (2 × 191,512 t), lane geometry "
                   "and RC-01→P1 / RC-02→P2 reach remain assumptions. The "
                   "integrated demonstration starts both product lanes at a "
                   "small 10,000 t operating buffer so C1 demand must still "
                   "use the washing/stacking chain; this "
                   "starting inventory is ASSUMED, not a live SMS snapshot",
                   "LOUTA", source="L02 design basis / handling philosophy "
                   "(doc refs TBC)", placeholder="LT-P1/P2 · 140LG-RC-01/-02",
                   impact="Louta contribution is design-basis indicative",
                   action="approved GA/reach matrix, as-built equipment list "
                   "and live inventory snapshot"),
        Assumption(
            "S3-A21",
            "Louta product piles contain multiple grade profiles in the "
            "demonstration: LT-P1 = 60% TBT + 40% BT and LT-P2 = 60% "
            "BT-LOW-CD + 40% MT-LOW-CD by mass. Aggregate StockSpace "
            "quality is the exact "
            "mass-weighted composite (LT-P1: BPL 65%, Cd 33 ppm, MgO "
            "1.28%, SiO2 3.22%, moisture 12%; LT-P2: BPL 71.9%, Cd 16.59 "
            "ppm, MgO 0.78%, SiO2 1.88%, moisture 12%). Washed vectors, "
            "profile-to-pile mapping and mass shares are ASSUMED/TBC. The "
            "distribution is schematic and non-spatial: no pile zones, "
            "longitudinal order, boundaries or reclaim-face sequence are "
            "asserted.",
            "LOUTA", status="ASSUMED",
            source="project-team multi-profile instruction 24-Jul-2026 + "
            "simulation-only washed grade vectors",
            confidence="L",
            placeholder="config.PILE_GRADE_DISTRIBUTIONS + aggregate "
            "StockSpace.quality; dominant grades LT-P1=TBT, "
            "LT-P2=BT-LOW-CD",
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
                   "BT + 50% High-Cd; 110LG-OL2 = 50% BT + 50% "
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
            "BT + 50% High-Cd raw BT; SP-02 displays 50% BT + 50% "
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
                   placeholder="BG-P1=TBT, BG-P2=BT-LOW-CD",
                   impact="mine blending fidelity",
                   action="confirm zones/campaign rule"),
        Assumption("S3-A16", "Mine/raw-screened and washed-product quality "
                   "profiles are separate model records. The shared raw-grade "
                   "catalogue contains BT, High-Cd and Low-Cd profiles; "
                   "their current chemistry values come from the Louta BoD. "
                   "Benguerir physical/moisture states remain from B02 and "
                   "live pile assays remain separate. Merchant family and "
                   "raw Cd lineage are modelled independently: the historic "
                   "TBT/BT/MT IDs retain BT compatibility, with explicit "
                   "High-Cd and Low-Cd washed variants. All washed vectors "
                   "remain simulation-only numeric placeholders.",
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
                   "maps the documented BT case (584 t/h, 68.22% recovery) "
                   "to L1/TBT and the Low-Cd case (588 t/h, 67.71% recovery) "
                   "to L2/BT-LOW-CD. The rates/recoveries are source facts; the "
                   "case-to-line, output-grade and campaign allocations are "
                   "project assumptions. Feed demand is exposed separately as "
                   "5,864 t BT for L1 and 5,908 t Low-Cd for L2: the exact "
                   "daily-yield inputs for the configured 4,000 t recovered "
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
            "that has not arrived by T=168 h remains visible as in-transit. "
            "MACHINE_COVERAGE_REWARD is zero: no job is generated merely to "
            "activate a row. The built-in weekly profile schedules one "
            "finite Louta wash campaign day: exactly 4,000 t recovered from "
            "each line and 8,000 t dispatched on C1, while restoring both "
            "ore lanes and both product lanes to opening inventory. Edited "
            "daily feed campaigns resynchronise these exact horizon targets. "
            "Every "
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
            "The selectable seven-day demand presets are ASSUMED demonstration "
            "inputs, not approved production or train orders. S0 uses 168 "
            "one-hour buckets grouped into independently editable daily "
            "grade/quantity campaigns. BALANCED runs MPH plus the finite "
            "Louta campaign; BG_TRAIN focuses on Benguerir washing and an "
            "84,000 t weekly train order; FULL_FLOW is the default and combines "
            "both mine chains, 168,000 t train demand, 8,000 t C1 and 74,200 t "
            "C2 allocation; BLEND_SHOWCASE adds the shared MPH matrix example. "
            "Parallel alternative machines are selected only when the "
            "optimized material plan needs them; no jobs are added only to "
            "inflate utilisation.",
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
            "Its seven saved daily campaigns match PAP-1's G10 receiving "
            "grade and total 252,000 t over the FULL_FLOW week, "
            "while the separate 1,500 t/h design template remains available "
            "for sensitivity runs. Future "
            "state contributes zero active demand and "
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
        Assumption(
            "S3-A31",
            "BLEND_SHOWCASE is an assumed software-demonstration preset, not "
            "an approved production plan. It reuses FULL_FLOW demand and makes "
            "the shared M16 chevron matrix catalogue available alongside the "
            "optimized-share policy. Inventory carries forward continuously. "
            "Linear perfect mixing, assays and "
            "homogenisation response remain unvalidated.",
            "MPH/M16 blending", status="ASSUMED", confidence="L",
            source="project-team showcase request 08-Sep-2026 + S0 model assays",
            placeholder="PLANNING_DEMAND_PROFILES[BLEND_SHOWCASE]; "
            "optimized pre-wash and M16 chevron policies",
            impact="blend quality, extra handling, specific electricity, "
            "positioning and machine conflicts",
            action="confirm physical blending point, approved recipe and "
            "parcel sizes, live assays, homogenisation/segregation "
            "performance, motor/load curves and meter baseline"),
        Assumption(
            "S3-A34",
            "Held-running state and its cost are ASSUMED. The planner may now "
            "keep a machine running through a bucket WITHOUT moving material, "
            "instead of commanding a token parcel to bridge the gap. This was "
            "added because the audited S0 week carried five 1 t bridge "
            "buckets in the middle of otherwise substantial campaigns, and "
            "banning one merely moved the trick elsewhere (5 to 19 "
            "below-floor parcels): the model had no truthful way to express "
            "'stay on, move nothing'. EQUIPMENT_HOLD_BUCKET_PENALTY = 8 is "
            "bounded by argument, not by measurement: it must be below a stop "
            "plus a later start (10 + 20) or holding would never be chosen, "
            "and above zero or the planner would hold everywhere for free. "
            "Held minutes are reported separately from active minutes and are "
            "never presented as production. Whether these plants actually "
            "prefer an idling drive to a stop/restart, and at what energy and "
            "wear cost, is unknown.",
            "planning/equipment cycling", status="ASSUMED", confidence="L",
            source="schedule-fragmentation correction 13-Sep-2026; no idling "
            "energy, drive-wear or restart-limit evidence in the workspace",
            placeholder="EQUIPMENT_HOLD_BUCKET_PENALTY = 8; hold offered "
            "only where CAMPAIGN_POLICY.max_hold_min > 0",
            impact="machine start/stop counts, held time, and whether a "
            "bridge bucket appears as production or as an idling drive",
            action="obtain the operating philosophy for holding versus "
            "stopping between parcels, the idling energy draw and the "
            "drive-thermal restart limit, then price the hold from those"),
        Assumption(
            "S3-A32",
            "Campaign / parcel / minimum-run policy is an ASSUMED "
            "demonstration smoothing rule, NOT a plant-approved operating "
            "limit. Without it the hourly activity constraint degenerates to "
            "one physical tonne for any machine whose rate_min_tph is zero \u2014 "
            "every stacker here \u2014 so the planner could pad a campaign with "
            "meaningless 1 t hours at no cost, and the minute overlay drew "
            "each as a 1-minute run. The installed defaults are a 250 t "
            "preferred parcel per bucket, a 20-minute minimum run, a "
            "30-minute maximum held (running, empty-belt) gap and no dwell "
            "constraint (1 bucket). Other presets allow and price an "
            "explicit below-floor exception. The operator-default blending "
            "showcase keeps the 250 t preference soft. Optional bounded "
            "parcel consolidation is disabled by default. Residuals may wait "
            "only in the existing modelled buffers and are batched without "
            "changing route/grade totals, receiving-unit service, terminal "
            "inventories or worsening the original objective. Process "
            "targets derived from forecasts may report shortfalls; explicit "
            "operator commitments stay hard. Minute executability and actual "
            "cycling/cost checks gate every accepted secondary candidate. "
            "A necessary small job remains feasible and visible.",
            "planning/equipment cycling", status="ASSUMED", confidence="L",
            source="schedule-fragmentation correction 13-Sep-2026; no "
            "equipment datasheet or operating-philosophy evidence in the "
            "workspace",
            placeholder="CAMPAIGN_POLICY default 250 t / 20 min / 30 min / "
            "1 bucket; allow_short_parcel true; MIN_JOB_VOLUME_T 0 (disabled). "
            "Service-first objectives use bounded 15 s wall / 4 s deterministic "
            "secondary search. Short-parcel penalty 4000 is independent of "
            "equipment weights. Consolidation off; derived targets soft.",
            impact="number of physical runs, machine starts/stops, "
            "changeovers, held time and (through them) demand service",
            action="obtain minimum stable belt/drive loading, permitted "
            "start frequency per hour, drive-thermal restart limits and the "
            "maximum period the plant accepts running an empty belt rather "
            "than stopping it; then approve per-machine values"),
        Assumption(
            "S3-A33",
            "Continuous-feed service share is an ASSUMED demonstration test, "
            "not an approved minimum stable feed rate. The previous rule "
            "called a demand-active hour served whenever at least ONE "
            "physical tonne arrived, which is also the integer activity floor "
            "of the mass model: a single token tonne per hour bought a "
            "zero-gap continuity KPI while the rest of the order went "
            "unserved. Continuous-feed destinations now additionally require "
            "50% of that hour's OWN requested tonnes. The threshold is "
            "demand-derived on purpose so it asserts no t/h figure, and it "
            "never exceeds the bucket's own integer model demand. Token "
            "deliveries are reported separately rather than hidden.",
            "planning/continuous feed", status="ASSUMED", confidence="L",
            source="schedule-fragmentation correction 13-Sep-2026; receiving "
            "plants have not issued a minimum stable feed rate",
            placeholder="CONTINUITY_POLICIES.minimum_service_fraction = 0.5",
            impact="reported feed-gap minutes and episodes, the continuity "
            "objective terms and therefore the chosen delivery profile",
            action="obtain each receiving plant's approved minimum stable "
            "feed rate and move it to CONTINUITY_POLICIES.minimum_tph"),
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
                   "demand is SCOPE_TBC. The restored presets range from zero "
                   "train demand to 84,000 t/week in BG_TRAIN and 168,000 "
                   "t/week in FULL_FLOW/BLEND_SHOWCASE. These are ASSUMED "
                   "planning orders, not source facts.",
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
            "BG-RAW-1 = 50% BT + 50% "
            "High-Cd (mass-weighted composite BPL 55.585 %, MgO 1.495 %, "
            "CO2 8.31 %, SiO2 14.995 %, Cd 23 ppm); BG-RAW-2 = 50% BT "
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
    apply_all_enabled_blending_defaults(sc)
    apply_planning_demand_profile(sc, DEFAULT_PLANNING_DEMAND_PROFILE)
    _install_operational_objective(
        sc, ["PAP-1", "PAP-2", "DRY-FEED"])
    return sc


def _daily_demand_entries(
        sc: Scenario, daily_quantities_t: Dict[str, float],
        requested_grades: Dict[str, str],
        daily_factors: Optional[Dict[str, Tuple[float, ...]]] = None,
        daily_grades: Optional[Dict[str, Tuple[str, ...]]] = None,
        ) -> List[DemandEntry]:
    """Expand daily plant campaigns onto the scenario's uniform grid.

    Each civil-day quantity is distributed as whole physical tonnes across
    that day's buckets, preserving the exact daily total on the hourly S0
    grid.  Partial-day compatibility fixtures receive the proportional share
    of the daily quantity.  The grade remains explicit even on zero-demand
    rows so the seven-day editor can stage a future campaign without changing
    the destination default.
    """
    daily_factors = daily_factors or {}
    daily_grades = daily_grades or {}
    dt_h = float(sc.sim.dt_h)
    horizon = int(sc.sim.horizon_buckets)
    day_buckets: Dict[int, List[int]] = {}
    first_date = sc.sim.bucket_dt(0).date()
    for bucket in range(horizon):
        day_index = (sc.sim.bucket_dt(bucket).date() - first_date).days
        day_buckets.setdefault(day_index, []).append(bucket)

    entries: List[DemandEntry] = []
    for destination in sc.destinations:
        daily_quantity = max(float(
            daily_quantities_t.get(destination.id, 0.0) or 0.0), 0.0)
        factors = tuple(daily_factors.get(destination.id, (1.0,) * 7)) \
            or (1.0,) * 7
        grades = tuple(daily_grades.get(destination.id, ()))
        default_grade = str(requested_grades.get(
            destination.id, destination.requested_grade) or "")
        destination.demand_default_tph = daily_quantity / 24.0
        destination.requested_grade = default_grade

        for day_index, buckets in sorted(day_buckets.items()):
            covered_h = min(len(buckets) * dt_h, 24.0)
            factor = max(float(factors[day_index % len(factors)]), 0.0)
            requested_t = int(round(
                daily_quantity * factor * covered_h / 24.0))
            base_t, extra_t = divmod(requested_t, len(buckets))
            grade = (
                str(grades[day_index % len(grades)] or "")
                if grades else default_grade)
            for offset, bucket in enumerate(buckets):
                mass_t = base_t + (1 if offset < extra_t else 0)
                entries.append(DemandEntry(
                    destination.id, bucket, mass_t / dt_h,
                    requested_grade=grade))
    return entries


def synchronize_weekly_demand_targets(
        sc: Scenario, *, c2_target_t: Optional[float] = None) -> None:
    """Synchronise exact process/corridor targets with current demand rows.

    This must be called after a manual weekly-demand edit.  It removes stale
    one-day demonstration targets, derives each process chain's horizon input
    and recovered-product output from the current grade/yield campaigns, and
    makes C1 equal Louta recovered output.  C2 is profile-only: omitting an
    explicit horizon target safely clears it for a custom weekly plan.
    """
    dt_h = float(sc.sim.dt_h)

    def demand_total(destination_id: str) -> float:
        return sum(
            float(sc.demand_tph(destination_id, bucket)) * dt_h
            for bucket in range(sc.sim.horizon_buckets))

    def recovered_total(destination: Destination) -> float:
        transfers = [
            transfer for transfer in sc.process_transfers
            if transfer.process_stage == destination.process_stage
        ]
        # Match optimizer.Y exactly.  Weekly S0 carries fractional yield over
        # one civil day per transfer/campaign, then floors to whole physical
        # tonnes at the day boundary.
        campaign_input: Dict[Tuple[int, str, str], int] = {}
        for bucket in range(sc.sim.horizon_buckets):
            mass_t = float(sc.demand_tph(destination.id, bucket)) * dt_h
            if mass_t <= 0:
                continue
            grade = sc.demand_grade(destination.id, bucket)
            transfer = next((
                item for item in transfers
                if not grade or sc.space(item.source_space).grade == grade
            ), None)
            if transfer is not None:
                day = (sc.sim.bucket_dt(bucket).date()
                       - sc.sim.bucket_dt(0).date()).days
                key = (day, grade, transfer.id)
                campaign_input[key] = campaign_input.get(key, 0) + int(
                    round(mass_t))
        total_t = 0
        by_id = {transfer.id: transfer for transfer in transfers}
        for (_day, _grade, transfer_id), input_t in campaign_input.items():
            factor = max(min(sc.transfer_output_factor(
                by_id[transfer_id]), 1.0), 0.0)
            coefficient = int(round(factor * 10_000))
            total_t += (input_t * coefficient) // 10_000
        return float(total_t)

    targets = dict(sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}) or {})
    daily_bucket_count = max(int(round(24.0 / dt_h)), 1)
    sc.config["PROCESS_YIELD_CARRY_WINDOW_BUCKETS"] = daily_bucket_count
    sc.config["PROCESS_ZERO_CARRY_WINDOW_BUCKETS"] = daily_bucket_count
    louta_outputs: Dict[str, float] = {}
    any_bg = False
    any_louta = False
    for destination in sc.destinations:
        if getattr(destination, "demand_kind", "delivery") != "process_feed":
            continue
        feed_t = demand_total(destination.id)
        output_t = recovered_total(destination)
        if destination.id in _BEN_PROCESS_STAGE_IDS:
            stages = _BEN_PROCESS_STAGE_IDS[destination.id]
            for stage in stages[:3]:
                targets[stage] = feed_t
            targets[stages[3]] = output_t
            targets[stages[4]] = output_t
            any_bg = any_bg or feed_t > 0
        elif destination.id in ("LOU-WASH-L1", "LOU-WASH-L2"):
            targets[destination.process_stage] = output_t
            louta_outputs[destination.process_stage] = output_t
            any_louta = any_louta or feed_t > 0

    for stage in ("LOU-WASH-L1", "LOU-WASH-L2"):
        louta_outputs.setdefault(stage, 0.0)
        targets.setdefault(stage, 0.0)
    sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = targets
    sc.config["LOU_WASH_LINE_OUTPUT_TARGET_T"] = louta_outputs

    louta_enabled = bool(sc.config.get(
        "LOU_S0_STOCKED_ROUTE_ENABLED", any_louta)) and any_louta
    c1_target = sum(louta_outputs.values()) if louta_enabled else 0.0
    corridor_targets = dict(
        sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {}) or {})
    corridor_targets["C1"] = c1_target
    corridor_targets["C2"] = max(float(c2_target_t or 0.0), 0.0)
    sc.config["CORRIDOR_DISPATCH_TARGET_T"] = corridor_targets
    sc.config["LOU_S0_STOCKED_ROUTE_ENABLED"] = louta_enabled
    sc.config["LOU_C1_STOCKED_ROUTE_TARGET_T"] = c1_target
    sc.config["BEN_S0_PROCESS_ACTIVE"] = any_bg
    sc.config["LOU_S0_PROCESS_ACTIVE"] = any_louta


def apply_planning_demand_profile(
        sc: Scenario, profile_id: str) -> Dict[str, object]:
    """Apply one downstream-driven seven-day demand bundle.

    MPH final-unit orders are the primary driver. The preset's explicit
    same-week replenishment portion is split between C1/Louta and
    C2/Benguerir; opening M16 stock covers the balance. Benguerir's independent
    train order is additive. Each mine production call-off is converted to raw
    physical feed with the configured beneficiation yields.
    """
    profile_id = _LEGACY_PLANNING_DEMAND_PROFILE_ALIASES.get(
        profile_id, profile_id)
    try:
        profile = PLANNING_DEMAND_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown planning demand profile: {profile_id}") from exc
    horizon_h = sc.sim.horizon_buckets * sc.sim.dt_h
    if horizon_h <= 0:
        raise ValueError("planning horizon must be positive")
    horizon_days = horizon_h / 24.0

    restored_profile = bool(profile.get("legacy_demand_model", False))
    # PAP rock demand rescaled to plant duty (project team, 28-Sep-2026):
    # one PAP line makes 2,800 t of acid from 5,300 t of G10 rock, which is
    # ~38,500 t/week per line -> 5,500 t/day. The previous 36,000 t/day
    # (252,000 t/week per line) was ~6.5x plant duty and was the dominant
    # reason the yard could never be served: total mine production across
    # both sites is ~24,200 t/week, so PAP alone out-asked the entire supply
    # chain tenfold and the week ran on opening stock.
    # DRY-FEED rescaled to duty (project team, 28-Sep-2026): 25,600 t/week of
    # G10 plus 3,500 t/week spread over the three BG export precursors, i.e.
    # 29,100 t/week = 4,157 t/day against the day factors below. The previous
    # 19,200 t/day (134,400 t/week) asked for 75,840 t/week of G10 alone -
    # almost twice PAP's entire duty - so drying out-competed the acid plant
    # for the same certified rock.
    mph_daily_t = (
        {"PAP-1": 5_500.0, "PAP-2": 5_500.0, "DRY-FEED": 4_157.0}
        if restored_profile else {
            destination_id: max(float(quantity_t or 0.0), 0.0)
            for destination_id, quantity_t in dict(
                profile.get("mph_daily_t", {}) or {}).items()
        }
    )
    c2_share = float(profile.get("c2_share", 0.40))
    replenishment_share = float(profile.get(
        "corridor_replenishment_share", 1.0))
    bg_production_share = float(profile.get("bg_production_share", 1.0))
    if not 0.0 <= c2_share <= 1.0:
        raise ValueError("c2_share must be between 0 and 1")
    if not 0.0 <= replenishment_share <= 1.0:
        raise ValueError("corridor_replenishment_share must be between 0 and 1")
    if not 0.0 <= bg_production_share <= 1.0:
        raise ValueError("bg_production_share must be between 0 and 1")
    active_mph_daily_t = sum(
        mph_daily_t.get(destination_id, 0.0)
        for destination_id in ("PAP-1", "PAP-2", "DRY-FEED")
        if destination_line_operating_state(sc, destination_id) != "FUTURE"
    )
    active_mph_horizon_t = float(round(active_mph_daily_t * horizon_days))
    train_daily_t = max(float(profile["bg_train_product_t"]), 0.0)

    def horizon_factor_total(destination_id: str) -> float:
        factors = tuple(_WEEKLY_DEMAND_FACTORS.get(
            destination_id, (1.0,) * 7)) or (1.0,) * 7
        whole_days = int(horizon_days)
        total = sum(float(factors[day % len(factors)])
                    for day in range(whole_days))
        partial = horizon_days - whole_days
        if partial > 1e-9:
            total += partial * float(factors[whole_days % len(factors)])
        return max(total, 1e-9)

    def feed_for_output(output_t: float, output_factor: float) -> float:
        """Smallest whole raw tonne whose integer yield covers output_t."""
        target_t = max(int(round(output_t)), 0)
        coefficient = int(round(max(min(output_factor, 1.0), 0.0) * 10_000))
        if target_t and coefficient <= 0:
            raise ValueError("beneficiation output factor must be positive")
        if not target_t:
            return 0.0
        return float((target_t * 10_000 + coefficient - 1) // coefficient)

    if restored_profile:
        louta_process_active = bool(
            profile.get("louta_process_active", True))
        bg_process_active = bool(profile.get("bg_process_active", False))
        louta_daily_feed_targets = {
            line_id: (target if louta_process_active else 0.0)
            for line_id, target in _LOUTA_S0_LINE_FEED_TARGET_T.items()
        }
        bg_daily_feed_targets = {
            line_id: (float(case["feed_t"]) if bg_process_active else 0.0)
            for line_id, case in _BEN_WASH_CASES.items()
        }
        # ``c1_dispatch_t``/``c2_dispatch_t`` are DAILY corridor call-offs -
        # quantities requested, not rate limits (corridor capacity, 2,600 t/h
        # shared, is modelled separately).
        #
        # They set ``corridor_replenishment_t`` and the C1/C2 split, and the
        # C2 target directly. C1 is NOT taken from here in the end:
        # ``synchronize_weekly_demand_targets`` overwrites the C1 corridor
        # target with Louta's actual recovered washing output, because a
        # corridor cannot dispatch more than its mine produced. So raising
        # ``c1_dispatch_t`` alone does not raise C1 traffic - Louta's campaign
        # calendar (currently one day, 2 lines x 4,000 t = 8,000 t/week) is
        # the real lever, and ``LOU_S0_STOCKED_ROUTE_ENABLED`` guards the
        # invariant that the two stay equal.
        c1_horizon_output_t = (
            max(float(profile.get("c1_dispatch_t", 0.0)), 0.0) * horizon_days
            if louta_process_active else 0.0)
        c2_horizon_output_t = max(
            float(profile.get("c2_dispatch_t", 0.0)), 0.0) * horizon_days
        corridor_replenishment_t = (
            c1_horizon_output_t + c2_horizon_output_t)
        bg_horizon_output_t = sum(
            float(case["output_t"]) * horizon_factor_total(line_id)
            for line_id, case in _BEN_WASH_CASES.items()
            if bg_process_active)
        c2_share = (
            c2_horizon_output_t / corridor_replenishment_t
            if corridor_replenishment_t else 0.0)
        replenishment_share = (
            corridor_replenishment_t / active_mph_horizon_t
            if active_mph_horizon_t else 0.0)
    else:
        corridor_replenishment_t = float(round(
            active_mph_horizon_t * replenishment_share))
        c2_horizon_output_t = float(round(
            corridor_replenishment_t * c2_share))
        c1_horizon_output_t = corridor_replenishment_t - c2_horizon_output_t
        louta_daily_feed_targets = {
            "LOU-WASH-L1": feed_for_output(
                c1_horizon_output_t / 2.0
                / horizon_factor_total("LOU-WASH-L1"),
                _LOUTA_WASH_CASES["BT"]["recovery"]),
            "LOU-WASH-L2": feed_for_output(
                c1_horizon_output_t / 2.0
                / horizon_factor_total("LOU-WASH-L2"),
                _LOUTA_WASH_CASES["LOW_CD"]["recovery"]),
        }
        bg_outbound_duty_t = (
            c2_horizon_output_t + train_daily_t * horizon_days)
        bg_horizon_output_t = float(round(
            bg_outbound_duty_t * bg_production_share))
        bg_daily_feed_targets = {
            line_id: feed_for_output(
                bg_horizon_output_t / len(_BEN_WASH_CASES)
                / horizon_factor_total(line_id),
                float(case["recovery"]))
            for line_id, case in _BEN_WASH_CASES.items()
        }
    daily_quantities_t = {
        "PAP-1": mph_daily_t.get("PAP-1", 0.0),
        "PAP-2": mph_daily_t.get("PAP-2", 0.0),
        "DRY-FEED": mph_daily_t.get("DRY-FEED", 0.0),
        "BG-TRAIN-PROD": train_daily_t,
        "BG-TRAIN-RAW": 0.0,
        "LOU-WASH-L1": louta_daily_feed_targets["LOU-WASH-L1"],
        "LOU-WASH-L2": louta_daily_feed_targets["LOU-WASH-L2"],
    }
    daily_quantities_t.update({
        line_id: bg_daily_feed_targets[line_id]
        for line_id in _BEN_WASH_CASES
    })
    requested_grades = {
        "PAP-1": "G10",
        "PAP-2": "G10",
        "DRY-FEED": "TBT",
        "BG-TRAIN-PROD": "TBT",
        "BG-TRAIN-RAW": "RAW-BT",
        "LOU-WASH-L1": "RAW-BT",
        "LOU-WASH-L2": "RAW-BT-LOW-CD",
    }
    requested_grades.update({
        line_id: str(case["feed_grade"])
        for line_id, case in _BEN_WASH_CASES.items()
    })
    # A profile is a complete demand bundle, so no per-bucket row from a
    # previous profile may leak into the weekly campaign.
    daily_factors = deepcopy(_WEEKLY_DEMAND_FACTORS)
    daily_factors.update(profile.get("daily_quantity_factors", {}) or {})
    daily_grades = deepcopy(_WEEKLY_DEMAND_GRADES)
    daily_grades.update(profile.get("daily_grades", {}) or {})
    sc.demand = _daily_demand_entries(
        sc, daily_quantities_t, requested_grades,
        daily_factors=daily_factors,
        daily_grades=daily_grades)
    # Presets may alter quantities and timing, but PAP's receiving contract
    # is invariant: every PAP bucket requests the G10 specification.
    from .washing import synchronize_pap
    synchronize_pap(sc)
    c2_horizon_target = c2_horizon_output_t
    sc.config["LOU_S0_STOCKED_ROUTE_ENABLED"] = c1_horizon_output_t > 0
    synchronize_weekly_demand_targets(
        sc, c2_target_t=c2_horizon_target)
    actual_c1_t = float(dict(sc.config.get(
        "CORRIDOR_DISPATCH_TARGET_T", {}) or {}).get("C1", 0.0))
    sc.config["DEMAND_DRIVER_SUMMARY"] = {
        "basis": (
            "Restored explicit demonstration campaigns"
            if restored_profile else
            "MPH final demand drives same-week C1/C2 replenishment; opening "
            "stock covers the balance and train is additive at Benguerir"),
        "active_mph_demand_t": active_mph_horizon_t,
        "corridor_replenishment_t": corridor_replenishment_t,
        "c1_louta_product_t": actual_c1_t,
        "c2_benguerir_product_t": c2_horizon_target,
        "bg_train_product_t": train_daily_t * horizon_days,
        "c2_share": c2_share,
        "corridor_replenishment_share": replenishment_share,
        "bg_same_week_production_t": bg_horizon_output_t,
        "bg_production_share": bg_production_share,
    }
    sc.config["MPH_C2_SUPPLY_SHARE"] = c2_share
    sc.config["ACTIVE_PLANNING_DEMAND_PROFILE"] = profile_id
    # Batch residuals within the EXISTING physical buffers, not by imposing
    # the assumed 250 t preference as a hard command bound or relaxing targets.
    # The primary model retains priced short exceptions. A bounded secondary
    # pass fixes service/quantities and can only improve short-command count
    # without worsening the original objective. Necessary tiny jobs remain
    # representable and are still reported, including if polishing times out.
    campaign = deepcopy(sc.config.get(CAMPAIGN_POLICY_KEY, {}) or {})
    campaign_default = dict(campaign.get("default", {}) or {})
    campaign_default["allow_short_parcel"] = True
    campaign["default"] = campaign_default
    sc.config[CAMPAIGN_POLICY_KEY] = campaign
    sc.config["PROCESS_STAGE_TARGET_SHORTFALL_MAX_T"] = {}
    sc.config["PROCESS_STAGE_TARGET_SHORTFALL_BASIS"] = ""
    sc.config["PARCEL_CONSOLIDATION_ENABLED"] = False
    # Provenance matters: these were derived from forecast orders, not issued
    # as operator-fixed production instructions. Keep explicit targets exact.
    sc.config["PLANNING_PROCESS_STAGE_TARGETS"] = list(
        sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}))
    sc.config["PLANNING_CORRIDOR_TARGETS"] = list(
        sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {}))
    sc.config["PARCEL_CONSOLIDATION_WALL_TIME_S"] = 15.0
    sc.config["PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S"] = 4.0
    sc.config["PARCEL_CONSOLIDATION_BASIS"] = (
        "ASSUMED demonstration scheduling preference, NOT a plant-approved "
        "minimum lot. Bounded secondary scheduling minimises below-preference "
        "command buckets at fixed route/grade quantities, service and terminal "
        "inventories, while retaining all physical constraints and protecting "
        "the original objective. Short jobs remain possible if required; a "
        "failed or unhelpful polish retains the primary incumbent.")
    # The chevron campaign adds enough binary scheduling decisions that the
    # ordinary 12 s/day limit can expire before day 1 has an incumbent. Keep
    # the faster budget for other presets; loading a preset resets its budget.
    weekly_total_wall_s, weekly_window_wall_s = (
        (385.0, 55.0) if bool(profile.get("blend_showcase", False))
        else (280.0, 40.0))
    sc.config["WEEKLY_TOTAL_WALL_TIME_S"] = weekly_total_wall_s
    sc.config["WEEKLY_WINDOW_WALL_TIME_S"] = weekly_window_wall_s
    sc.config["CP_SAT_WORKERS"] = 8
    sc.config["SERVICE_FIRST_INCLUDES_PROCESS_FEED"] = bool(
        profile.get("service_first_includes_process_feed", False))
    sc.config.pop("PROCESS_FEED_MINIMUM_CAMPAIGN_T", None)
    sc.config["PLANNING_DEMAND_PROFILE_BASIS"] = (
        "ASSUMED restored demonstration campaigns, not approved production "
        "or train orders. Final-unit and washing campaigns use the explicit "
        "seven-day preset calendar retained from the prior simulator version."
        if restored_profile else
        "ASSUMED daily demonstration orders, not approved production or train "
        "orders. Active MPH demand drives an explicit same-week replenishment "
        "share split between C1/Louta and C2/Benguerir; opening product stock "
        "covers the remaining deliveries. Benguerir train demand is then "
        "added. Raw washing feed is derived from the scheduled mine-production "
        "portion using the configured physical beneficiation factors.")
    prewash_modes = dict(profile.get("prewash_treatment_modes", {}) or {})
    prewash_cfg = sc.config.get("PRE_WASHING_BLENDING")
    if prewash_modes and isinstance(prewash_cfg, dict):
        points = prewash_cfg.get("points", {})
        if isinstance(points, dict):
            for point_id, modes in prewash_modes.items():
                point = points.get(point_id)
                if isinstance(point, dict):
                    point["allowed_treatment_modes"] = list(modes)
        sc.config["INTEGRATED_PREWASH_TREATMENT_POLICY"] = deepcopy(
            prewash_modes)
    if bool(profile.get("operating_stock_floors", False)):
        # These are week-end operating floors, not daily equality targets.
        # They prevent the demonstration from serving orders by simply
        # draining opening intermediate/product stock while spilling the
        # configured mine arrivals. Existing operator minima are retained.
        floor_ids = (
            "110LG-OL1", "110LG-OL2", "LT-P1", "LT-P2",
            "BG-MINE-1", "BG-MINE-2", "BG-RAW-1", "BG-RAW-2",
            "BG-P1", "BG-P2",
        )
        minima = dict(sc.config.get("TERMINAL_INVENTORY_MIN_T", {}) or {})
        installed = {}
        for space_id in floor_ids:
            try:
                floor_t = float(sc.space(space_id).mass_t)
            except StopIteration:
                continue
            minima.setdefault(space_id, floor_t)
            installed[space_id] = minima[space_id]
        sc.config["TERMINAL_INVENTORY_MIN_T"] = minima
        sc.config["INTEGRATED_OPERATING_STOCK_FLOORS_T"] = installed
        sc.config["FINAL_ONLY_TERMINAL_INVENTORY_MIN_SPACES"] = list(
            installed)
        # Soft: restocking is a purpose pursued with spare capacity, not a
        # promise that outranks delivered demand. Hard floors here make a
        # supply-limited week INFEASIBLE instead of trading off.
        sc.config["TERMINAL_INVENTORY_MIN_SOFT_SPACES"] = list(installed)
        sc.config["INTEGRATED_OPERATING_STOCK_FLOOR_BASIS"] = (
            "ASSUMED weekly operating policy: each listed intermediate or "
            "product pile may vary during the week but must finish at or "
            "above its planning-start inventory. This links mine arrivals "
            "and washing to outbound demand without imposing the rejected "
            "exact daily stock reset.")
    else:
        owned_floors = dict(sc.config.get(
            "INTEGRATED_OPERATING_STOCK_FLOORS_T", {}) or {})
        minima = dict(sc.config.get("TERMINAL_INVENTORY_MIN_T", {}) or {})
        for space_id, value in owned_floors.items():
            if minima.get(space_id) == value:
                minima.pop(space_id, None)
        sc.config["TERMINAL_INVENTORY_MIN_T"] = minima
        sc.config.pop("INTEGRATED_OPERATING_STOCK_FLOORS_T", None)
        sc.config.pop("INTEGRATED_OPERATING_STOCK_FLOOR_BASIS", None)
        sc.config.pop("FINAL_ONLY_TERMINAL_INVENTORY_MIN_SPACES", None)
    if bool(profile.get("blend_showcase", False)):
        # A deliberately explicit, reproducible demonstration matrix.  With
        # the S0 assays it yields BPL 68.85 and Cd 20.213, which satisfies the
        # DRY-FEED BT limits.  This is a software showcase assumption, not an
        # approved plant recipe; operators can adjust it on the Blending page.
        policy = deepcopy(sc.config.get("CHEVRON_STACKING", {}) or {})
        # Install the showcase's example only when the first row is still the
        # untouched built-in template. A demand-preset change is not authority
        # to discard operator recipes or an already constructed physical lot.
        catalog = deepcopy(policy.get("matrix_catalog", []))
        if catalog and catalog[0] == _default_chevron_stacking_config()["matrix_catalog"][0]:
            catalog[0] = {
                "id": "MPH-HUMID-01",
                "name": "BT dry-feed demonstration matrix",
                "components": [
                    {"grade_id": "TBT", "target_fraction": 0.10},
                    {"grade_id": "BT-LOW-CD", "target_fraction": 0.90},
                ],
            }
        policy.update({
            # Demand presets must not reset the operator's opt-ins or shared
            # selection mode. Fresh policies already allow every stockpile
            # and use optimized shares; the example remains a catalogue row.
            "enabled": bool(policy.get("enabled_spaces", [])),
            "enabled_spaces": list(policy.get("enabled_spaces", [])),
            "model_version": 7,
            "example_catalog_revision": 2,
            "quantity_mode": "DEMAND_DRIVEN",
            "min_input_grade_t": 500.0,
            "global_mode": policy.get("global_mode", "OPTIMIZED"),
            "matrix_catalog": catalog,
        })
        for legacy_key in (
                "mode_by_space", "recipes", "result_grades",
                "batch_t_by_space", "ready_minute_by_space",
                "minimum_component_fraction_by_space", "global_result_grade",
                "global_batch_t", "global_ready_minute", "passes_per_cycle",
                "global_minimum_component_fraction"):
            policy.pop(legacy_key, None)
        sc.config["CHEVRON_STACKING"] = policy
        sc.config["BLENDING_SHOWCASE"] = {
            "space_id": "HP-07",
            "location": "MPH M16 · humid-phosphate stockpile HP-07",
            "purpose": "Blend for downstream demand using the shared policy",
            "recipe": "TBT 10% + BT-LOW-CD 90%",
            "quality_reference": "BPL 68.85 · Cd 20.213",
            "validation_scope": (
                "Assumed S0 assays and linear mass-weighted mixing; "
                "not a commissioned plant recipe"
            ),
        }
    else:
        sc.config.pop("BLENDING_SHOWCASE", None)
    sc.config.pop("BLENDING_SHOWCASE_PREVIOUS_POLICY", None)
    from .washing import synchronize_pap, reconcile_derived_policy
    synchronize_pap(sc)
    reconcile_derived_policy(sc)
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
                 transfer.yield_factor,
                 getattr(transfer, "mass_conversion", ""),
                 getattr(transfer, "dry_feed_capacity_tph", 0.0),
                 transfer.residence_min,
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
                 transfer.yield_factor,
                 getattr(transfer, "mass_conversion", ""),
                 getattr(transfer, "dry_feed_capacity_tph", 0.0),
                 transfer.residence_min,
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


def _migrate_chevron_policy_v5(sc: Scenario) -> bool:
    """Add the two blend-policy modes without replacing operator matrices.

    Version 4 already has the physically meaningful campaign state, batch and
    recipe data.  Version 5 only adds policy metadata around those values, so
    this migration is intentionally additive.  In particular it must not copy
    the fresh built-in policy over a matrix edited in the running application.
    """
    raw = sc.config.get("CHEVRON_STACKING", {})
    if not isinstance(raw, dict):
        return False
    try:
        version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version < 4 or version >= 5:
        return False

    upgraded = deepcopy(raw)
    recipes = upgraded.get("recipes", {})
    recipes = recipes if isinstance(recipes, dict) else {}

    modes = upgraded.get("mode_by_space", {})
    modes = dict(modes) if isinstance(modes, dict) else {}
    for space_id in recipes:
        modes.setdefault(str(space_id), "MATRIX")
    upgraded["mode_by_space"] = modes

    bounds_by_space = upgraded.get("share_bounds_by_space", {})
    bounds_by_space = (dict(bounds_by_space)
                       if isinstance(bounds_by_space, dict) else {})
    for space_id, components in recipes.items():
        if str(space_id) in bounds_by_space or not isinstance(
                components, (list, tuple)):
            continue
        grade_ids = [
            str(component.get("grade_id", "") or "").strip()
            for component in components if isinstance(component, dict)
        ]
        grade_ids = list(dict.fromkeys(
            grade_id for grade_id in grade_ids if grade_id))
        if len(grade_ids) >= 2:
            bounds_by_space[str(space_id)] = [
                {"grade_id": grade_id, "min_fraction": 0.05,
                 "max_fraction": 0.95}
                for grade_id in grade_ids
            ]
    upgraded["share_bounds_by_space"] = bounds_by_space

    minimum_components = upgraded.get(
        "minimum_component_fraction_by_space", {})
    minimum_components = (
        dict(minimum_components)
        if isinstance(minimum_components, dict) else {})
    for space_id in modes:
        minimum_components.setdefault(str(space_id), 0.05)
    upgraded["minimum_component_fraction_by_space"] = minimum_components

    proposals = upgraded.get("proposed_recipes_by_space", {})
    upgraded["proposed_recipes_by_space"] = (
        dict(proposals) if isinstance(proposals, dict) else {})
    upgraded["model_version"] = 5
    sc.config["CHEVRON_STACKING"] = upgraded
    return True


def _migrate_chevron_policy_v6(sc: Scenario) -> bool:
    """Collapse per-pile policy copies into one reusable MPH catalogue.

    Enabled stockpiles and physical campaign states remain independent.  No
    configured matrix is assigned to a stockpile; distinct legacy recipes are
    retained as alternative catalogue entries available to every new pile.
    """
    raw = sc.config.get("CHEVRON_STACKING", {})
    if not isinstance(raw, dict) or not raw:
        return False
    try:
        version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= 6:
        return False

    upgraded = deepcopy(raw)
    enabled_spaces = [str(value) for value in raw.get(
        "enabled_spaces", []) if str(value)]
    preferred = enabled_spaces[0] if enabled_spaces else ""

    def first_value(mapping, default):
        mapping = mapping if isinstance(mapping, dict) else {}
        if preferred and preferred in mapping:
            return deepcopy(mapping[preferred])
        return deepcopy(next(iter(mapping.values()), default))

    recipes = raw.get("recipes", {})
    recipes = recipes if isinstance(recipes, dict) else {}
    catalog = []
    signatures = set()
    for space_id, rows in recipes.items():
        if not isinstance(rows, (list, tuple)):
            continue
        cleaned = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            grade_id = str(row.get("grade_id", "") or "").strip()
            try:
                share = float(row.get(
                    "target_fraction", row.get("mass_fraction", 0)) or 0)
            except (TypeError, ValueError):
                continue
            if grade_id and share > 0:
                cleaned.append({
                    "grade_id": grade_id, "target_fraction": share})
        signature = tuple(sorted(
            (row["grade_id"], round(row["target_fraction"], 10))
            for row in cleaned))
        if len(signature) < 2 or signature in signatures:
            continue
        signatures.add(signature)
        catalog.append({
            "id": f"MPH-HUMID-{len(catalog) + 1:02d}",
            "name": f"Migrated matrix ({space_id})",
            "components": cleaned,
        })

    # Freeze each physically started v5 campaign before collapsing policy
    # maps; such piles may legitimately have different historical contracts.
    states = upgraded.get("campaign_states_by_space", {})
    states = states if isinstance(states, dict) else {}
    for space_id, state in states.items():
        if not isinstance(state, dict) or state.get("status") not in {
                "BUILDING", "COMPLETE", "DEPLETED"}:
            continue
        state.setdefault("batch_t", raw.get("batch_t_by_space", {}).get(space_id, 0))
        state.setdefault("result_grade", raw.get("result_grades", {}).get(space_id, ""))
        state.setdefault("ready_minute", raw.get("ready_minute_by_space", {}).get(space_id, 0))
        if not state.get("resolved_recipe") and space_id in recipes:
            state["resolved_recipe"] = deepcopy(recipes[space_id])

    modes = raw.get("mode_by_space", {})
    mode = str(first_value(modes, "MATRIX") or "MATRIX").upper()
    if mode not in {"MATRIX", "OPTIMIZED"}:
        mode = "MATRIX"
    upgraded.update({
        "model_version": 6,
        "global_mode": mode,
        "matrix_catalog": catalog,
        "optimized_share_bounds": first_value(
            raw.get("share_bounds_by_space", {}), []),
        "global_minimum_component_fraction": float(first_value(
            raw.get("minimum_component_fraction_by_space", {}), 0.05)
            or 0.05),
        "global_result_grade": str(first_value(
            raw.get("result_grades", {}), "") or ""),
        "global_batch_t": first_value(
            raw.get("batch_t_by_space", {}), 0),
        "global_ready_minute": first_value(
            raw.get("ready_minute_by_space", {}), 0),
        "proposed_matrix_by_space": {},
        "enabled_spaces": enabled_spaces,
        "enabled": bool(enabled_spaces),
    })
    for key in (
            "mode_by_space", "recipes", "share_bounds_by_space",
            "minimum_component_fraction_by_space", "result_grades",
            "batch_t_by_space", "ready_minute_by_space",
            "ready_bucket_by_space"):
        upgraded.pop(key, None)
    sc.config["CHEVRON_STACKING"] = upgraded
    return True


def _migrate_chevron_policy_v7(sc: Scenario) -> bool:
    """Remove fixed campaign inputs while retaining already stacked material.

    Legacy imported scenarios remain readable until explicitly migrated. Hot
    built-in scenarios adopt demand-driven planning; only a physical campaign
    keeps the recipe and quantity selected before its first accepted stacking.
    """
    raw = sc.config.get("CHEVRON_STACKING", {})
    if not isinstance(raw, dict) or not raw:
        return False
    try:
        version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= 7:
        return False
    from .blending import (chevron_settings, chevron_batch_allocation,
                           chevron_construction_batch_t, chevron_result_grade,
                           chevron_ready_minute)
    cfg = chevron_settings(sc)
    upgraded = deepcopy(raw)
    states = deepcopy(cfg["campaign_states_by_space"])
    bounds = upgraded.get("optimized_share_bounds", [])
    inherited_default_bounds = (
        len(bounds) == 2 and
        {row.get("grade_id") for row in bounds if isinstance(row, dict)} == {"TBT", "BT-LOW-CD"}
        and all(isinstance(row, dict) and row.get("min_fraction") == 0.05
                and row.get("max_fraction") == 0.95 for row in bounds))
    if inherited_default_bounds:
        upgraded["optimized_share_bounds"] = [
            {"grade_id": row["grade_id"], "min_fraction": 0.0, "max_fraction": 1.0}
            for row in bounds]
    for space_id, state in states.items():
        has_physical_material = (state.get("status") in {"BUILDING", "COMPLETE", "DEPLETED"}
                                 or sum(state.get("stacked_t_by_grade", {}).values()) > 0)
        if not has_physical_material:
            continue
        if not state.get("resolved_recipe"):
            state["resolved_recipe"] = chevron_batch_allocation(sc, space_id)
        state["batch_t"] = state.get("batch_t") or chevron_construction_batch_t(sc, space_id)
        state["result_grade"] = state.get("result_grade") or chevron_result_grade(sc, space_id)
        state["ready_minute"] = state.get("ready_minute") or chevron_ready_minute(sc, space_id)
    upgraded.update({
        "model_version": 7,
        "quantity_mode": "DEMAND_DRIVEN",
        "min_input_grade_t": raw.get("min_input_grade_t", 500.0),
        "stacking_traverse_speed_m_min": raw.get("stacking_traverse_speed_m_min"),
        "campaign_states_by_space": states,
        "proposed_campaigns_by_space": {},
        "proposed_recipes_by_space": {},
        "proposed_matrix_by_space": {},
    })
    for key in ("global_result_grade", "global_batch_t", "global_ready_minute",
                "passes_per_cycle", "global_minimum_component_fraction",
                "result_grades", "batch_t_by_space", "ready_minute_by_space",
                "ready_bucket_by_space", "minimum_component_fraction_by_space"):
        upgraded.pop(key, None)
    sc.config["CHEVRON_STACKING"] = upgraded
    return True


def _install_additional_mph_matrix(sc: Scenario) -> bool:
    """Add the new example once without replacing operator recipes or state."""
    if sc.name not in (S3_SCENARIO_NAME, S2_SCENARIO_NAME):
        return False
    raw = sc.config.get("CHEVRON_STACKING")
    if not isinstance(raw, dict) or raw.get("example_catalog_revision") == 2:
        return False
    catalog = raw.get("matrix_catalog")
    if not isinstance(catalog, list):
        return False
    entry = _alternative_mph_blend_matrix()
    if not all(sc.grade(row["grade_id"]) is not None for row in entry["components"]):
        return False
    updated = deepcopy(raw)
    added = not any(item.get("id") == entry["id"] for item in catalog)
    if added:
        updated["matrix_catalog"].append(entry)
    updated["example_catalog_revision"] = 2
    sc.config["CHEVRON_STACKING"] = updated
    return added


def _install_missing_prewash_baseline(
        sc: Scenario, reference: Scenario) -> bool:
    """Install v37 pre-washing inputs without replacing operator edits."""
    changed = False
    if not isinstance(sc.config.get("PRE_WASHING_BLENDING"), dict):
        sc.config["PRE_WASHING_BLENDING"] = deepcopy(
            reference.config["PRE_WASHING_BLENDING"])
        changed = True
    expected_ids = {
        transfer.id for transfer in reference.process_transfers
        if transfer.id.endswith("BLEND-ALT")
    }
    present_ids = {transfer.id for transfer in sc.process_transfers}
    missing_ids = expected_ids - present_ids
    if missing_ids:
        sc.process_transfers.extend(
            deepcopy(transfer) for transfer in reference.process_transfers
            if transfer.id in missing_ids)
        changed = True
    return changed


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
    train_export_grades_changed = synchronize_train_export_grades(sc)
    from .washing import synchronize_pap
    pap_g10_changed = synchronize_pap(sc)
    if (sc.name == S3_SCENARIO_NAME and sc.config.get(
            "BUILTIN_BASELINE_REVISION") in {
                "2026-09-22-integrated-weekly-demand-v41",
                "2026-09-22-integrated-weekly-demand-v42",
                "2026-09-22-integrated-weekly-demand-v43",
            }):
        # v44 restores the earlier multi-preset demand catalogue and makes
        # FULL_FLOW the visible default again. Re-applying the preset does not
        # touch operator inventories or blending enablement.
        apply_planning_demand_profile(sc, DEFAULT_PLANNING_DEMAND_PROFILE)
        sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
        return "solver"
    if (sc.name == S3_SCENARIO_NAME and sc.config.get(
            "BUILTIN_BASELINE_REVISION") ==
            "2026-09-22-remove-duplicate-raw-bt-v40"):
        # v41 deliberately replaces the former multi-preset showcase. Keep a
        # small operating buffer in each product pile so the first rolling
        # window can be seeded, but make it far smaller than weekly demand so
        # the integrated exercise still requires washing and stacking. All
        # capacities, assays, mine/raw inventories and saved blending
        # enablement remain untouched.
        reference = end_to_end()
        reference_spaces = {
            space.id: space for space in reference.spaces
            if space.id in {"LT-P1", "LT-P2", "BG-P1", "BG-P2"}
        }
        for space_id in ("LT-P1", "LT-P2", "BG-P1", "BG-P2"):
            try:
                space = sc.space(space_id)
            except StopIteration:
                continue
            source = reference_spaces.get(space_id)
            if source is not None:
                space.mass_t = source.mass_t
                space.state = source.state
        sc.segments = [
            segment for segment in sc.segments
            if segment.space_id not in {"LT-P1", "LT-P2", "BG-P1", "BG-P2"}
        ]
        sc.segments.extend(
            deepcopy(segment) for segment in reference.segments
            if segment.space_id in {"LT-P1", "LT-P2", "BG-P1", "BG-P2"}
        )
        exact = dict(sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}) or {})
        for space_id in ("LT-P1", "LT-P2", "BG-P1", "BG-P2"):
            exact.pop(space_id, None)
        sc.config["TERMINAL_INVENTORY_EXACT_T"] = exact
        apply_planning_demand_profile(sc, DEFAULT_PLANNING_DEMAND_PROFILE)
        sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
        return "solver"
    if sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME) and sc.config.get(
            "BUILTIN_BASELINE_REVISION") == "2026-09-15-s0-soft-parcel-batching-v33":
        # v34 changes scheduling, not topology, stock or order contracts.
        # Bypass older topology migrations so a hot reload cannot replace
        # operator stock/assay edits, reorder orders or reset custom weeks.
        reference = mph_phosphate() if sc.name == S2_SCENARIO_NAME else end_to_end()
        profile_id = sc.config.get("ACTIVE_PLANNING_DEMAND_PROFILE", "")
        if sc.name == S3_SCENARIO_NAME and profile_id in PLANNING_DEMAND_PROFILES:
            apply_planning_demand_profile(reference, profile_id)
        _install_missing_prewash_baseline(sc, reference)
        _migrate_chevron_policy_v5(sc)
        _migrate_chevron_policy_v6(sc)
        _migrate_chevron_policy_v7(sc)
        _install_additional_mph_matrix(sc)
        for key in SCHEDULING_DEFAULTS:
            sc.config.setdefault(key, deepcopy(reference.config.get(key, SCHEDULING_DEFAULTS[key])))
        for key, target_key in (("PLANNING_PROCESS_STAGE_TARGETS", "PROCESS_STAGE_OUTPUT_TARGET_T"),
                                ("PLANNING_CORRIDOR_TARGETS", "CORRIDOR_DISPATCH_TARGET_T")):
            sc.config.setdefault(key, deepcopy(reference.config.get(key, []))
                                 if sc.config.get(target_key) == reference.config.get(target_key) else [])
        if sc.config.get("WEEKLY_TOTAL_WALL_TIME_S") == 84 and sc.config.get("WEEKLY_WINDOW_WALL_TIME_S") == 12:
            sc.config["WEEKLY_TOTAL_WALL_TIME_S"] = 280.0
            sc.config["WEEKLY_WINDOW_WALL_TIME_S"] = 40.0
        sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
        return "solver"
    legacy_blend_reset = False
    if (sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME)
            and sc.config.get("BUILTIN_BASELINE_REVISION") != BUILTIN_BASELINE_REVISION):
        # A topology migration must also finish the legacy blend migration in
        # this same call. Previously the new revision stamp was written first,
        # so the next page rerun performed a second solver migration and
        # cleared freshly generated outputs again.
        legacy_blend = sc.config.get("CHEVRON_STACKING", {})
        try:
            legacy_version = int(legacy_blend.get("model_version", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            legacy_version = 0
        if legacy_version < 4:
            legacy_blend_reset = True
            if legacy_blend:
                archives = sc.config.get("LEGACY_BLEND_POLICY_ARCHIVE", [])
                archives = list(archives) if isinstance(archives, list) else []
                archives.append(deepcopy(legacy_blend))
                sc.config["LEGACY_BLEND_POLICY_ARCHIVE"] = archives
            sc.config["CHEVRON_STACKING"] = _default_chevron_stacking_config()
            legacy_product_blend = sc.config.pop("M16_PRODUCT_STOCKPILE_BLENDING", None)
            if legacy_product_blend is not None:
                sc.config["LEGACY_PRODUCT_BLENDING_ARCHIVE"] = deepcopy(legacy_product_blend)
    blend_policy_v5_changed = (
        _migrate_chevron_policy_v5(sc)
        if sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME) else False)
    blend_policy_v6_changed = (
        _migrate_chevron_policy_v6(sc)
        if sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME) else False)
    blend_policy_v7_changed = (
        _migrate_chevron_policy_v7(sc)
        if sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME) else False)
    blend_catalog_changed = _install_additional_mph_matrix(sc)
    if sc.config.get("BUILTIN_BASELINE_REVISION") == BUILTIN_BASELINE_REVISION:
        # Blending policy changed without a topology revision. Hot Streamlit
        # sessions need the corrected M16 pile-construction definition;
        # imported scenarios never enter this migration function.
        blending_cfg = sc.config.get("CHEVRON_STACKING", {})
        try:
            blending_version = int(
                blending_cfg.get("model_version", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            blending_version = 0
        if sc.name in (S3_SCENARIO_NAME, S2_SCENARIO_NAME) and (
                not isinstance(blending_cfg, dict)
                or blending_version < 4):
            fresh = (
                mph_phosphate() if sc.name == S2_SCENARIO_NAME else
                end_to_end(
                    ben_b02_enabled=bool(sc.config.get(
                        "BEN_B02_OPTION_ENABLED", True)),
                    ben_raw_to_c2=bool(sc.config.get(
                        "BEN_RAW_TO_C2_ENABLED", False)),
                    ben_st3_stacking=bool(sc.config.get(
                        "BEN_ST3_STACKING_ENABLED", False)),
                    multi_grade_zones=bool(sc.config.get(
                        "MULTI_GRADE_ZONES_ENABLED", False)),
                ))
            sc.config["CHEVRON_STACKING"] = deepcopy(
                fresh.config.get(
                    "CHEVRON_STACKING",
                    _default_chevron_stacking_config()))
            sc.config.pop("M16_PRODUCT_STOCKPILE_BLENDING", None)
            return "solver"
        prewash_baseline_changed = False
        if sc.name == S3_SCENARIO_NAME:
            prewash_baseline_changed = _install_missing_prewash_baseline(
                sc, end_to_end())
        if (train_export_grades_changed or prewash_baseline_changed
                or pap_g10_changed or blend_policy_v7_changed or blend_catalog_changed):
            return "solver"
        return "metadata" if (
            blend_policy_v5_changed or blend_policy_v6_changed) else ""
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
            *WASHED_GRADE_IDS,
        }
        sc.grades = [grade for grade in sc.grades
                     if grade.id not in raw_ids] + [
            deepcopy(grade) for grade in fresh_s2.grades
            if grade.id in {RAW_PENDING_GRADE_ID, *WASHED_GRADE_IDS}
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
        for key in OPERATIONAL_OBJECTIVE_CONFIG_KEYS:
            if key not in sc.config and key in fresh_s2.config:
                sc.config[key] = deepcopy(fresh_s2.config[key])
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
    prewash_blending_changed = (
        sc.config.get("PRE_WASHING_BLENDING")
        != fresh.config.get("PRE_WASHING_BLENDING"))
    sc.config["PRE_WASHING_BLENDING"] = deepcopy(
        fresh.config["PRE_WASHING_BLENDING"])
    prewash_baseline_changed = _install_missing_prewash_baseline(sc, fresh)
    operational_policy_installed = False
    provenance_reference = deepcopy(fresh)
    loaded_profile = sc.config.get("ACTIVE_PLANNING_DEMAND_PROFILE", "")
    if loaded_profile in PLANNING_DEMAND_PROFILES:
        apply_planning_demand_profile(provenance_reference, loaded_profile)
    for key in OPERATIONAL_OBJECTIVE_CONFIG_KEYS:
        if key not in sc.config and key in fresh.config:
            if key in ("PLANNING_PROCESS_STAGE_TARGETS", "PLANNING_CORRIDOR_TARGETS"):
                target_key = ("PROCESS_STAGE_OUTPUT_TARGET_T" if "PROCESS" in key
                              else "CORRIDOR_DISPATCH_TARGET_T")
                # No authority to soften legacy operator edits. Infer forecast
                # provenance only when the complete saved target map matches
                # the selected preset's derivation.
                sc.config[key] = (deepcopy(provenance_reference.config.get(key, []))
                                  if sc.config.get(target_key) == provenance_reference.config.get(target_key)
                                  else [])
                operational_policy_installed = True
                continue
            sc.config[key] = deepcopy(fresh.config[key])
            operational_policy_installed = True
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
        "S3-A26", "S3-A27", "S3-A28", "S3-A29", "S3-A30", "S3-A31",
        "S3-A32", "S3-A33", "S3-A34",
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
        *RAW_GRADE_IDS,
        *LEGACY_RAW_GRADE_ID_ALIASES,
        *WASHED_GRADE_IDS,
        *TRAIN_EXPORT_GRADE_IDS,
    }
    quality_vector_grade_ids = set(WASHED_GRADE_IDS) | set(RAW_GRADE_IDS)
    old_grade_vectors = {
        grade.id: (dict(grade.quality), grade.rock_value,
                   getattr(grade, "source_grade_id", ""))
        for grade in sc.grades if grade.id in quality_vector_grade_ids
    }
    old_export_grades = {
        grade.id: (deepcopy(getattr(grade, "receiving_spec", [])),
                   dict(getattr(grade, "source_quality_ranges", {})),
                   tuple(getattr(grade, "receiving_destinations", [])))
        for grade in sc.grades if grade.id in set(TRAIN_EXPORT_GRADE_IDS)
    }
    sc.grades = [g for g in sc.grades if g.id not in refreshed_grade_ids] + [
        deepcopy(g) for g in fresh.grades
        if g.id in {
            RAW_PENDING_GRADE_ID, *RAW_GRADE_IDS, *WASHED_GRADE_IDS,
            *TRAIN_EXPORT_GRADE_IDS,
        }
    ]
    quality_grades_changed = old_grade_vectors != {
        grade.id: (dict(grade.quality), grade.rock_value,
                   getattr(grade, "source_grade_id", ""))
        for grade in sc.grades if grade.id in quality_vector_grade_ids
    }
    export_grades_changed = old_export_grades != {
        grade.id: (deepcopy(getattr(grade, "receiving_spec", [])),
                   dict(getattr(grade, "source_quality_ranges", {})),
                   tuple(getattr(grade, "receiving_destinations", [])))
        for grade in sc.grades if grade.id in set(TRAIN_EXPORT_GRADE_IDS)
    }
    existing_quality_params = {p.name for p in sc.quality_params}
    sc.quality_params.extend(
        deepcopy(p) for p in fresh.quality_params
        if p.name not in existing_quality_params
    )
    refreshed_profiles = {
        p.id: p for p in fresh.material_profiles
        if p.id in {
            "M16-WASHED-HUMID",
            "BEN-RAW-SCREENED",
            "BEN-WASHED-FILTERED",
            "BEN-SETTLED-PRODUCT",
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
    for key in (
            "WASHED_GRADE_SOURCE_MAPPING",
            "WASHED_GRADE_BY_RAW_AND_FAMILY",
            "WASHED_GRADE_MAPPING_BASIS"):
        sc.config[key] = deepcopy(fresh.config[key])
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

    # Revision v27 changes S0's planning contract from one day to seven civil
    # days while retaining an hourly solver/job grid.  Streamlit hot reloads
    # keep the old Scenario and even old DemandEntry class instances alive,
    # so rebuild the complete demand bundle instead of mutating those rows in
    # place.  Preserve the operator's selected built-in profile where valid.
    # Reapplying the profile below always changes/normalises solver inputs for
    # an object whose revision stamp reached this migration branch.
    weekly_planning_changed = (
        sc.sim.horizon_buckets != fresh.sim.horizon_buckets
        or sc.sim.dt_h != fresh.sim.dt_h
        or sc.sim.horizon_minutes != fresh.sim.horizon_minutes)
    sc.sim.horizon_buckets = fresh.sim.horizon_buckets
    sc.sim.dt_h = fresh.sim.dt_h
    sc.sim.horizon_minutes = fresh.sim.horizon_minutes
    sc.sim.mixed_resolution_enabled = False
    profile_id = str(sc.config.get(
        "ACTIVE_PLANNING_DEMAND_PROFILE",
        DEFAULT_PLANNING_DEMAND_PROFILE))
    if profile_id not in PLANNING_DEMAND_PROFILES:
        profile_id = DEFAULT_PLANNING_DEMAND_PROFILE
    if weekly_planning_changed:
        apply_planning_demand_profile(sc, profile_id)
    sc.config["BUILTIN_BASELINE_REVISION"] = BUILTIN_BASELINE_REVISION
    blend_catalog_changed = _install_additional_mph_matrix(sc) or blend_catalog_changed
    return ("solver" if (grade_ids_changed
                         or quality_grades_changed
                         or export_grades_changed
                         or train_export_grades_changed
                         or pap_g10_changed
                         or destination_schema_changed
                         or corridor_changed or belt_changed or louta_changed
                         or louta_process_changed
                         or benguerir_process_changed or m16_changed
                         or solver_policy_changed or weekly_planning_changed
                         or prewash_blending_changed
                         or prewash_baseline_changed
                         or blend_catalog_changed
                         or legacy_blend_reset
                         or operational_policy_installed)
            else "metadata")
