"""Benguerir washed-product block, redrawn as a replica of the LOUTA block.

Design-review only. The production geometry (``simulator/ui/layout_geometry``)
is imported read-only and never modified; this module derives a NEW
``SiteGeometry`` for the BENGUERIR view by taking Benguerir's own real elements
(the model-backed product stage BG-P1/BG-P2 + RC2/ST3/RC3, the display-only
screened-ore piles and their machines, and the three washing lines) and
re-placing them onto Louta's clean four-stage template:

    mine storage (NEW, display-only) -> screened-ore storage -> washing (3
    lines) -> washed-product storage -> C2 / BG-TRAIN dispatch

Two things differ from Louta on purpose, per the review request:
  * THREE washing lines (Louta has two);
  * a mine-storage stockyard with two stockpile lines is ADDED on top. Those two
    piles are not in the production model, so they are drawn DISPLAY-ONLY
    (no tonnage/grade/solver role) — an honest layout proposal.

Only BG-P1/BG-P2 and RC2/ST3/RC3 keep their ids, so their live fill/state still
binds. Everything else is display/reference, exactly as in the real Benguerir
geometry.
"""
from __future__ import annotations

import copy
from typing import Dict, List

from ui.layout_geometry import (ChipGeo, ConveyorGeo, EquipGeo, MachineGeo,
                                PileGeo, ProcessLineGeo, RailGeo, SiteGeometry,
                                StampGeo, ZoneGeo, geometry_for)

# ---- Louta four-stage coordinate template (ox=oy=0, from louta_geometry) -----
# mine storage (Louta 102MG)
_MINE_P1 = (22, 48.4, 92, 51.4)
_MINE_P2 = (22, 42.4, 92, 45.4)
_MINE_RAIL_Y = 46.9
# screened-ore storage (Louta 110/120LG)
_ORE_P1 = (8, 32.4, 66, 35.2)
_ORE_P2 = (8, 26.6, 66, 29.4)
_ORE_RY_TOP, _ORE_RY_MID, _ORE_RY_BOT = 35.9, 30.9, 25.1
_ORE_RX = (6.5, 67.5)
# washing band (Louta washing stage)
_WASH_Y0, _WASH_Y1 = 18.4, 20.4
_WASH_CARDS = [(14, 33), (37, 56), (60, 79)]      # three line cards, x spans
# washed-product storage (Louta 130/140LG)
_PROD_P1 = (8, 8.6, 80, 11.6)
_PROD_P2 = (8, 2.6, 80, 5.6)
_PROD_RY_TOP, _PROD_RY_MID, _PROD_RY_BOT = 13.1, 7.1, 1.2
_PROD_RX = (6.5, 81.5)


def _reposition_pile(p: PileGeo, box) -> None:
    p.x0, p.y0, p.x1, p.y1 = box


def _reposition_rail(r: RailGeo, x0: float, x1: float, y: float) -> None:
    r.x0, r.x1, r.y = x0, x1, y


