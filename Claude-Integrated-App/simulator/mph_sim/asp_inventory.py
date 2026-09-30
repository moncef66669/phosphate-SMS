"""Offline, position-aware inventory projection for the ASP validation gate.

This is not a 3-D reclaim-face model. Each supplied segment is uniform within
its own bounds; gaps between supplied segments are empty. Reclaims reserve
mass at start, while stacks become available at completion. No unlocated
exogenous/process inflow is invented, and no scenario input is mutated.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite

from .domain import UNKNOWN_STOCK_QUALITY_KEY


EPS = 1e-6


@dataclass
class InventoryLot:
    x_from: float
    x_to: float
    mass_t: float
    grade: str
    quality: dict
    age_days: float = 0.0


class InventoryProjection:
    """Fresh per-evaluation ledger; physical positions are metres, not repères.

    ``ASP_ALLOW_ASSUMED_INVENTORY`` defaults to True for existing offline demo
    scenarios. False requires explicit spatial inventory and assays. Assumed
    geometry/chemistry is always returned as an InventoryBasis information
    issue by the adapter; neither mode claims plant certification.
    """

    def __init__(self, scenario):
        self.sc = scenario
        self.allow_assumed = scenario.config.get(
            "ASP_ALLOW_ASSUMED_INVENTORY", True) is True
        self.lots = {}
        self.errors = {}
        self.notes = {}
        self.pending = []  # (completion, item id, pile, kind, mass, lot)
        self.reserved_reclaim = {}
        self.pending_stack = {}
        for space in scenario.spaces:
            self.lots[space.id] = []
            self.errors[space.id] = []
            self.notes[space.id] = set()
            self.reserved_reclaim[space.id] = 0.0
            self.pending_stack[space.id] = 0.0
            self._initialize(space)

    def _quality(self, pile, grade, explicit):
        if explicit:
            return dict(explicit)
        if pile in self.sc.config.get(UNKNOWN_STOCK_QUALITY_KEY, []):
            return {}
        if not self.allow_assumed:
            return {}
        space = self.sc.space(pile)
        # Never apply a dominant pile's assay to a different segment grade.
        if grade == space.grade and space.quality:
            quality = dict(space.quality)
        else:
            material = self.sc.grade(grade)
            quality = dict(material.quality) if material else {}
        if quality:
            self.notes[pile].add("Missing local assay uses the scenario quality "
                                  "basis, not a confirmed segment analysis.")
        return quality

    def _initialize(self, space):
        fail = self.errors[space.id].append
        values = (space.x_from, space.x_to, space.mass_t, space.capacity_t)
        if not all(isfinite(v) for v in values) or \
                space.x_to <= space.x_from or space.mass_t < 0 or \
                space.capacity_t < space.mass_t:
            fail("Invalid pile bounds, mass or capacity")
            return
        segments = sorted((s for s in self.sc.segments
                           if s.space_id == space.id),
                          key=lambda s: (s.x_from, s.x_to))
        if not segments:
            if space.mass_t <= EPS:
                return
            if not self.allow_assumed:
                fail("Spatial inventory is missing; import segment inventory")
                return
            self.notes[space.id].add("No spatial inventory supplied: opening "
                                     "mass is assumed uniform across the pile.")
            self.lots[space.id].append(InventoryLot(
                space.x_from, space.x_to, space.mass_t, space.grade,
                self._quality(space.id, space.grade, space.quality)))
            return
        previous_end = space.x_from
        for segment in segments:
            if not all(isfinite(v) for v in (segment.x_from, segment.x_to,
                                             segment.mass_t, segment.age_days)) or \
                    segment.x_to <= segment.x_from or segment.mass_t < 0 or \
                    segment.age_days < 0 or segment.x_from < previous_end or \
                    segment.x_from < space.x_from or segment.x_to > space.x_to:
                fail("Invalid, overlapping or out-of-bounds inventory segments")
                return
            previous_end = segment.x_to
            grade = segment.grade or space.grade
            self.lots[space.id].append(InventoryLot(
                segment.x_from, segment.x_to, segment.mass_t, grade,
                self._quality(space.id, grade, segment.quality), segment.age_days))
        total = sum(s.mass_t for s in segments)
        if abs(total - space.mass_t) > EPS:
            fail(f"Segment mass {total:g} t does not reconcile with "
                 f"pile mass {space.mass_t:g} t; refresh the inventory snapshot")

    def advance(self, timestamp):
        remaining = []
        for event in sorted(self.pending, key=lambda e: (e[0], e[1])):
            end, _, pile, kind, mass, lot = event
            if end > timestamp:
                remaining.append(event)
            elif kind == "Stacking":
                self.pending_stack[pile] -= mass
                self.lots[pile].append(lot)
            else:
                self.reserved_reclaim[pile] -= mass
        self.pending = remaining

    def _split(self, pile, boundaries):
        pieces = []
        for lot in self.lots[pile]:
            cuts = [lot.x_from] + sorted({x for x in boundaries
                                        if lot.x_from < x < lot.x_to}) + [lot.x_to]
            for lo, hi in zip(cuts, cuts[1:]):
                pieces.append(replace(lot, x_from=lo, x_to=hi,
                                      mass_t=lot.mass_t * (hi-lo) /
                                      (lot.x_to-lot.x_from)))
        self.lots[pile] = pieces

    def apply(self, item, end):
        """Return errors; commit a reservation only if all inventory checks pass."""
        pile = item.Pile
        if pile not in self.lots:
            return ["Unknown pile"]
        if self.errors[pile]:
            return list(self.errors[pile])
        if item.JobType not in ("Stacking", "Reclaiming"):
            return []
        lo, hi = sorted((item.StartX, item.StopX))
        mass = item.PlannedTonnage
        if item.JobType == "Stacking":
            physical_and_reserved = (sum(s.mass_t for s in self.lots[pile])
                                     + self.reserved_reclaim[pile]
                                     + self.pending_stack[pile])
            if physical_and_reserved + mass > self.sc.space(pile).capacity_t + EPS:
                return [f"Stacking exceeds pile capacity: "
                        f"{physical_and_reserved + mass:g} t including reservations"]
            material = self.sc.grade(item.Grade)
            if not material or getattr(material, "is_specification", False):
                return [f"Invalid physical stacking grade: {item.Grade}"]
            # A planned grade's chemistry is a forecast, never a measured assay.
            quality = dict(material.quality) if self.allow_assumed else {}
            self.notes[pile].add("Stacking is projected uniformly within its "
                                 "position range and released at completion; "
                                 "its assay is forecast, not measured by SMS.")
            lot = InventoryLot(lo, hi, mass, item.Grade, quality)
            self.pending_stack[pile] += mass
            self.pending.append((end, item.JobId, pile, "Stacking", mass, lot))
            return []
        self._split(pile, (lo, hi))
        selected = [s for s in self.lots[pile]
                    if s.x_from >= lo and s.x_to <= hi and s.mass_t > EPS]
        available = sum(s.mass_t for s in selected)
        errors = []
        if available <= EPS or mass > available + EPS:
            errors.append(f"Insufficient inventory in {lo:g}-{hi:g} m: "
                          f"need {mass:g} t, available after earlier "
                          f"reservations {available:g} t")
        if any(not s.quality or not all(isinstance(v, (int, float)) and isfinite(v)
                                       for v in s.quality.values())
               for s in selected):
            errors.append("Grade not yet confirmed: missing or invalid analysis "
                          "in the requested position range")
        grades = {}
        for lot in selected:
            grades[lot.grade] = grades.get(lot.grade, 0.0) + lot.mass_t
        if grades:
            peak = max(grades.values())
            prevalent = [g for g, m in grades.items() if abs(m - peak) <= EPS]
            if len(prevalent) != 1 or prevalent[0] != item.Grade or not item.Grade:
                errors.append(f"Grade mismatch in requested range: prevalent "
                              f"{', '.join(sorted(prevalent))}; "
                              f"requested {item.Grade}")
        if errors:
            return errors
        # Proportional withdrawal from all lots in the range, NOT selective
        # extraction of a convenient grade from an unmodelled vertical layer.
        fraction = min(1.0, mass / available)
        for lot in selected:
            lot.mass_t *= 1.0 - fraction
        self.reserved_reclaim[pile] += mass
        self.pending.append((end, item.JobId, pile, "Reclaiming", mass, None))
        return []