def benguerir_replica_geometry(sc, show_future: bool = False) -> SiteGeometry:
    src = geometry_for(sc, "BENGUERIR", show_future=show_future)
    piles = {p.pile_id: copy.deepcopy(p) for p in src.piles}
    rails = {r.rail_id: copy.deepcopy(r) for r in src.rails}
    machines = {m.machine_id: copy.deepcopy(m) for m in src.machines}
    lines = {pl.line_id: copy.deepcopy(pl) for pl in src.process_lines}

    g = SiteGeometry(
        "BENGUERIR", "BENGUERIR — replica of Louta layout (proposed)",
        bounds=(0, 0, 128, 56), not_to_scale=True,
        source="proposed layout (design review) — Louta template applied to "
               "Benguerir · production geometry unchanged")

    # ================= stage 4: washed-product storage (REAL) ================
    _reposition_pile(piles["BG-P1"], _PROD_P1)
    _reposition_pile(piles["BG-P2"], _PROD_P2)
    _reposition_rail(rails["BG-RL-P1"], *_PROD_RX, _PROD_RY_TOP)
    _reposition_rail(rails["BG-SL"], *_PROD_RX, _PROD_RY_MID)
    _reposition_rail(rails["BG-RL-P2"], *_PROD_RX, _PROD_RY_BOT)

    # ================= stage 2: screened-ore storage (display) ===============
    # Re-id the two screened-ore piles so the global "MINE ORE" bunker grouping
    # (which wraps BG-RAW-1/2 in the composite) no longer applies here — this
    # stage is screened ore, and the dedicated mine-storage stage sits above it.
    ore1, ore2 = piles["BG-RAW-1"], piles["BG-RAW-2"]
    ore1.pile_id, ore2.pile_id = "BG-SCR-1", "BG-SCR-2"
    ore1.label = "BEN · SCREENED-ORE PILE 1"
    ore2.label = "BEN · SCREENED-ORE PILE 2"
    _reposition_pile(ore1, _ORE_P1)
    _reposition_pile(ore2, _ORE_P2)
    rails["BG-RAW-ST1-R"].flanks = ["BG-SCR-1"]
    rails["BG-RAW-R"].flanks = ["BG-SCR-1", "BG-SCR-2"]
    rails["BG-RAW-ST2-R"].flanks = ["BG-SCR-2"]
    _reposition_rail(rails["BG-RAW-ST1-R"], *_ORE_RX, _ORE_RY_TOP)
    _reposition_rail(rails["BG-RAW-R"], *_ORE_RX, _ORE_RY_MID)
    _reposition_rail(rails["BG-RAW-ST2-R"], *_ORE_RX, _ORE_RY_BOT)

    # ================= stage 1: mine storage (NEW, display-only) =============
    mine1 = copy.deepcopy(piles["BG-RAW-1"])
    mine2 = copy.deepcopy(piles["BG-RAW-2"])
    mine1.pile_id, mine2.pile_id = "BG-MINE-1", "BG-MINE-2"
    mine1.label = "BEN · MINE STOCKPILE-1 (proposed)"
    mine2.label = "BEN · MINE STOCKPILE-2 (proposed)"
    for m in (mine1, mine2):
        m.modelled = False
        m.label_with_total = False
        m.display_note = ("proposed mine-storage stockyard — NOT in the "
                          "production model · no tonnage/solver inventory")
    _reposition_pile(mine1, _MINE_P1)
    _reposition_pile(mine2, _MINE_P2)
    mine_rail = RailGeo("BG-MINE-R", _MINE_P1[0] - 1.5, _MINE_RAIL_Y,
                        _MINE_P1[2] + 1.5, kind="stacker",
                        flanks=["BG-MINE-1", "BG-MINE-2"], working_sides="both",
                        label="mine stacking line (proposed · display-only)",
                        tbc=False, label_pos="above")
    mine_st1 = MachineGeo("BG-MINE-ST-1", "BG-MINE-R", 0.30, 0.7, display=True,
                          kind="stacker", label="BEN · Mine stacker 1",
                          note="proposed display-only mine-storage stacker")
    mine_st2 = MachineGeo("BG-MINE-ST-2", "BG-MINE-R", 0.68, 0.7, display=True,
                          kind="stacker", label="BEN · Mine stacker 2",
                          note="proposed display-only mine-storage stacker")

    # ================= washing: three line cards side by side ================
    order = ["BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3"]
    for lid, (cx0, cx1) in zip(order, _WASH_CARDS):
        pl = lines[lid]
        pl.x0, pl.y0, pl.x1, pl.y1 = cx0, _WASH_Y0, cx1, _WASH_Y1

    # ---- assemble piles / rails / machines ----------------------------------
    g.piles = [mine1, mine2, piles["BG-RAW-1"], piles["BG-RAW-2"],
               piles["BG-P1"], piles["BG-P2"]]
    g.rails = [mine_rail, rails["BG-RAW-ST1-R"], rails["BG-RAW-R"],
               rails["BG-RAW-ST2-R"], rails["BG-RL-P1"], rails["BG-SL"],
               rails["BG-RL-P2"]]
    g.machines = [mine_st1, mine_st2,
                  machines["BG-RAW-ST-1"], machines["BG-RAW-BWR"],
                  machines["BG-RAW-ST-2"],
                  machines["RC2"], machines["ST3"], machines["RC3"]]
    g.process_lines = [lines["BG-WASH-L1"], lines["BG-WASH-L2"],
                       lines["BG-WASH-L3"]]

    # ---- zones (mirror Louta's scope + stage structure) ---------------------
    g.zones = [
        ZoneGeo("BENGUERIR MINE SMS — screened-product mine storage "
                "(proposed layout · display-only)", 1, 40.8, 104, 52.4),
        ZoneGeo("BENGUERIR BENEFICIATION SMS", 1, 0.7, 126.5, 38.6),
        ZoneGeo("SCREENED-ORE STORAGE — 2 stockpiles",
                3, 23.6, 74, 38.0, style="stage"),
        ZoneGeo("WASHING / BENEFICIATION — 3 LINES (Line 1 operating · "
                "Lines 2–3 future)", 12, 15.8, 82, 22.0, style="stage"),
        ZoneGeo("WASHED-PRODUCT STORAGE — 2 lines",
                3, 0.85, 89, 15.2, style="stage"),
    ]

    # ---- conveyors: Louta-style clean routing -------------------------------
    g.conveyors = _replica_conveyors()

    # ---- dispatch equipment (C2 + BG-TRAIN) ---------------------------------
    train = next((copy.deepcopy(e) for e in src.equipment
                  if e.kind == "destbox" and "TRAIN" in e.label), None)
    if train is not None:
        train.x, train.y = 118.0, 3.0
    g.equipment = [
        EquipGeo("diverter", 71.5, 30.9, "raw flip gates", scale=0.8),
        EquipGeo("diverter", 100, 11.4, "product flip gate", scale=0.8),
        EquipGeo("diverter", 100, 6.2, "product flip gate", scale=0.8),
        EquipGeo("junction", 113.5, 8.8),
        EquipGeo("corridor_out", 121, 8.8, "C2 → M16", link_ids=["C2"],
                 scale=0.8),
    ]
    if train is not None:
        g.equipment.append(train)

    # ---- a couple of orienting chips, plus the schematic stamp --------------
    g.chips = [
        ChipGeo("PROPOSED LAYOUT — mine storage is display-only", 99, 34.2,
                note="Benguerir redrawn on the Louta template for review; the "
                     "mine-storage stockyard is not in the production model.",
                kind="info"),
    ]
    g.stamps = [StampGeo(103, 54.6), StampGeo(106, 2.6,
                "washed product → C2 (→ M16) · and → BG-TRAIN station")]
    g.height_px = 780
    return g


def _replica_conveyors() -> List[ConveyorGeo]:
    c: List[ConveyorGeo] = []
    # mine -> screened-ore (short vertical interface, like Louta LT-102MG-BEN)
    c.append(ConveyorGeo("BG-MINE-IN", [(7, 46.9), (19, 46.9)],
                         tag="from Benguerir mine ore transport",
                         planning_enabled=False, label_frac=0.8, label_dy=-1.2))
    c.append(ConveyorGeo("BG-MINE-ORE", [(45, 42.0), (45, 38.6)],
                         tag="to screened-ore storage",
                         planning_enabled=False, label_frac=0.5))
    # ore reclaim -> common transfer -> three washing lines (like RB1..RB7)
    c += [
        ConveyorGeo("BG-ORE-RB1", [(67.5, 35.9), (71.5, 35.9), (71.5, 31.4)],
                    planning_enabled=False, tag="RB1", label_frac=0.35,
                    label_dy=1.1),
        ConveyorGeo("BG-ORE-RB2", [(67.5, 25.1), (71.5, 25.1), (71.5, 30.4)],
                    planning_enabled=False, tag="RB2", label_frac=0.35,
                    label_dy=-1.1),
        ConveyorGeo("BG-ORE-COMMON",
                    [(71.5, 30.4), (71.5, 22.9), (23, 22.9)],
                    planning_enabled=False,
                    tag="common ore transfer → washing", label_frac=0.72,
                    label_dy=0.9),
    ]
    for i, (cx0, cx1) in enumerate(_WASH_CARDS):
        xc = (cx0 + cx1) / 2
        c.append(ConveyorGeo(f"BG-ORE-L{i + 1}",
                             [(xc, 22.9), (xc, _WASH_Y1)],
                             planning_enabled=False, compact=True))
    # washing -> product stacking (left collector then down, like SL11)
    coll = 5.5
    drops = [16.6, 17.0, 17.4]
    for i, (cx0, cx1) in enumerate(_WASH_CARDS):
        xc = (cx0 + cx1) / 2
        c.append(ConveyorGeo(f"BG-WSTORE-L{i + 1}",
                             [(xc, _WASH_Y0), (xc, drops[i]), (coll, drops[i])],
                             planning_enabled=False, compact=True))
    c.append(ConveyorGeo("BG-SL-FEED",
                         [(coll, 16.6), (coll, 7.1), (6.5, 7.1)],
                         tag="SL → product stacking", planning_enabled=False,
                         label_frac=1.0, label_dy=1.0))
    # product reclaim -> dispatch (real C2 corridor + BG-TRAIN branch)
    c += [
        ConveyorGeo("BG-P1-COLLECT", [(81.5, 13.1), (85.5, 13.1)],
                    tag="product reclaim · line 1", label_frac=0.5,
                    label_dy=1.2),
        ConveyorGeo("BG-P2-COLLECT", [(81.5, 1.2), (85.5, 1.2)],
                    tag="product reclaim · line 2", label_frac=0.5,
                    label_dy=1.2),
        ConveyorGeo("BG-RL1", [(85.5, 13.1), (91, 11.4), (98.6, 11.4)],
                    machine_id="RC2", tag="RL1", label_frac=0.45, label_dy=1.6),
        ConveyorGeo("BG-RL2", [(85.5, 1.2), (91, 6.2), (98.6, 6.2)],
                    machine_id="RC3", tag="RL2", label_frac=0.45,
                    label_dy=-1.6),
        ConveyorGeo("BG-RL3", [(101.4, 11.4), (108, 10.2), (112.5, 10.2)],
                    tag="RL3", label_frac=0.55, label_dy=1.2),
        ConveyorGeo("BG-RL4", [(101.4, 6.2), (108, 7.4), (112.5, 7.4)],
                    tag="RL4", label_frac=0.55, label_dy=-1.2),
        ConveyorGeo("BG-C2-TAKEOFF", [(113.5, 8.8), (117.3, 8.8)],
                    link_ids=["C2"], tag="C2 take-off", label_frac=0.5,
                    label_dy=-1.4),
        ConveyorGeo("BG-TRAIN-BRANCH",
                    [(113.5, 8.8), (116, 5.5), (118, 4.6)],
                    tag="→ BG-TRAIN", label_frac=0.6, label_dy=1.0),
    ]
    return c
