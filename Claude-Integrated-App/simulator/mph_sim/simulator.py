"""Discrete-time closed-loop simulation engine — layer 5.

Models the report's loop: plan -> validate -> dispatch -> execute -> feedback
(JobUpdated) -> detect deviation -> replan (§15.8). Runs entirely offline on
the scenario snapshot; supports the 11 required disturbance event types.
Fully deterministic for a given (scenario, plan, event list) - the stored
seed is threaded through the solver for deterministic replay.
"""
from __future__ import annotations

import copy
import datetime as dtm
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .blending import (CAMPAIGN_COMPLETE,
                       chevron_campaign_state, chevron_construction_batch_t,
                       chevron_enabled_for_space, chevron_is_demand_driven,
                       chevron_ready_minute,
                       update_chevron_campaign_state)
from .domain import (BeltProfileEntry, FixedJob, Job, JobUpdate, LogRecord,
                     PlanResult, Scenario, SimEvent, UNKNOWN_STOCK_QUALITY_KEY,
                     UNKNOWN_BELT_QUALITY_KEY)
from .coordinates import x_interval_to_reperes
from .continuity import compute_continuity_metrics, parse_continuity_policies
from .optimizer import SolveOptions, job_group_key, solve_plan
from .timing import bucket_minutes, validated_execution_windows
from . import mine_arrivals, washing


@dataclass
class BeltParcel:
    link_id: str
    arrive_bucket: int
    mass_t: float
    grade: str
    quality: Dict[str, float] = field(default_factory=dict)
    material_id: str = ""


class Simulator:
    def __init__(self, scenario: Scenario, plan: PlanResult,
                 events: Optional[List[SimEvent]] = None,
                 auto_replan: bool = True):
        self.sc = copy.deepcopy(scenario)
        if mine_arrivals.CONFIG_KEY in self.sc.config:
            mine_arrivals.refresh(self.sc)
        self.plan = plan
        self._adopt_blend_plan(plan)
        self.auto_replan = auto_replan
        self.bucket = 0
        self.T = scenario.sim.horizon_buckets
        self.dt = scenario.sim.dt_h
        self.bucket_width_min = bucket_minutes(scenario)
        self.current_minute = 0

        self.jobs: Dict[str, Job] = {j.id: copy.deepcopy(j) for j in plan.jobs}
        self.events: List[SimEvent] = list(copy.deepcopy(events or []))
        self.events.sort(key=self._event_minute)
        self._applied_event_keys: Set[Tuple[str, int]] = set()
        self.log: List[LogRecord] = []
        self.job_updates: List[JobUpdate] = []

        # live state ------------------------------------------------------
        self.inv: Dict[str, float] = {s.id: s.mass_t for s in self.sc.spaces}
        self.space_quality: Dict[str, Dict[str, float]] = {
            s.id: dict(self.sc.quality_of(s.id)) for s in self.sc.spaces}
        self.material_catalog = copy.deepcopy(plan.material_catalog)
        self.material_mass = {
            sid: {mid: float(series[0]) for mid, series in materials.items() if series}
            for sid, materials in plan.material_inventory.items()}
        self.material_pending = copy.deepcopy(
            self.sc.config.get("_WASHING_MODEL_PENDING", {}))
        self.material_quality = {
            mid: dict(record["quality"]) for mid, record in self.material_catalog.items()}
        self.material_assays: Dict[str, Dict[str, dict]] = {}
        self.actual_washing_flows: List[Dict[str, object]] = []
        # Physical tonnes at the upstream receiving boundary. Rejected mine
        # arrivals stay outside the model and are never created as inventory.
        self.actual_mine_arrivals: List[Dict[str, object]] = []
        # Planning is whole-tonne; live measurements need not be. Preserve
        # the measured remainder in actual pools instead of losing it when
        # initializing from quantized planning inventories.
        for sid in self.material_mass:
            self._material_inventory_event(sid)
        # A chevron campaign is configured in physical wet-product tonnes,
        # but its chemistry is measured on dry solids.  Keep the physical and
        # dry quality bases separately so execution uses the same mass basis
        # as blend design. Ordinary inventory mixing uses the same dry-solids
        # basis whenever the component moistures are known.
        self._chevron_quality_basis: Dict[str, Dict[str, object]] = {}
        self._initialise_chevron_quality_basis()
        self.machine_down_until: Dict[str, int] = {}
        self.machine_down_until_minute: Dict[str, int] = {}
        self.machine_rate_factor: Dict[str, float] = {}
        self.machine_rate_factor_until_minute: Dict[str, int] = {}
        # Preserve overlapping temporary derates independently.  The two maps
        # above remain a compatibility/current-state view for existing callers.
        self.machine_rate_windows: Dict[
            str, List[Tuple[int, int, float]]] = {}
        self.link_down_until: Dict[str, int] = {}
        self.link_down_until_minute: Dict[str, int] = {}
        # Unexpected receiving-unit interruptions are resource outages, not
        # demand edits.  Separate live state preserves the original order as
        # unmet demand and allows normal work after the temporary stop.
        self.downstream_down_until: Dict[str, int] = {}
        self.downstream_down_until_minute: Dict[str, int] = {}
        self.demand_override: Dict[Tuple[str, int], float] = {}
        self.comms_down_until: int = -1
        self.comms_down_until_minute: int = -1
        self.machine_pos: Dict[str, float] = {m.id: m.position_x
                                              for m in self.sc.machines}
        # belt parcels without an explicit quality vector default to their
        # declared grade's vector (same convention as Scenario.quality_of);
        # an empty dict would otherwise blend in as ZERO quality and dilute
        # every pile it is stacked onto (defect fixed 14-Jul-2026)
        unknown_parcels = self.sc.config.get(UNKNOWN_BELT_QUALITY_KEY, [])
        def _parcel_quality(e, index) -> dict:
            if e.quality or index in unknown_parcels:
                return dict(e.quality)
            g = self.sc.grade(e.grade)
            return dict(g.quality) if g else {}

        self.belts: List[BeltParcel] = [
            BeltParcel(e.link_id, e.arrive_bucket, e.mass_t, e.grade,
                       _parcel_quality(e, index), e.material_id)
            for index, e in enumerate(self.sc.belt_profile)]
        if self.material_catalog:
            for parcel in self.belts:
                if not parcel.material_id:
                    matches = [mid for mid, record in self.material_catalog.items()
                               if record["grade_id"] == parcel.grade and record["quality"] == parcel.quality]
                    if len(matches) == 1:
                        parcel.material_id = matches[0]
                    else:
                        raise ValueError("in-transit parcel has no unique planned material identity")
        self.moved_by_job: Dict[str, float] = {}
        self.unstacked_t: float = 0.0
        self.banned: List[Tuple[str, str, str, int, int]] = []

        # per-bucket records ------------------------------------------------
        self.delivered: Dict[str, List[float]] = {d.id: [0.0] * self.T
                                                  for d in self.sc.destinations}
        self.delivered_q: Dict[str, List[Dict[str, float]]] = {
            d.id: [dict() for _ in range(self.T)] for d in self.sc.destinations}
        # Certified receiving identity is tracked independently from source
        # material identity.  For PAP this is G10 only after the hard assay
        # gate has accepted the movement.
        self.delivered_grade: Dict[str, List[str]] = {
            d.id: [""] * self.T for d in self.sc.destinations}
        self.inv_series: Dict[str, List[float]] = {s.id: [s.mass_t]
                                                   for s in self.sc.spaces}
        # per-bucket history for the Stockyard Monitor (additive; index 0 =
        # initial state, index t+1 = state after bucket t, like inv_series)
        self.quality_series: Dict[str, List[Dict[str, float]]] = {
            s.id: [dict(self.sc.quality_of(s.id))] for s in self.sc.spaces}
        # executed activity per bucket: what actually moved (survives replans)
        self.activity_log: List[Dict[str, object]] = []
        # outage history for the process mimic (additive): down_series[t] =
        # machines in a failure window during bucket t; link_down_series[t]
        # likewise for tripped conveyors/routes
        self.down_series: List[List[str]] = []
        self.link_down_series: List[List[str]] = []
        self.machine_active: Dict[str, List[int]] = {m.id: [0] * self.T
                                                     for m in self.sc.machines}
        self.link_load: Dict[str, List[float]] = {l.id: [0.0] * self.T
                                                  for l in self.sc.links}
        self.travel_m: Dict[str, float] = {m.id: 0.0 for m in self.sc.machines}
        self.starts_stops: Dict[str, int] = {m.id: 0 for m in self.sc.machines}
        self._prev_space: Dict[str, str] = {}
        self.replans = 0
        #: Attempts that returned no usable plan, leaving the PREVIOUS plan in
        #: force. ``replans`` counts attempts, not successes, so these two must
        #: be read together to know whether the plan is actually current.
        self.replan_failures = 0
        #: Attempts that only produced a plan after dropping the frozen jobs.
        #: The plan is current but the near-term commitment was not honoured.
        self.replan_freeze_relaxed = 0
        self.last_replan_failure: Optional[Dict[str, object]] = None
        self.replan_solve_s: List[float] = []
        self.plan_history: List[Tuple[int, List[Job], List[Job], str]] = []
        self.pending_replan_reason: str = ""
        cadence = int(getattr(self.sc.sim, "replan_interval_min", 0) or
                      self.bucket_width_min)
        self.next_replan_minute = max(cadence, 1)
        self.last_replan_minute = 0
        self.pending_replan_minute: Optional[int] = None
        self._bucket_runtime: Optional[Dict[str, object]] = None
        # Hard events can interrupt a minute-refined job inside a native solve
        # bucket.  Completed physical tonnes remain in ``moved_by_job`` and
        # this record exposes the residual handed to the next replan.
        self.interrupted_jobs: Dict[str, Dict[str, object]] = {}

    # ------------------------------------------------------------------ util
    def _adopt_blend_plan(self, plan: PlanResult) -> None:
        """Carry selected blend quantities into execution without declaring them built.

        Demand-driven quantities belong to the accepted plan, not the global
        configuration.  Physical campaign state remains authoritative after
        the first actual movement, including when a replan is accepted.
        """
        if not chevron_is_demand_driven(self.sc):
            return
        raw = dict(self.sc.config.get("CHEVRON_STACKING", {}) or {})
        campaigns = dict(raw.get("proposed_campaigns_by_space", {}) or {})
        recipes = dict(raw.get("proposed_recipes_by_space", {}) or {})
        matrices = dict(raw.get("proposed_matrix_by_space", {}) or {})
        records = dict(getattr(plan, "blend_campaigns", {}) or {})
        for space_id in set(campaigns) | set(recipes) | set(records):
            if chevron_campaign_state(self.sc, space_id).get("resolved_recipe"):
                continue
            record = records.get(space_id, {})
            composition = list(record.get("composition", ()) or ())
            if not composition:
                campaigns.pop(space_id, None)
                recipes.pop(space_id, None)
                matrices.pop(space_id, None)
                continue
            campaigns[space_id] = {
                key: copy.deepcopy(record[key])
                for key in ("batch_t", "total_t", "ready_minute", "result_grade",
                            "campaign_id", "planned_from_minute", "selected_matrix",
                            "served_grades", "served_destinations")
                if key in record}
            recipes[space_id] = copy.deepcopy(composition)
            selected = str(record.get("selected_matrix", "") or "")
            if selected:
                matrices[space_id] = selected
            else:
                matrices.pop(space_id, None)
        raw["proposed_campaigns_by_space"] = campaigns
        raw["proposed_recipes_by_space"] = recipes
        raw["proposed_matrix_by_space"] = matrices
        self.sc.config["CHEVRON_STACKING"] = raw

    def _log(self, kind: str, msg: str, asset: str = "", **detail):
        detail.setdefault("minute", self.current_minute)
        self.log.append(LogRecord(self.bucket, kind, msg, asset, dict(detail)))

    def _prepare_dynamic_blend_stack(self, job: Job) -> str:
        """Select the job's construction lot, reopening only a physically empty pile."""
        if not chevron_is_demand_driven(self.sc) or not self._is_chevron_stack(job):
            return ""
        space_id = str(job.target_space or job.space)
        state = chevron_campaign_state(self.sc, space_id)
        recipe = copy.deepcopy(list(job.blend_recipe or ()))
        if not recipe:
            return "blend construction recipe missing; stacking held"

        def composition(rows):
            return sorted((str(row.get("grade_id", "")),
                           float(row.get("tonnage_t", 0) or 0),
                           float(row.get("target_fraction", 0) or 0))
                          for row in rows)

        metadata = copy.deepcopy(dict(getattr(job, "blend_campaign", {}) or {}))
        status = str(state.get("status", ""))
        if status == "BUILDING":
            if composition(recipe) != composition(state.get("resolved_recipe", ())):
                return "previous blend construction incomplete; different recipe held"
            current_id = str(state.get("campaign_id", "") or "")
            next_id = str(metadata.get("campaign_id", "") or "")
            if current_id and next_id and current_id != next_id:
                return "previous blend construction incomplete; next lot held"
            return ""

        inventory = max(float(self.inv.get(space_id, 0)), 0)
        if status in {"COMPLETE", "DEPLETED"}:
            if inventory > 1e-6:
                return "previous blended inventory remains; new construction held"
            if job.id in state.get("accounted_job_t_by_id", {}):
                return "completed blend stack job already accounted; stacking held"
        elif inventory > 1e-6:
            return "unassigned stockpile inventory remains; blend construction held"

        raw = dict(self.sc.config.get("CHEVRON_STACKING", {}) or {})
        if not metadata:
            metadata = copy.deepcopy(dict(raw.get(
                "proposed_campaigns_by_space", {}).get(space_id, {}) or {}))
        recipe_total = sum(max(float(row.get("tonnage_t", 0) or 0), 0)
                           for row in recipe)
        if recipe_total <= 0:
            return "selected blend component tonnes missing; stacking held"
        metadata["batch_t"] = recipe_total
        metadata["total_t"] = recipe_total
        states = dict(raw.get("campaign_states_by_space", {}) or {})
        # Keep the accounting ledger even when physical contents are depleted:
        # snapshots revisit completed jobs and must not rebuild historical lots.
        states[space_id] = {
            "status": "NOT_STARTED", "remaining_inventory_t": 0,
            "accounted_job_t_by_id": dict(state.get("accounted_job_t_by_id", {})),
        }
        raw["campaign_states_by_space"] = states
        for field, value in (("proposed_recipes_by_space", recipe),
                             ("proposed_campaigns_by_space", metadata)):
            proposals = dict(raw.get(field, {}) or {})
            proposals[space_id] = value
            raw[field] = proposals
        matrices = dict(raw.get("proposed_matrix_by_space", {}) or {})
        selected_matrix = str(metadata.get("selected_matrix", "") or "")
        if selected_matrix:
            matrices[space_id] = selected_matrix
        else:
            matrices.pop(space_id, None)
        raw["proposed_matrix_by_space"] = matrices
        self.sc.config["CHEVRON_STACKING"] = raw
        self._chevron_quality_basis.pop(space_id, None)
        self.space_quality[space_id] = {}
        self.sc.space(space_id).quality = {}
        return ""

    @staticmethod
    def _blank_chevron_quality_basis() -> Dict[str, object]:
        return {
            "physical_t": 0.0,
            "dry_t": 0.0,
            "numerator_by_param": {},
            "denominator_by_param": {},
        }

    @staticmethod
    def _quality_from_chevron_basis(
            basis: Dict[str, object]) -> Dict[str, float]:
        numerators = basis.get("numerator_by_param", {})
        denominators = basis.get("denominator_by_param", {})
        quality: Dict[str, float] = {}
        for parameter, numerator in numerators.items():
            denominator = float(denominators.get(parameter, 0.0))
            expected_basis = float(basis.get(
                "physical_t" if parameter == "moisture" else "dry_t", 0.0))
            # Missing component assays remain unknown, including after
            # carrying a partially constructed campaign through a replan.
            if denominator > 1e-12 and math.isclose(
                    denominator, expected_basis, rel_tol=1e-9, abs_tol=1e-9):
                quality[str(parameter)] = round(
                    float(numerator) / denominator, 12)
        return quality

    def _grade_dry_fraction(
            self, grade_id: str, quality: Dict[str, float]) -> float:
        grade = self.sc.grade(str(grade_id or ""))
        moisture = quality.get("moisture")
        if moisture is None:
            moisture = (getattr(grade, "product_moisture_pct", None)
                        if grade is not None else None)
        if moisture is None:
            moisture = quality.get("moisture", 0.0)
        try:
            return max(min(1.0 - float(moisture or 0.0) / 100.0,
                           1.0), 0.0)
        except (TypeError, ValueError):
            return 1.0

    def _mix_quality(
            self, old_mass: float, old_quality: Dict[str, float],
            incoming_mass: float, incoming_quality: Dict[str, float], *,
            old_grade: str = "", incoming_grade: str = "",
            old_stage: str = "", incoming_stage: str = "") -> Dict[str, float]:
        """Mix positive physical masses without certifying absent assays.

        Chemistry uses dry solids when all moistures are known; moisture is
        always a wet-mass average. Explicit stage moisture fields can supply
        missing moisture, but never a chemical assay. With no complete moisture
        basis, legacy vectors retain equal dry fractions (physical weighting).
        """
        components = []
        for mass, raw_quality, grade_id, stage in (
                (old_mass, old_quality, old_grade, old_stage),
                (incoming_mass, incoming_quality, incoming_grade, incoming_stage)):
            if mass <= 0:
                continue
            quality = {}
            for parameter, raw_value in raw_quality.items():
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    quality[parameter] = value
            grade = self.sc.grade(grade_id) if grade_id else None
            if "moisture" not in raw_quality and grade is not None:
                material_class = str(stage or grade.material_class or "").lower()
                moisture = (grade.raw_moisture_pct if material_class.startswith("raw")
                            else grade.product_moisture_pct)
                if moisture is not None and math.isfinite(float(moisture)):
                    quality["moisture"] = float(moisture)
            if "moisture" in quality and not 0 <= quality["moisture"] <= 100:
                quality.pop("moisture")
            components.append((float(mass), quality))
        if not components:
            return {}
        parameters = set.intersection(*(set(q) for _, q in components))
        use_dry = "moisture" in parameters
        result = {}
        for parameter in parameters:
            weighted = [(mass * (1 - q["moisture"] / 100
                                 if use_dry and parameter != "moisture" else 1), q)
                        for mass, q in components]
            denominator = sum(mass for mass, _ in weighted)
            if denominator > 0:
                result[parameter] = sum(mass * q[parameter] for mass, q in weighted) / denominator
        return result

    @staticmethod
    def _add_chevron_quality_component(
            basis: Dict[str, object], physical_t: float,
            dry_fraction: float, quality: Dict[str, float]) -> None:
        physical_t = max(float(physical_t), 0.0)
        dry_t = physical_t * max(min(float(dry_fraction), 1.0), 0.0)
        basis["physical_t"] = float(basis.get("physical_t", 0.0)) \
            + physical_t
        basis["dry_t"] = float(basis.get("dry_t", 0.0)) + dry_t
        numerators = basis.setdefault("numerator_by_param", {})
        denominators = basis.setdefault("denominator_by_param", {})
        for parameter, raw_value in quality.items():
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            parameter_basis = physical_t if parameter == "moisture" else dry_t
            if parameter_basis <= 0:
                continue
            numerators[parameter] = float(numerators.get(parameter, 0.0)) \
                + value * parameter_basis
            denominators[parameter] = float(
                denominators.get(parameter, 0.0)) + parameter_basis

    def _initialise_chevron_quality_basis(self) -> None:
        """Restore carried chevron composition on its canonical mass basis."""
        for space in self.sc.spaces:
            state = chevron_campaign_state(self.sc, space.id)
            stacked = state.get("stacked_t_by_grade", {})
            if not isinstance(stacked, dict) or not stacked:
                continue
            positive = {
                str(grade_id): max(float(tonnage or 0.0), 0.0)
                for grade_id, tonnage in stacked.items()
                if float(tonnage or 0.0) > 0
            }
            total = sum(positive.values())
            current_mass = max(float(self.inv.get(space.id, 0.0)), 0.0)
            if total <= 0 or current_mass <= 0:
                continue
            # Reclaiming a homogeneous completed campaign preserves its
            # composition.  Scale carried construction tonnes to the remaining
            # physical inventory so later execution can continue consistently.
            scale = current_mass / total
            basis = self._blank_chevron_quality_basis()
            if space.id in self.sc.config.get(UNKNOWN_STOCK_QUALITY_KEY, []) \
                    and not space.quality:
                # A snapshot's explicitly unknown live assay takes precedence
                # over the campaign recipe's catalogue-derived composition.
                self._add_chevron_quality_component(basis, current_mass, 1.0, {})
                self._chevron_quality_basis[space.id] = basis
                self.space_quality[space.id] = {}
                continue
            if space.quality:
                # Timestamped/live pile assays survive a snapshot. Rebuilding
                # chemistry from catalogue grades here erased an offspec assay
                # whenever the simulation was resumed from carried inventory.
                observed = dict(space.quality)
                self._add_chevron_quality_component(
                    basis, current_mass,
                    self._grade_dry_fraction(space.grade, observed), observed)
                self._chevron_quality_basis[space.id] = basis
                self.space_quality[space.id] = self._quality_from_chevron_basis(basis)
                continue
            for grade_id, tonnage in positive.items():
                grade = self.sc.grade(grade_id)
                if grade is None:
                    continue
                component_quality = dict(grade.quality)
                self._add_chevron_quality_component(
                    basis, tonnage * scale,
                    self._grade_dry_fraction(grade_id, component_quality),
                    component_quality)
            quality = self._quality_from_chevron_basis(basis)
            if quality:
                self._chevron_quality_basis[space.id] = basis
                self.space_quality[space.id] = quality

    @staticmethod
    def _is_chevron_stack(job: Job) -> bool:
        if str(getattr(job, "kind", "") or "").lower() != "stack":
            return False
        method = str(getattr(job, "method", "") or "").lower()
        mode = str(getattr(job, "blend_mode", "") or "").lower()
        return method == "chevron" or mode in {
            "chevron stacking", "blend matrix", "optimized shares"}

    def _stack_quality(
            self, job: Job, old_mass_t: float, moved_t: float,
            incoming_quality: Dict[str, float]) -> Dict[str, float]:
        """Blend one stack movement on the correct campaign mass basis."""
        old_quality = self.space_quality.get(job.space, {})
        if not self._is_chevron_stack(job):
            space = self.sc.space(job.space)
            return self._mix_quality(
                old_mass_t, old_quality, moved_t, incoming_quality,
                old_grade=space.grade, incoming_grade=job.grade,
                old_stage=space.material_class)

        basis = self._chevron_quality_basis.get(job.space)
        if basis is None:
            basis = self._blank_chevron_quality_basis()
            # A new chevron pile is normally empty.  If imported state carries
            # untracked material, retain it using the best available aggregate
            # moisture instead of silently discarding its chemistry.
            if old_mass_t > 1e-9:
                self._add_chevron_quality_component(
                    basis, old_mass_t,
                    self._grade_dry_fraction(
                        self.sc.space(job.space).grade, old_quality),
                    old_quality)
            self._chevron_quality_basis[job.space] = basis
        component_quality = dict(incoming_quality)
        self._add_chevron_quality_component(
            basis, moved_t,
            self._grade_dry_fraction(job.grade, component_quality),
            component_quality)
        quality = self._quality_from_chevron_basis(basis)
        return quality

    def _consume_chevron_quality_basis(
            self, space_id: str, moved_t: float, old_mass_t: float) -> None:
        """Reduce a homogeneous blend basis proportionally when reclaimed."""
        basis = self._chevron_quality_basis.get(space_id)
        if basis is None or moved_t <= 0 or old_mass_t <= 0:
            return
        remaining_fraction = max(
            (float(old_mass_t) - float(moved_t)) / float(old_mass_t), 0.0)
        if remaining_fraction <= 1e-12:
            self._chevron_quality_basis.pop(space_id, None)
            return
        basis["physical_t"] = float(basis.get("physical_t", 0.0)) \
            * remaining_fraction
        basis["dry_t"] = float(basis.get("dry_t", 0.0)) \
            * remaining_fraction
        for key in ("numerator_by_param", "denominator_by_param"):
            values = basis.get(key, {})
            for parameter in list(values):
                values[parameter] = float(values[parameter]) \
                    * remaining_fraction

    def _event_minute(self, ev: SimEvent) -> int:
        """Resolve an event to an absolute minute from scenario start.

        Exact ISO timestamps are accepted for integration callers while the
        legacy bucket API remains unchanged.  The simulation engine operates
        at whole-minute resolution, matching ``JobExecutionWindow``.
        """
        if getattr(ev, "at_iso", ""):
            start = dtm.datetime.fromisoformat(
                self.sc.sim.start_iso.replace("Z", "+00:00"))
            at = dtm.datetime.fromisoformat(
                str(ev.at_iso).replace("Z", "+00:00"))
            if start.tzinfo is None and at.tzinfo is not None:
                start = start.replace(tzinfo=at.tzinfo)
            elif start.tzinfo is not None and at.tzinfo is None:
                at = at.replace(tzinfo=start.tzinfo)
            return int(round((at - start).total_seconds() / 60.0))
        minute = getattr(ev, "minute", None)
        if minute is not None:
            return int(minute)
        return int(ev.bucket) * self.bucket_width_min

    def _event_duration_min(self, ev: SimEvent) -> int:
        duration = getattr(ev, "duration_minutes", None)
        if duration is not None:
            return max(int(duration), 1)
        return max(int(ev.duration_buckets), 1) * self.bucket_width_min

    def _bucket_after_minute(self, minute: int) -> int:
        """Exclusive conservative bucket bound covering ``minute``."""
        return max(int(math.ceil(minute / self.bucket_width_min)), 0)

    def demand_tph(self, dest_id: str, bucket: int) -> float:
        if (dest_id, bucket) in self.demand_override:
            return self.demand_override[(dest_id, bucket)]
        return self.sc.demand_tph(dest_id, bucket)

    def _elapsed_demand_backlog(self) -> Dict[str, Dict[str, object]]:
        """Return executed-horizon demand not yet physically delivered.

        A replan starts at the current physical time, so demand in an elapsed
        native bucket would otherwise disappear even when an exact-minute
        outage stopped its job part-way through.  Reconstruct the outstanding
        order from authoritative demand less actual delivery.  Applying actual
        delivery FIFO to the elapsed grade campaigns retains the oldest
        outstanding grade without counting the completed prefix twice.
        """
        elapsed_minute = min(
            max(int(self.current_minute), 0), self.T * self.bucket_width_min)
        full_buckets, partial_minutes = divmod(
            elapsed_minute, self.bucket_width_min)
        backlog: Dict[str, Dict[str, object]] = {}
        for destination in self.sc.destinations:
            portions: List[Tuple[str, float]] = []
            for bucket in range(min(full_buckets, self.T)):
                mass = max(float(self.demand_tph(
                    destination.id, bucket)), 0.0) * self.dt
                if mass > 1e-9:
                    portions.append((
                        self.sc.demand_grade(destination.id, bucket), mass))
            if partial_minutes and full_buckets < self.T:
                mass = max(float(self.demand_tph(
                    destination.id, full_buckets)), 0.0) * \
                    partial_minutes / 60.0
                if mass > 1e-9:
                    portions.append((self.sc.demand_grade(
                        destination.id, full_buckets), mass))

            delivered_mass = sum(
                float(rate_tph) * self.dt
                for rate_tph in self.delivered.get(destination.id, ()))
            outstanding_by_grade: Dict[str, float] = {}
            for grade, requested_mass in portions:
                credited = min(delivered_mass, requested_mass)
                delivered_mass -= credited
                outstanding = requested_mass - credited
                if outstanding > 1e-6:
                    outstanding_by_grade[grade] = \
                        outstanding_by_grade.get(grade, 0.0) + outstanding
            total = sum(outstanding_by_grade.values())
            if total > 1e-6:
                backlog[destination.id] = {
                    "total_t": total,
                    "by_grade_t": outstanding_by_grade,
                }
        return backlog

    def machine_ok(self, mid: str, minute: Optional[int] = None) -> bool:
        now = self.current_minute if minute is None else int(minute)
        if now < self.machine_down_until_minute.get(mid, -1):
            return False
        return self.sc.machine(mid).available and \
            not self.sc.calendar_scope_blocked(mid, minute=now)

    def link_ok(self, lid: str, minute: Optional[int] = None) -> bool:
        now = self.current_minute if minute is None else int(minute)
        if now < self.link_down_until_minute.get(lid, -1):
            return False
        return self.sc.link(lid).available and not \
            self.sc.calendar_scope_blocked(f"LINK:{lid}", minute=now)

    def rate_factor(self, mid: str, minute: Optional[int] = None) -> float:
        now = self.current_minute if minute is None else int(minute)
        active = [factor for start, end, factor
                  in self.machine_rate_windows.get(mid, ())
                  if start <= now < end]
        if active:
            return min(active)
        if now >= self.machine_rate_factor_until_minute.get(mid, -1):
            return 1.0
        return self.machine_rate_factor.get(mid, 1.0)

    def comms_ok(self, minute: Optional[int] = None) -> bool:
        now = self.current_minute if minute is None else int(minute)
        return now >= self.comms_down_until_minute

    def downstream_ok(self, job: Job, minute: Optional[int] = None) -> bool:
        """Whether the receiving plant/process line used by ``job`` is up."""
        now = self.current_minute if minute is None else int(minute)
        for target, until in self.downstream_down_until_minute.items():
            if now >= until:
                continue
            if job.dest_id == target:
                return False
            try:
                destination = self.sc.destination(target)
            except StopIteration:
                continue
            stage = str(getattr(destination, "process_stage", "") or "")
            if stage and job.process_stage == stage:
                return False
        return True

    # -------------------------------------------------------------- events
    def inject(self, ev: SimEvent):
        self.events.append(ev)
        self.events.sort(key=self._event_minute)

    def _job_active_at_minute(self, job: Job, minute: int) -> bool:
        windows = validated_execution_windows(job)
        if windows:
            return any(w.start_minute <= minute
                       < w.start_minute + w.duration_min for w in windows)
        canonical_start = getattr(job, "start_minute", None)
        canonical_end = getattr(job, "end_minute", None)
        if canonical_start is not None and canonical_end is not None:
            return int(canonical_start) <= minute < int(canonical_end)
        start = job.start_bucket * self.bucket_width_min
        end = (job.end_bucket + 1) * self.bucket_width_min
        return start <= minute < end

    def _job_overlaps_interval(
            self, job: Job, start_minute: int, end_minute: int) -> bool:
        """Whether any physical material-work window intersects [start, end)."""
        windows = validated_execution_windows(job)
        if windows:
            return any(
                window.start_minute < end_minute and
                start_minute < window.start_minute + window.duration_min
                for window in windows)
        canonical_start = getattr(job, "start_minute", None)
        canonical_end = getattr(job, "end_minute", None)
        if canonical_start is not None and canonical_end is not None:
            return int(canonical_start) < end_minute and \
                start_minute < int(canonical_end)
        job_start = job.start_bucket * self.bucket_width_min
        job_end = (job.end_bucket + 1) * self.bucket_width_min
        return job_start < end_minute and start_minute < job_end

    def _bucket_path_shares(self, job: Job, bucket: int) -> List[Tuple[Tuple[str, ...], float]]:
        """Exact positive route shares for this bucket; no dominant-path fallback.

        A legacy aggregate-only job retains its declared path. When an exact
        profile exists, an absent/zero row means no route allocation.
        """
        if not job.bucket_flows:
            return [(tuple(job.path), 1.0)]
        masses: Dict[Tuple[str, ...], float] = {}
        for flow in job.bucket_flows:
            if flow.bucket == bucket and flow.tonnage_t > 1e-9:
                path = tuple(flow.path or job.path)
                masses[path] = masses.get(path, 0.0) + float(flow.tonnage_t)
        total = sum(masses.values())
        return [(path, mass / total) for path, mass in masses.items()] if total else []

    def _job_uses_link_during(self, job: Job, link_id: str,
                             start_minute: int, end_minute: int) -> bool:
        if not job.bucket_flows:
            return link_id in job.path
        return any(
            flow.tonnage_t > 1e-9 and link_id in (flow.path or job.path)
            and flow.bucket * self.bucket_width_min < end_minute
            and start_minute < (flow.bucket + 1) * self.bucket_width_min
            for flow in job.bucket_flows)

    def _route_capacity_t(self, shares, start_minute: int, end_minute: int,
                          interval_load: Dict[str, float]) -> float:
        """Limit the whole stream while preserving its allocated path ratios."""
        hours = (end_minute - start_minute) / 60.0
        fractions: Dict[str, float] = {}
        for path, share in shares:
            for link_id in path:
                fractions[link_id] = fractions.get(link_id, 0.0) + share
        maximum = float("inf")
        for link_id, fraction in fractions.items():
            link = self.sc.link(link_id)
            cap = self.sc.effective_resource_rate_tph(
                {f"LINK:{link_id}"}, link.capacity_tph,
                minute=start_minute, end_minute=end_minute) * hours
            maximum = min(maximum, max(cap - interval_load.get(link_id, 0.0), 0.0) / fraction)
        shared: Dict[str, List] = {}
        conflicts: Dict[str, List[str]] = {}
        for link in self.sc.links:
            if link.shared_group:
                shared.setdefault(link.shared_group, []).append(link)
            if link.conflict_group:
                conflicts.setdefault(link.conflict_group, []).append(link.id)
        for group, members in shared.items():
            member_ids = {link.id for link in members}
            # One material stream consumes a shared conveyor group once even
            # when its route traverses several links belonging to that group.
            fraction = sum(share for path, share in shares
                           if member_ids.intersection(path))
            if fraction > 0:
                cap = min(link.capacity_tph for link in members) * hours
                used = interval_load.get(f"SHARED:{group}", 0.0)
                maximum = min(maximum, max(cap - used, 0.0) / fraction)
        for members in conflicts.values():
            requested = {lid for lid in members if fractions.get(lid, 0.0) > 0}
            active = {lid for lid in members if interval_load.get(lid, 0.0) > 1e-9}
            if requested and len(requested | active) > 1:
                return 0.0
        return max(maximum, 0.0)

    def _record_route_flow(self, shares, bucket: int, moved_t: float,
                           interval_load: Dict[str, float]) -> None:
        link_shared = {link.id: link.shared_group for link in self.sc.links
                       if link.shared_group}
        for path, share in shares:
            for link_id in path:
                amount = moved_t * share
                interval_load[link_id] = interval_load.get(link_id, 0.0) + amount
                if link_id in self.link_load:
                    self.link_load[link_id][bucket] += amount / self.dt
            for group in {link_shared[link_id] for link_id in path
                          if link_id in link_shared}:
                key = f"SHARED:{group}"
                interval_load[key] = interval_load.get(key, 0.0) + moved_t * share

    def _dispatch_routes(self, job: Job, shares, bucket: int,
                          moved_t: float, quality: Dict[str, float],
                          material_id: str = "") -> None:
        for path, share in shares:
            link_id = path[-1] if path else ""
            lag = sum(self.sc.link(lid).transit_buckets for lid in path)
            self.belts.append(BeltParcel(
                link_id, bucket + lag, moved_t * share,
                job.grade or self.sc.space(job.space).grade, dict(quality), material_id))

    def _stack_routes(self, job: Job, shares, capacity_t: float,
                       arrivals: Dict[Tuple[str, str], BeltParcel],
                       material_id: str = "") -> float:
        by_source: Dict[Tuple[str, str], float] = {}
        for path, share in shares:
            exit_node = None
            for link_id in path:
                link = self.sc.link(link_id)
                if link.transit_buckets > 0:
                    exit_node = link.dst
            if exit_node is None and path:
                exit_node = self.sc.link(path[0]).src
            key = (exit_node, material_id or job.grade)
            by_source[key] = by_source.get(key, 0.0) + share
        available = min((arrivals[key].mass_t / share if key in arrivals else 0.0
                         for key, share in by_source.items()), default=0.0)
        free = self.sc.space(job.space).capacity_t - self.inv.get(job.space, 0.0)
        moved = max(min(capacity_t, available, free), 0.0)
        for key, share in by_source.items():
            parcel = arrivals.get(key)
            amount = moved * share
            if parcel is not None and amount > 0:
                parcel.mass_t -= amount
                old = self.inv.get(job.space, 0.0)
                self.space_quality[job.space] = self._stack_quality(
                    job, old, amount, parcel.quality)
                self.inv[job.space] = old + amount
        return moved

    def _material_available(self, sid: str, mid: str, minute: int) -> float:
        mass = self.material_mass.get(sid, {}).get(mid, 0.)
        pending = sum(float(part["tonnage_t"]) for part in
                      self.material_pending.get(sid, {}).get(mid, [])
                      if int(part["ready_minute"]) > minute)
        return max(mass - pending, 0.)

    def _add_material(self, sid: str, mid: str, amount: float, quality: dict) -> None:
        if sid not in self.material_mass:
            return  # Existing managed chevrons use their measured campaign quality.
        pools = self.material_mass[sid]
        old = pools.get(mid, 0.)
        self.material_assays.setdefault(sid, {})[mid] = self._mix_quality(
            old, self._material_quality(sid, mid), amount, quality)
        pools[mid] = old + amount

    def _material_quality(self, sid: str, mid: str) -> dict:
        return self.material_assays.get(sid, {}).get(mid, self.material_quality.get(mid, {}))

    def _sync_material_quality(self, sid: str) -> None:
        if sid not in self.material_mass:
            return
        mass, quality = 0., {}
        for mid, amount in self.material_mass[sid].items():
            if amount <= 1e-9:
                continue
            quality = self._mix_quality(mass, quality, amount, self._material_quality(sid, mid))
            mass += amount
        self.space_quality[sid] = quality

    def _material_assay_event(self, sid: str, parameters: dict) -> None:
        # A new measured pile assay supersedes planned component assays.
        # Apply it to every live pool; snapshotting gives changed pools new IDs.
        for mid, mass in self.material_mass.get(sid, {}).items():
            if mass > 1e-9:
                self.material_assays.setdefault(sid, {})[mid] = {
                    **self._material_quality(sid, mid), **parameters}

    def _material_inventory_event(self, sid: str) -> None:
        pools = self.material_mass.get(sid)
        if pools is None:
            return
        old = sum(pools.values())
        new = self.inv[sid]
        if old > 1e-9:
            ratio = new / old
            for mid in pools:
                pools[mid] *= ratio
                for part in self.material_pending.get(sid, {}).get(mid, []):
                    part["tonnage_t"] *= ratio
        elif new > 0:
            space = self.sc.space(sid)
            record = dict(grade_id=space.grade, quality=dict(self.space_quality.get(sid, {})),
                          material_class=space.material_class, treatment_id="", source_grade_id=space.grade)
            mid = washing._register(self.sc, self.material_catalog, record)
            self.material_quality[mid] = record["quality"]
            pools[mid] = new

    def _execute_material_job(self, job, bucket, start_minute, end_minute,
                              capacity_t, arrivals):
        rows = [row for row in job.material_flows if int(row["bucket"]) == bucket
                and float(row["tonnage_t"]) > 0]
        planned = sum(float(row["tonnage_t"]) for row in rows)
        if planned <= 0:
            return 0., 0., "no material allocation in bucket"
        source = job.source_space if job.transfer_id else job.space
        corridor_stack = job.kind == "stack" and not job.transfer_id
        if job.kind == "reclaim" and job.dest_id:
            mixed_mass, mixed_quality = 0.0, {}
            for row in rows:
                row_mass = float(row["tonnage_t"])
                row_quality = self._material_quality(
                    source, row["material_id"])
                mixed_quality = self._mix_quality(
                    mixed_mass, mixed_quality, row_mass, row_quality,
                    incoming_grade=str(row.get("grade", "")),
                    incoming_stage=str(row.get("material_class", "")))
                mixed_mass += row_mass
            issue = self._hard_delivery_quality_issue(
                job.dest_id, mixed_quality, bucket)
            if issue:
                return 0., 0., issue
        limit = capacity_t
        if not corridor_stack:
            limit = min(limit, self.inv.get(source, 0.))
            by_material = {}
            for row in rows:
                mid = row["material_id"]
                by_material[mid] = by_material.get(mid, 0.) + float(row["tonnage_t"]) / planned
            for mid, share in by_material.items():
                available = (self._material_available(source, mid, start_minute)
                             if source in self.material_mass else self.inv.get(source, 0.))
                limit = min(limit, available / share)
        if job.transfer_id:
            output_ratios = {}
            try:
                for index, row in enumerate(rows):
                    ratio = float(row["output_tonnage_t"]) / float(row["tonnage_t"])
                    actual_q = self._material_quality(source, row["material_id"])
                    if row.get("treatment_id"):
                        mode = washing.settings(self.sc)["modes"][row["treatment_id"]]
                        actual_output_q = washing.treated_quality(actual_q, mode)
                        actual_factor = washing.output_factor(actual_q, actual_output_q, mode["dry_solids_yield"])
                        planned_factor = washing.output_factor(row["quality"], row["output_quality"], mode["dry_solids_yield"])
                        ratio *= actual_factor / planned_factor
                    elif row.get("output_material_class") == "dry":
                        ratio *= (1 - actual_q["moisture"] / 100) / (1 - row["quality"]["moisture"] / 100)
                    output_ratios[index] = ratio
            except (ValueError, KeyError, ZeroDivisionError) as exc:
                return 0., 0., f"invalid actual treatment assay; dispatch held: {exc}"
            ratio = sum(float(row["tonnage_t"]) * output_ratios[index]
                        for index, row in enumerate(rows)) / planned
            free = max(self.sc.space(job.target_space).capacity_t - self.inv[job.target_space], 0.)
            limit = min(limit, free / ratio if ratio > 0 else 0.)
        elif job.kind == "stack":
            limit = min(limit, max(self.sc.space(job.space).capacity_t - self.inv[job.space], 0.))
            for row in rows:
                path = row.get("path", job.path)
                exit_node = next((self.sc.link(lid).dst for lid in reversed(path)
                                  if self.sc.link(lid).transit_buckets > 0),
                                 self.sc.link(path[0]).src if path else "")
                parcel = arrivals.get((exit_node, row["material_id"]))
                share = float(row["tonnage_t"]) / planned
                limit = min(limit, parcel.mass_t / share if parcel else 0.)
        limit = max(limit, 0.)
        moved, produced = 0., 0.
        for row_index, row in enumerate(rows):
            amount = limit * float(row["tonnage_t"]) / planned
            if amount <= 0:
                continue
            mid, omid = row["material_id"], row["output_material_id"]
            quality = (dict(self._material_quality(source, mid))
                       if source in self.material_mass else dict(self.space_quality.get(source, {})))
            if corridor_stack:
                part_job = copy.copy(job)
                part_job.grade = row["grade"]
                path = row.get("path", job.path)
                exit_node = next((self.sc.link(lid).dst for lid in reversed(path)
                                  if self.sc.link(lid).transit_buckets > 0), self.sc.link(path[0]).src)
                quality = dict(arrivals[(exit_node, mid)].quality)
                amount = self._stack_routes(part_job, [(path, 1.)], amount, arrivals, mid)
                self._add_material(job.space, mid, amount, quality)
                self._sync_material_quality(job.space)
            else:
                before = self.inv[source]
                self.inv[source] -= amount
                if source in self.material_mass:
                    self.material_mass[source][mid] = max(self.material_mass[source].get(mid, 0.) - amount, 0.)
                self._consume_chevron_quality_basis(source, amount, before)
                self._sync_material_quality(source)
                if job.transfer_id:
                    out = amount * output_ratios[row_index]
                    if row.get("treatment_id"):
                        mode = washing.settings(self.sc)["modes"][row["treatment_id"]]
                        output_quality = washing.treated_quality(quality, mode)
                        # Keep the planner's integer-tonne recovery, but recalculate assays.
                        self.actual_washing_flows.append(dict(row, tonnage_t=amount,
                            output_tonnage_t=out, quality=quality,
                            output_quality=output_quality, job_id=job.id,
                            machine=job.machine,
                            process_stage=job.process_stage,
                            source_space=job.source_space,
                            target_space=job.target_space))
                    else:
                        output_quality = dict(quality)
                        if row.get("output_material_class") == "dry":
                            output_quality["moisture"] = 0.
                    target = job.target_space
                    old = self.inv[target]
                    self.space_quality[target] = self._mix_quality(
                        old, self.space_quality.get(target, {}), out, output_quality)
                    self.inv[target] += out
                    self._add_material(target, omid, out, output_quality)
                    self._sync_material_quality(target)
                    if job.treatment_id and out > 0:
                        self.material_pending.setdefault(target, {}).setdefault(omid, []).append(
                            dict(ready_minute=end_minute + job.residence_min, tonnage_t=out))
                    produced += out
                    for dest in self.sc.destinations:
                        if getattr(dest, "demand_kind", "delivery") == "process_feed" \
                                and dest.process_stage == job.process_stage \
                                and (not self.sc.demand_grade(dest.id, bucket)
                                     or self.sc.demand_grade(dest.id, bucket) == row["grade"]):
                            self._deliver(dest.id, bucket, amount, quality,
                                          row["grade"], row.get("material_class", ""))
                elif job.kind == "reclaim":
                    self._deliver(job.dest_id, bucket, amount, quality,
                                  getattr(job, "certified_grade", "")
                                  or row["grade"],
                                  row.get("material_class", ""))
                else:
                    part_job = copy.copy(job)
                    part_job.grade = row["grade"]
                    self._dispatch_routes(part_job, [(row.get("path", job.path), 1.)],
                                          bucket, amount, quality, mid)
            moved += amount
        return moved, produced, "material or residence availability limited" if limit + 1e-7 < capacity_t else ""

    def _interrupt_jobs(self, ev: SimEvent, minute: int):
        """Close affected work while retaining its completed physical prefix."""
        event_end_minute = minute + self._event_duration_min(ev)
        for job in self.jobs.values():
            if job.status not in ("PLANNED", "DISPATCHED", "RUNNING"):
                continue
            affected = (
                ev.etype == "machine_failure" and job.machine == ev.target
            ) or (
                ev.etype == "reduced_rate" and job.machine == ev.target
            ) or (
                ev.etype in ("conveyor_trip", "route_unavailable")
                and self._job_uses_link_during(job, ev.target, minute, event_end_minute)
            ) or (
                ev.etype == "downstream_interruption"
                and not self._job_downstream_unaffected(job, ev.target)
            )
            # Invalidate both the currently running instruction and committed
            # future instructions that overlap the physical event window.
            # Otherwise a future lock can be frozen into the failed route and
            # make the recovery solve infeasible before it even starts.
            if not affected or not self._job_overlaps_interval(
                    job, minute, event_end_minute):
                continue
            completed = float(self.moved_by_job.get(job.id, 0.0))
            remaining = max(float(job.tonnage_t) - completed, 0.0)
            job.status = "CANCELLED"
            self.interrupted_jobs[job.id] = {
                "minute": minute,
                "reason": f"{ev.etype}:{ev.target}",
                "completed_t": completed,
                "remaining_t": remaining,
            }
            self._log(
                "INTERRUPT",
                f"job {job.id} interrupted at minute {minute}: "
                f"{completed:.1f} t complete, {remaining:.1f} t residual",
                job.machine, job=job.id, event_id=ev.id,
                completed_t=completed, remaining_t=remaining,
                event_minute=minute)

    def _job_downstream_unaffected(self, job: Job, target: str) -> bool:
        """Return false when ``job`` supplies the interrupted receiving unit."""
        if job.dest_id == target:
            return False
        try:
            destination = self.sc.destination(target)
        except StopIteration:
            return True
        stage = str(getattr(destination, "process_stage", "") or "")
        return not (stage and job.process_stage == stage)

    def _expire_temporary_state(self, minute: int):
        window_machines = set(self.machine_rate_windows)
        for mid, windows in list(self.machine_rate_windows.items()):
            remaining = [window for window in windows if minute < window[1]]
            if remaining:
                self.machine_rate_windows[mid] = remaining
                active = [factor for start, end, factor in remaining
                          if start <= minute < end]
                if active:
                    self.machine_rate_factor[mid] = min(active)
                    self.machine_rate_factor_until_minute[mid] = max(
                        end for start, end, _factor in remaining
                        if start <= minute < end)
                else:
                    self.machine_rate_factor.pop(mid, None)
                    self.machine_rate_factor_until_minute.pop(mid, None)
            else:
                self.machine_rate_windows.pop(mid, None)
                self.machine_rate_factor.pop(mid, None)
                self.machine_rate_factor_until_minute.pop(mid, None)
        # The legacy bucket event path may populate only these maps.
        for mid, until in list(self.machine_rate_factor_until_minute.items()):
            if mid not in window_machines and minute >= until:
                self.machine_rate_factor.pop(mid, None)
                self.machine_rate_factor_until_minute.pop(mid, None)

    def _apply_events_at(self, minute: int):
        self.current_minute = minute
        for ev in self.events:
            event_minute = self._event_minute(ev)
            key = (ev.id, event_minute)
            if event_minute != minute or key in self._applied_event_keys:
                continue
            self._applied_event_keys.add(key)
            end_minute = minute + self._event_duration_min(ev)
            t_end = self._bucket_after_minute(end_minute)
            if ev.etype == "machine_failure":
                self._interrupt_jobs(ev, minute)
                self.machine_down_until_minute[ev.target] = max(
                    end_minute,
                    self.machine_down_until_minute.get(ev.target, -1))
                self.machine_down_until[ev.target] = t_end
                self.pending_replan_reason = f"machine_failure:{ev.target}"
            elif ev.etype in ("conveyor_trip", "route_unavailable"):
                self._interrupt_jobs(ev, minute)
                self.link_down_until_minute[ev.target] = max(
                    end_minute,
                    self.link_down_until_minute.get(ev.target, -1))
                self.link_down_until[ev.target] = t_end
                self.pending_replan_reason = f"{ev.etype}:{ev.target}"
            elif ev.etype == "demand_change":
                first = minute // self.bucket_width_min
                for t in range(first, min(t_end, self.T)):
                    self.demand_override[(ev.target, t)] = ev.magnitude
                self.pending_replan_reason = f"demand_change:{ev.target}"
            elif ev.etype == "delayed_mine_dispatch":
                for parcel in self.belts:
                    if parcel.link_id == ev.target and \
                            parcel.arrive_bucket >= self.bucket:
                        parcel.arrive_bucket += int(ev.magnitude)
                self.pending_replan_reason = \
                    f"delayed_mine_dispatch:{ev.target}"
            elif ev.etype == "offspec_assay":
                quality = self.space_quality.setdefault(ev.target, {})
                for key_name, value in (ev.params or {}).items():
                    quality[key_name] = value
                self._material_assay_event(ev.target, ev.params or {})
                self.pending_replan_reason = f"offspec_assay:{ev.target}"
            elif ev.etype == "inventory_discrepancy":
                self.inv[ev.target] = max(
                    self.inv.get(ev.target, 0.0) + ev.magnitude, 0.0)
                self._material_inventory_event(ev.target)
                self.pending_replan_reason = \
                    f"inventory_discrepancy:{ev.target}"
            elif ev.etype == "operator_reject":
                job = self.jobs.get(ev.target)
                if job and job.status in ("PLANNED", "RUNNING", "DISPATCHED"):
                    completed = float(self.moved_by_job.get(job.id, 0.0))
                    job.status = "REJECTED"
                    self.interrupted_jobs[job.id] = {
                        "minute": minute,
                        "reason": f"operator_reject:{job.id}",
                        "completed_t": completed,
                        "remaining_t": max(job.tonnage_t - completed, 0.0),
                    }
                    self.banned.append((job.machine, job.space, job.dest_id,
                                        self.bucket, job.end_bucket))
                    self.pending_replan_reason = \
                        f"operator_reject:{ev.target}"
            elif ev.etype == "reduced_rate":
                # With automatic replanning, close the current instruction at
                # the exact derate instant so its completed prefix is retained
                # and the residual can be reallocated at the next cadence.
                # Replay-only simulations keep the instruction running and
                # apply the physical factor minute by minute.
                if self.auto_replan:
                    self._interrupt_jobs(ev, minute)
                factor = max(min(float(ev.magnitude), 1.0), 0.0)
                windows = self.machine_rate_windows.setdefault(ev.target, [])
                windows.append((minute, end_minute, factor))
                active = [window_factor for start, end, window_factor
                          in windows if start <= minute < end]
                self.machine_rate_factor[ev.target] = min(active)
                self.machine_rate_factor_until_minute[ev.target] = max(
                    end for start, end, _factor in windows
                    if start <= minute < end)
                self.pending_replan_reason = f"reduced_rate:{ev.target}"
            elif ev.etype == "downstream_interruption":
                self._interrupt_jobs(ev, minute)
                self.downstream_down_until_minute[ev.target] = max(
                    end_minute,
                    self.downstream_down_until_minute.get(ev.target, -1))
                self.downstream_down_until[ev.target] = t_end
                self.pending_replan_reason = \
                    f"downstream_interruption:{ev.target}"
            elif ev.etype == "comms_loss":
                self.comms_down_until_minute = max(
                    end_minute, self.comms_down_until_minute)
                self.comms_down_until = t_end
            if ev.etype != "comms_loss":
                self.pending_replan_minute = (
                    minute if self.pending_replan_minute is None
                    else min(self.pending_replan_minute, minute))
            self._log(
                "EVENT",
                f"{ev.etype} on {ev.target or '-'} at minute {minute} "
                f"(magnitude={ev.magnitude}, "
                f"duration={self._event_duration_min(ev)} min)",
                ev.target, etype=ev.etype, event_id=ev.id,
                event_minute=minute, end_minute=end_minute)

    def _apply_events_legacy(self):
        for ev in [e for e in self.events if e.bucket == self.bucket]:
            t_end = self.bucket + max(ev.duration_buckets, 1)
            if ev.etype == "machine_failure":
                self.machine_down_until[ev.target] = t_end
                self.pending_replan_reason = f"machine_failure:{ev.target}"
            elif ev.etype in ("conveyor_trip", "route_unavailable"):
                self.link_down_until[ev.target] = t_end
                self.pending_replan_reason = f"{ev.etype}:{ev.target}"
            elif ev.etype == "demand_change":
                for t in range(self.bucket, min(t_end, self.T)):
                    self.demand_override[(ev.target, t)] = ev.magnitude
                self.pending_replan_reason = f"demand_change:{ev.target}"
            elif ev.etype == "delayed_mine_dispatch":
                for p in self.belts:
                    if p.link_id == ev.target and p.arrive_bucket >= self.bucket:
                        p.arrive_bucket += int(ev.magnitude)
                self.pending_replan_reason = f"delayed_mine_dispatch:{ev.target}"
            elif ev.etype == "offspec_assay":
                q = self.space_quality.setdefault(ev.target, {})
                for k, v in (ev.params or {}).items():
                    q[k] = v
                self.pending_replan_reason = f"offspec_assay:{ev.target}"
            elif ev.etype == "inventory_discrepancy":
                self.inv[ev.target] = max(self.inv.get(ev.target, 0.0)
                                          + ev.magnitude, 0.0)
                self.pending_replan_reason = f"inventory_discrepancy:{ev.target}"
            elif ev.etype == "operator_reject":
                j = self.jobs.get(ev.target)
                if j and j.status in ("PLANNED", "RUNNING", "DISPATCHED"):
                    j.status = "REJECTED"
                    self.banned.append((j.machine, j.space, j.dest_id,
                                        self.bucket, j.end_bucket))
                    self.pending_replan_reason = f"operator_reject:{ev.target}"
            elif ev.etype == "reduced_rate":
                self.machine_rate_factor[ev.target] = ev.magnitude
                self.pending_replan_reason = f"reduced_rate:{ev.target}"
            elif ev.etype == "downstream_interruption":
                for t in range(self.bucket, min(t_end, self.T)):
                    self.demand_override[(ev.target, t)] = 0.0
                self.pending_replan_reason = f"downstream_interruption:{ev.target}"
            elif ev.etype == "comms_loss":
                self.comms_down_until = t_end
            self._log("EVENT", f"{ev.etype} on {ev.target or '—'} "
                      f"(magnitude={ev.magnitude}, {ev.duration_buckets} buckets)",
                      ev.target, etype=ev.etype)

    def _interval_job_rate(
            self, job: Job, start_minute: int,
            end_minute: int) -> Optional[float]:
        """Rate for a material-work interval, or ``None`` when not active."""
        windows = validated_execution_windows(job)
        if windows:
            for window in windows:
                if window.start_minute <= start_minute and end_minute <= \
                        window.start_minute + window.duration_min:
                    return float(window.rate_tph)
            return None
        canonical_start = getattr(job, "start_minute", None)
        canonical_end = getattr(job, "end_minute", None)
        if canonical_start is not None and canonical_end is not None:
            if int(canonical_start) <= start_minute and \
                    end_minute <= int(canonical_end):
                return float(job.rate_tph)
            return None
        envelope_start = job.start_bucket * self.bucket_width_min
        envelope_end = (job.end_bucket + 1) * self.bucket_width_min
        if envelope_start <= start_minute and end_minute <= envelope_end:
            return float(job.rate_tph)
        return None

    def _planned_bucket_mass(self, job: Job, bucket: int) -> float:
        flows = [flow for flow in getattr(job, "bucket_flows", [])
                 if flow.bucket == bucket]
        if getattr(job, "bucket_flows", []):
            return float(sum(flow.tonnage_t for flow in flows))
        windows = validated_execution_windows(job)
        if windows:
            b0 = bucket * self.bucket_width_min
            b1 = b0 + self.bucket_width_min
            total = 0.0
            for window in windows:
                overlap = max(
                    min(b1, window.start_minute + window.duration_min)
                    - max(b0, window.start_minute), 0)
                if overlap:
                    total += float(window.tonnage_t) * overlap / max(
                        window.duration_min, 1)
            return total
        return float(job.rate_tph) * self.dt

    def _planned_bucket_output(self, job: Job, bucket: int,
                               planned_t: float) -> float:
        flows = [flow for flow in getattr(job, "bucket_flows", [])
                 if flow.bucket == bucket]
        if flows:
            return float(sum(flow.output_tonnage_t for flow in flows))
        if job.tonnage_t > 0:
            return float(job.output_tonnage_t) * planned_t / job.tonnage_t
        return 0.0

    def _execute_job_interval(
            self, job: Job, bucket: int, start_minute: int, end_minute: int,
            interval_rate_tph: float, planned_remaining_t: float,
            planned_output_t: float, planned_bucket_t: float,
            arrivals: Dict[Tuple[str, str], BeltParcel],
            interval_load: Optional[Dict[str, float]] = None) \
            -> Tuple[float, float, str]:
        """Execute one uninterrupted half-open minute interval."""
        hours = max(end_minute - start_minute, 0) / 60.0
        if hours <= 0 or planned_remaining_t <= 0:
            return 0.0, 0.0, ""
        if job.execution_windows and not validated_execution_windows(job):
            # An explicitly failed timing refinement is non-dispatchable at
            # the ASP bridge. Replaying its bucket-average job would claim
            # service from work that cannot physically be released.
            return 0.0, 0.0, "execution timing unvalidated; dispatch held"
        if not self.machine_ok(job.machine, start_minute):
            return 0.0, 0.0, "machine unavailable"
        shares = self._bucket_path_shares(job, bucket)
        if not shares:
            return 0.0, 0.0, "no exact route allocation in bucket"
        if any(not self.link_ok(link_id, start_minute)
               for path, _share in shares for link_id in path):
            return 0.0, 0.0, "route link unavailable"
        if not self.downstream_ok(job, start_minute):
            return 0.0, 0.0, "receiving plant unavailable"
        blend_stack_issue = self._prepare_dynamic_blend_stack(job)
        if blend_stack_issue:
            return 0.0, 0.0, blend_stack_issue
        withdrawal_space = (job.source_space if job.transfer_id
                            else job.space if job.kind in ("reclaim", "dispatch")
                            else "")
        if withdrawal_space:
            blend_issue = self._blend_withdrawal_issue(
                withdrawal_space, start_minute, job)
            if blend_issue:
                return 0.0, 0.0, blend_issue
            if job.kind == "reclaim" and job.dest_id and not job.material_flows:
                actual_quality = self.space_quality.get(withdrawal_space, {})
                issue = self._hard_delivery_quality_issue(
                    job.dest_id, actual_quality, bucket)
                if issue:
                    return 0.0, 0.0, issue

        factor = self.rate_factor(job.machine, start_minute)
        effective_rate = min(
            interval_rate_tph * factor,
            self.sc.machine(job.machine).rate_max_tph * factor)
        scopes = {job.machine, f"SITE:{self.sc.machine(job.machine).site}"}
        if job.dest_id:
            scopes.add(f"LINE:{job.dest_id}")
        if job.process_stage:
            scopes.add(f"LINE:{job.process_stage}")
        effective_rate = self.sc.effective_resource_rate_tph(
            scopes, effective_rate, minute=start_minute, end_minute=end_minute)
        interval_load = interval_load if interval_load is not None else {}
        route_capacity = self._route_capacity_t(shares, start_minute, end_minute, interval_load)
        capacity_t = min(effective_rate * hours, planned_remaining_t, route_capacity)
        moved = 0.0
        output_moved = 0.0
        note = ""

        if job.material_flows:
            moved, output_moved, note = self._execute_material_job(
                job, bucket, start_minute, end_minute, capacity_t, arrivals)
        elif job.transfer_id and job.source_space and job.target_space:
            source_quality = dict(self.space_quality.get(job.source_space, {}))
            output_ratio = (planned_output_t / planned_bucket_t
                            if planned_bucket_t > 0 else 0.0)
            if output_ratio <= 0:
                return 0.0, 0.0, "invalid process output ratio"
            source_available = self.inv.get(job.source_space, 0.0)
            target_free = max(
                self.sc.space(job.target_space).capacity_t
                - self.inv.get(job.target_space, 0.0), 0.0)
            moved = min(capacity_t, source_available,
                        target_free / output_ratio)
            output_moved = moved * output_ratio
            self.inv[job.source_space] = source_available - moved
            self._consume_chevron_quality_basis(
                job.source_space, moved, source_available)
            if output_moved > 0:
                old = self.inv.get(job.target_space, 0.0)
                old_quality = self.space_quality.get(job.target_space, {})
                output_grade = self.sc.grade(job.output_grade)
                incoming_quality = (
                    dict(output_grade.quality) if output_grade else
                    dict(self.space_quality.get(job.source_space, {})))
                target = self.sc.space(job.target_space)
                self.space_quality[job.target_space] = self._mix_quality(
                    old, old_quality, output_moved, incoming_quality,
                    old_grade=target.grade, old_stage=target.material_class,
                    incoming_grade=job.output_grade or job.grade)
                self.inv[job.target_space] = old + output_moved
            for destination in self.sc.destinations:
                if getattr(destination, "demand_kind", "delivery") == "process_feed" \
                        and destination.process_stage == job.process_stage \
                        and (not self.sc.demand_grade(destination.id, bucket)
                             or self.sc.demand_grade(destination.id, bucket)
                             == job.grade):
                    self._deliver(destination.id, bucket, moved, source_quality,
                                  job.grade, self.sc.space(job.source_space).material_class)
        elif job.kind in ("reclaim", "dispatch"):
            source_available = self.inv.get(job.space, 0.0)
            moved = min(capacity_t, source_available)
            self.inv[job.space] = source_available - moved
            self._consume_chevron_quality_basis(
                job.space, moved, source_available)
            quality = dict(self.space_quality.get(job.space, {}))
            if job.kind == "reclaim":
                self._deliver(job.dest_id, bucket, moved, quality,
                              getattr(job, "certified_grade", "")
                              or job.grade,
                              self.sc.space(job.space).material_class)
            else:
                self._dispatch_routes(job, shares, bucket, moved, quality)
        elif job.kind == "stack":
            moved = self._stack_routes(job, shares, capacity_t, arrivals)

        self._record_route_flow(shares, bucket, moved, interval_load)
        self.moved_by_job[job.id] = self.moved_by_job.get(job.id, 0.0) + moved
        if moved > 0 and self._is_chevron_stack(job) and \
                chevron_construction_batch_t(self.sc, job.space) > 0:
            # Lock the matrix on the first actual movement and derive physical
            # completion continuously. A clock boundary alone never completes
            # material that was not stacked because of an outage/shortfall.
            update_chevron_campaign_state(
                self.sc, [job], {job.space: self.inv[job.space]},
                actual_tonnage_by_job=self.moved_by_job,
                completed_through_minute=end_minute)
        elif moved > 0 and withdrawal_space and \
                chevron_construction_batch_t(self.sc, withdrawal_space) > 0:
            update_chevron_campaign_state(
                self.sc, (), {withdrawal_space: self.inv[withdrawal_space]},
                completed_through_minute=end_minute)
        return moved, output_moved, note

    def _blend_withdrawal_issue(self, space_id: str, minute: int,
                              job: Optional[Job] = None) -> str:
        state = chevron_campaign_state(self.sc, space_id)
        managed = (chevron_enabled_for_space(self.sc, space_id)
                   or bool(state.get("stacked_t_by_grade")))
        if not managed:
            return ""
        if (not chevron_is_demand_driven(self.sc)
                and chevron_construction_batch_t(self.sc, space_id) <= 0):
            return ""
        if state.get("status") != CAMPAIGN_COMPLETE:
            return "blend construction incomplete; reclaim held"
        if chevron_is_demand_driven(self.sc) and job is not None:
            expected = str((getattr(job, "blend_campaign", {}) or {}).get(
                "campaign_id", "") or "")
            current = str(state.get("campaign_id", "") or "")
            if expected and current and expected != current:
                return "selected blend lot not constructed; reclaim held"
        completed = state.get("completed_minute")
        planned_ready = (0 if chevron_is_demand_driven(self.sc)
                         else chevron_ready_minute(self.sc, space_id))
        available = max(planned_ready,
                        int(completed) if completed is not None else 0)
        if minute < available:
            return "blend not yet ready; reclaim held"
        return ""

    def _execute_mine_arrivals_interval(
            self, bucket: int, minutes: float,
            start_minute: Optional[int] = None) -> None:
        """Share each receiver's free capacity across simultaneous raw grades.

        The managed prefix denotes external supply, so it must never drain an
        internal donor. Capacity rejection is explicit physical mass outside
        the receiving boundary, without carryover into a later interval.
        """
        hours = max(minutes, 0) / 60.0
        if hours <= 0:
            return
        grouped = {}
        for flow in self.sc.exogenous:
            if not flow.id.startswith(mine_arrivals.FLOW_PREFIX) or not (
                    flow.start_bucket <= bucket <= flow.end_bucket):
                continue
            if flow.source_space:
                raise ValueError("managed mine arrivals must be external supply")
            if not math.isfinite(flow.rate_tph) or flow.rate_tph < 0:
                raise ValueError("mine arrival rate must be finite and nonnegative")
            grade = self.sc.grade(flow.grade)
            if grade is None:
                raise ValueError(f"mine arrival has unknown grade {flow.grade}")
            target = self.sc.space(flow.target_space)
            if grade.material_class and target.material_class and \
                    grade.material_class != target.material_class:
                raise ValueError("mine arrival grade and target material classes differ")
            if self.material_catalog:
                pools = self.material_mass.get(flow.target_space)
                if pools is None or not math.isclose(
                        math.fsum(pools.values()),
                        self.inv.get(flow.target_space, 0.0),
                        abs_tol=1e-7, rel_tol=1e-9):
                    raise ValueError(
                        "mine arrival target material accounting differs "
                        "from physical inventory")
            grouped.setdefault(flow.target_space, []).append(
                (flow, flow.rate_tph * hours,
                 dict(flow.quality or grade.quality), grade))

        start = self.current_minute if start_minute is None else start_minute
        for target_id in sorted(grouped):
            rows = sorted(grouped[target_id], key=lambda row: row[0].id)
            target = self.sc.space(target_id)
            scheduled_total = math.fsum(row[1] for row in rows)
            if not math.isfinite(scheduled_total):
                raise ValueError("mine arrival interval tonnage must be finite")
            if scheduled_total <= 0:
                continue
            old = self.inv.get(target_id, 0.0)
            free = max(target.capacity_t - old, 0.0)
            accepted_total = min(scheduled_total, free)
            fraction = accepted_total / scheduled_total
            added = 0.0
            for index, (flow, scheduled, quality, grade) in enumerate(rows):
                accepted = (max(accepted_total - added, 0.0)
                            if index == len(rows) - 1
                            else scheduled * fraction)
                rejected = max(scheduled - accepted, 0.0)
                if accepted > 0:
                    self.space_quality[target_id] = self._mix_quality(
                        old + added, self.space_quality.get(target_id, {}),
                        accepted, quality, old_grade=target.grade,
                        old_stage=target.material_class,
                        incoming_grade=flow.grade,
                        incoming_stage=grade.material_class)
                    if self.material_catalog:
                        record = dict(
                            grade_id=flow.grade, quality=quality,
                            material_class=grade.material_class,
                            treatment_id="", source_grade_id=flow.grade)
                        mid = washing._register(
                            self.sc, self.material_catalog, record)
                        self.material_quality[mid] = quality
                        self._add_material(target_id, mid, accepted, quality)
                added += accepted
                # A native bucket can straddle midnight. The generated flow
                # ID retains its configured civil day even in that case.
                identity = flow.id[len(mine_arrivals.FLOW_PREFIX):].split(":")
                day = (int(identity[1])
                       if len(identity) == 4 and identity[1].isdigit()
                       else (self.sc.sim.minute_dt(start).date()
                             - self.sc.sim.bucket_dt(0).date()).days + 1)
                row = dict(
                    flow_id=flow.id, grade_id=flow.grade,
                    site=target.site, target_space=target_id, bucket=bucket,
                    day=day,
                    start_minute=start, end_minute=start + minutes,
                    scheduled_t=scheduled, accepted_t=accepted,
                    rejected_t=rejected, mass_basis="physical_t")
                self.actual_mine_arrivals.append(row)
                if rejected > 1e-7:
                    self._log(
                        "WARNING",
                        f"Mine arrival {flow.grade} at {target_id}: "
                        f"accepted {accepted:.3f} t of {scheduled:.3f} t; "
                        f"rejected {rejected:.3f} t (receiving buffer capacity)",
                        target_id, minute=start, **row)
            self.inv[target_id] = (target.capacity_t
                                   if 0 < free <= scheduled_total
                                   else old + accepted_total)
            if self.material_catalog:
                self._sync_material_quality(target_id)

    def _execute_exogenous_interval(self, bucket: int, minutes: int):
        hours = max(minutes, 0) / 60.0
        if hours <= 0:
            return
        for flow in self.sc.exogenous:
            if flow.id.startswith(mine_arrivals.FLOW_PREFIX):
                continue
            if not (flow.start_bucket <= bucket <= flow.end_bucket):
                continue
            amount = flow.rate_tph * hours
            if flow.source_space:
                amount = min(amount, self.inv.get(flow.source_space, 0.0))
            free = self.sc.space(flow.target_space).capacity_t \
                - self.inv.get(flow.target_space, 0.0)
            amount = min(amount, max(free, 0.0))
            if amount <= 0:
                continue
            if flow.source_space:
                old_source = self.inv[flow.source_space]
                self.inv[flow.source_space] -= amount
                self._consume_chevron_quality_basis(
                    flow.source_space, amount, old_source)
            if flow.source_space:
                incoming_quality = dict(
                    self.space_quality.get(flow.source_space, {}))
            elif flow.quality:
                incoming_quality = dict(flow.quality)
            else:
                grade = self.sc.grade(flow.grade)
                incoming_quality = dict(grade.quality) if grade else {}
            old = self.inv.get(flow.target_space, 0.0)
            old_quality = self.space_quality.get(flow.target_space, {})
            target = self.sc.space(flow.target_space)
            source = self.sc.space(flow.source_space) if flow.source_space else None
            self.space_quality[flow.target_space] = self._mix_quality(
                old, old_quality, amount, incoming_quality,
                old_grade=target.grade, old_stage=target.material_class,
                incoming_grade=source.grade if source else flow.grade,
                incoming_stage=source.material_class if source else "")
            self.inv[flow.target_space] = old + amount
            if self.material_catalog:
                if flow.source_space in self.material_mass:
                    pools = self.material_mass[flow.source_space]
                    matches = [mid for mid in pools if
                               self.material_catalog[mid]["grade_id"] == flow.grade and pools[mid] > 0]
                    remaining = amount
                    for mid in matches:
                        part = min(pools[mid], remaining)
                        pools[mid] -= part
                        self._add_material(flow.target_space, mid, part,
                                           self._material_quality(flow.source_space, mid))
                        remaining -= part
                    if remaining > 1e-7:
                        raise ValueError("exogenous source material accounting differs from physical inventory")
                    self._sync_material_quality(flow.source_space)
                else:
                    grade = self.sc.grade(flow.grade)
                    record = dict(grade_id=flow.grade, quality=incoming_quality,
                        material_class=grade.material_class if grade else "",
                        treatment_id="", source_grade_id=flow.grade)
                    mid = washing._register(self.sc, self.material_catalog, record)
                    self.material_quality[mid] = incoming_quality
                    self._add_material(flow.target_space, mid, amount, incoming_quality)
                self._sync_material_quality(flow.target_space)
        self._execute_mine_arrivals_interval(bucket, minutes)

    def _job_physical_end_minute(self, job: Job) -> int:
        windows = validated_execution_windows(job)
        if windows:
            return max(window.start_minute + window.duration_min
                       for window in windows)
        canonical_end = getattr(job, "end_minute", None)
        if canonical_end is not None:
            return int(canonical_end)
        return (job.end_bucket + 1) * self.bucket_width_min

    def _job_physical_start_minute(self, job: Job) -> int:
        windows = validated_execution_windows(job)
        if windows:
            return min(window.start_minute for window in windows)
        canonical_start = getattr(job, "start_minute", None)
        if canonical_start is not None:
            return int(canonical_start)
        return job.start_bucket * self.bucket_width_min

    def _finish_elapsed_jobs(self, minute: int):
        for job in self.jobs.values():
            if job.status == "RUNNING" and \
                    self._job_physical_end_minute(job) <= minute:
                job.status = "FINISHED"

    def _mixed_enabled(self) -> bool:
        return bool(getattr(self.sc.sim, "mixed_resolution_enabled", False))

    def _maybe_replan_at_cadence(self):
        """Run the authoritative rolling solve on an exact fine-grid origin."""
        if not self._mixed_enabled():
            return
        cadence = max(int(getattr(
            self.sc.sim, "replan_interval_min", 0) or
            self.bucket_width_min), 1)
        if self.current_minute < self.next_replan_minute:
            return
        due = self.next_replan_minute
        while self.next_replan_minute <= self.current_minute:
            self.next_replan_minute += cadence
        if not self.auto_replan or self.current_minute >= \
                self.T * self.bucket_width_min:
            return
        reason = self.pending_replan_reason or \
            f"rolling interval:{due}min"
        self.replan(reason=reason, start_minute=self.current_minute)
        self.last_replan_minute = self.current_minute
        self.pending_replan_reason = ""
        self.pending_replan_minute = None

    # -------------------------------------------------------------- stepping
    def _step_bucket_legacy(self) -> bool:
        """Advance one bucket. Returns False when the horizon is exhausted."""
        if self.bucket >= self.T:
            return False
        t = self.bucket
        self._apply_events_legacy()
        self.down_series.append(
            [m for m, du in self.machine_down_until.items() if t < du])
        self.link_down_series.append(
            [l for l, du in self.link_down_until.items() if t < du])

        # arrivals available this bucket, per (exit node, grade)
        arr: Dict[Tuple[str, str], BeltParcel] = {}
        for p in self.belts:
            if p.arrive_bucket == t and p.mass_t > 0:
                key = (self.sc.link(p.link_id).dst, p.grade)
                if key in arr:
                    tot = arr[key].mass_t + p.mass_t
                    arr[key].quality = self._mix_quality(
                        arr[key].mass_t, arr[key].quality, p.mass_t, p.quality,
                        old_grade=arr[key].grade, incoming_grade=p.grade)
                    arr[key].mass_t = tot
                else:
                    arr[key] = BeltParcel(p.link_id, t, p.mass_t, p.grade,
                                          dict(p.quality))

        deviation = False
        interval_load: Dict[str, float] = {}
        for j in sorted(self.jobs.values(), key=lambda x: x.id):
            if not (j.start_bucket <= t <= j.end_bucket) or \
                    j.status in ("CANCELLED", "REJECTED", "FINISHED"):
                continue
            if j.status == "PLANNED":
                j.status = "RUNNING"
                self._log("DISPATCH", f"job {j.id} started ({j.kind} {j.space}"
                          + (f" -> {j.dest_id}" if j.dest_id else "") + ")", j.machine)
            bucket_flows = [
                flow for flow in getattr(j, "bucket_flows", [])
                if flow.bucket == t
            ]
            planned_t = self._planned_bucket_mass(j, t)
            shares = self._bucket_path_shares(j, t)
            if planned_t <= 0 or not shares:
                continue
            moved = 0.0
            output_moved = 0.0
            note = ""
            if t < self.machine_down_until.get(j.machine, -1) or \
                    not self.machine_ok(j.machine, t * self.bucket_width_min):
                note = "machine unavailable"
            elif any(t < self.link_down_until.get(li, -1) or
                     not self.link_ok(li, t * self.bucket_width_min)
                     for path, _share in shares for li in path):
                note = "route link unavailable"
            else:
                eff_rate = min(j.rate_tph * self.rate_factor(j.machine),
                               self.sc.machine(j.machine).rate_max_tph
                               * self.rate_factor(j.machine))
                scopes = {j.machine, f"SITE:{self.sc.machine(j.machine).site}"}
                if j.dest_id:
                    scopes.add(f"LINE:{j.dest_id}")
                if j.process_stage:
                    scopes.add(f"LINE:{j.process_stage}")
                eff_rate = self.sc.effective_resource_rate_tph(scopes, eff_rate, bucket=t)
                cap_t = min(eff_rate * self.dt, planned_t, self._route_capacity_t(
                    shares, t * self.bucket_width_min, (t + 1) * self.bucket_width_min,
                    interval_load))
                if j.transfer_id and j.source_space and j.target_space:
                    # Internal process jobs carry physical tonnes at both
                    # stages.  The optimiser records the exact physical output
                    # after beneficiation/drying; replay preserves that ratio
                    # instead of treating the job as an ordinary reclaim or
                    # stack and losing the target-side mass balance.
                    planned_output_t = (
                        sum(flow.output_tonnage_t for flow in bucket_flows)
                        if bucket_flows else
                        (j.output_tonnage_t * planned_t / j.tonnage_t
                         if j.tonnage_t > 0 else 0.0))
                    output_ratio = (
                        planned_output_t / planned_t if planned_t > 0 else 0.0)
                    if output_ratio <= 0:
                        note = "invalid process output ratio"
                    else:
                        source_available = self.inv.get(j.source_space, 0.0)
                        target_free = max(
                            self.sc.space(j.target_space).capacity_t
                            - self.inv.get(j.target_space, 0.0), 0.0)
                        moved = min(
                            cap_t, source_available,
                            target_free / output_ratio)
                        output_moved = moved * output_ratio
                        self.inv[j.source_space] = source_available - moved
                        self._consume_chevron_quality_basis(
                            j.source_space, moved, source_available)
                        if output_moved > 0:
                            old = self.inv.get(j.target_space, 0.0)
                            old_quality = self.space_quality.get(
                                j.target_space, {})
                            output_grade = self.sc.grade(j.output_grade)
                            incoming_quality = (
                                dict(output_grade.quality) if output_grade else
                                dict(self.space_quality.get(j.source_space, {})))
                            target = self.sc.space(j.target_space)
                            self.space_quality[j.target_space] = self._mix_quality(
                                old, old_quality, output_moved, incoming_quality,
                                old_grade=target.grade, old_stage=target.material_class,
                                incoming_grade=j.output_grade or j.grade)
                            self.inv[j.target_space] = old + output_moved
                elif j.kind in ("reclaim", "dispatch"):
                    source_available = self.inv.get(j.space, 0.0)
                    moved = min(cap_t, source_available)
                    self.inv[j.space] = source_available - moved
                    self._consume_chevron_quality_basis(
                        j.space, moved, source_available)
                    q = dict(self.space_quality.get(j.space, {}))
                    if j.kind == "reclaim":
                        self._deliver(j.dest_id, t, moved, q,
                                      getattr(j, "certified_grade", "")
                                      or j.grade,
                                      self.sc.space(j.space).material_class)
                    else:  # dispatch onto corridor
                        self._dispatch_routes(j, shares, t, moved, q)
                elif j.kind == "stack":
                    moved = self._stack_routes(j, shares, cap_t, arr)
            self._record_route_flow(shares, t, moved, interval_load)
            self.moved_by_job[j.id] = self.moved_by_job.get(j.id, 0.0) + moved
            self.activity_log.append({
                "bucket": t, "job": j.id, "machine": j.machine,
                "kind": j.kind, "space": j.space, "dest_id": j.dest_id,
                "grade": j.grade, "moved_t": round(moved, 1),
                "output_t": round(output_moved, 1),
                "rate_tph": round(moved / self.dt, 1), "note": note})
            self.machine_active[j.machine][t] = 1

            # travel & starts
            prev = self._prev_space.get(j.machine)
            sp = self.sc.space(j.space)
            target_x = ((j.start_x + j.stop_x) / 2
                        if j.stop_x > j.start_x
                        else (sp.x_from + sp.x_to) / 2)
            # Same-pile jobs may be at different repères.  Move to the
            # current work-range midpoint instead of the generic pile centre.
            if prev != j.space or t == j.start_bucket:
                self.travel_m[j.machine] += abs(
                    self.machine_pos[j.machine] - target_x)
                self.machine_pos[j.machine] = target_x
                self.starts_stops[j.machine] += 1
                self._prev_space[j.machine] = j.space

            if t == j.end_bucket and j.status == "RUNNING":
                j.status = "FINISHED"
            if self.comms_ok():
                self.job_updates.append(JobUpdate(
                    job_id=j.id, bucket=t, status=j.status, moved_t=round(moved, 1),
                    effective_rate_tph=round(moved / self.dt, 1),
                    machine_position_x=self.machine_pos[j.machine],
                    quality=dict(self.space_quality.get(j.space, {})),
                    forecast_end_bucket=j.end_bucket, note=note))
                if moved < planned_t * (1 - self.sc.sim.deviation_tolerance):
                    deviation = True
                    self._log("DEVIATION", f"job {j.id}: moved {moved:.0f} t vs planned "
                              f"{planned_t:.0f} t ({note or 'shortfall'})", j.machine,
                              job=j.id, moved=moved, planned=planned_t)
            elif note or moved < planned_t * (1 - self.sc.sim.deviation_tolerance):
                self._log("INFO", f"comms loss: deviation on {j.id} NOT reported "
                          "(stale data)", j.machine)

        # exogenous flows (manual payloaders, unmodelled washing output, ...)
        for e in self.sc.exogenous:
            if e.id.startswith(mine_arrivals.FLOW_PREFIX):
                continue
            if e.start_bucket <= t <= e.end_bucket:
                amt = e.rate_tph * self.dt
                if e.source_space:
                    amt = min(amt, self.inv.get(e.source_space, 0.0))
                    self.inv[e.source_space] -= amt
                free = self.sc.space(e.target_space).capacity_t \
                    - self.inv.get(e.target_space, 0.0)
                amt = min(amt, max(free, 0.0))
                if amt > 0:
                    # incoming material updates pile quality by mass-weighted
                    # blending (defect fixed 18-Jul-2026: inflow previously
                    # added mass while silently keeping the old quality).
                    # A flow without an explicit vector uses its declared
                    # grade's vector — same convention as belt parcels; a
                    # source-space transfer carries the source pile quality.
                    if e.source_space:
                        fq = dict(self.space_quality.get(e.source_space, {}))
                    elif e.quality:
                        fq = dict(e.quality)
                    else:
                        g = self.sc.grade(e.grade)
                        fq = dict(g.quality) if g else {}
                    old = self.inv.get(e.target_space, 0.0)
                    oq = self.space_quality.get(e.target_space, {})
                    target = self.sc.space(e.target_space)
                    source = self.sc.space(e.source_space) if e.source_space else None
                    self.space_quality[e.target_space] = self._mix_quality(
                        old, oq, amt, fq,
                        old_grade=target.grade, old_stage=target.material_class,
                        incoming_grade=source.grade if source else e.grade,
                        incoming_stage=source.material_class if source else "")
                    self.inv[e.target_space] = old + amt

        self._execute_mine_arrivals_interval(
            t, self.dt * 60, start_minute=t * self.bucket_width_min)

        # unstacked arrivals left over -> lost feed alarm (belts cannot hold)
        for key, parcel in arr.items():
            self.unstacked_t += max(parcel.mass_t, 0.0)
            if parcel.mass_t > 1.0:
                self._log("DEVIATION", f"{parcel.mass_t:.0f} t of {key[1]} arrived at "
                          f"{key[0]} and was NOT stacked (no stacker capacity)", key[0])
                deviation = True
        self.belts = [p for p in self.belts if not (p.arrive_bucket <= t)]

        for s in self.sc.spaces:
            self.inv_series[s.id].append(self.inv[s.id])
            self.quality_series[s.id].append(
                dict(self.space_quality.get(s.id, {})))

        self.bucket += 1
        if deviation and self.comms_ok() and self.auto_replan \
                and self.sc.sim.replan_on_deviation and self.bucket < self.T:
            self.replan(reason=self.pending_replan_reason or "execution deviation")
        elif self.pending_replan_reason and self.auto_replan and self.bucket < self.T:
            self.replan(reason=self.pending_replan_reason)
        self.pending_replan_reason = ""
        return self.bucket < self.T

    def _advance_current_bucket(self, stop_minute: int) -> bool:
        """Advance part or all of one bucket using exact minute intervals.

        The optimiser's bucket remains the state-commit boundary.  Within it,
        validated execution windows and event timestamps are authoritative for
        physical movement.  A contingency therefore preserves material moved
        before the event and replans only after that partial state is committed.
        """
        if self.bucket >= self.T:
            return False
        bucket = self.bucket
        bucket_start = bucket * self.bucket_width_min
        bucket_end = bucket_start + self.bucket_width_min
        start_minute = max(self.current_minute, bucket_start)
        end_minute = min(max(int(stop_minute), start_minute), bucket_end)
        self.current_minute = start_minute

        # Arrivals retain their native solver-bucket contract.  Their mass is
        # consumed progressively by stack windows during this bucket.
        if self._bucket_runtime is None:
            arrivals: Dict[Tuple[str, str], BeltParcel] = {}
            for parcel in self.belts:
                if parcel.arrive_bucket != bucket or parcel.mass_t <= 0:
                    continue
                key = (self.sc.link(parcel.link_id).dst, parcel.material_id or parcel.grade)
                if key in arrivals:
                    existing = arrivals[key]
                    total = existing.mass_t + parcel.mass_t
                    existing.quality = self._mix_quality(
                        existing.mass_t, existing.quality, parcel.mass_t, parcel.quality,
                        old_grade=existing.grade, incoming_grade=parcel.grade)
                    existing.mass_t = total
                else:
                    arrivals[key] = BeltParcel(
                        parcel.link_id, bucket, parcel.mass_t, parcel.grade,
                        dict(parcel.quality), parcel.material_id)
            self._bucket_runtime = {
                "bucket": bucket,
                "arrivals": arrivals,
                "stats": {},
                "down_seen": set(),
                "links_down_seen": set(),
                "deviation": False,
            }
        runtime = self._bucket_runtime
        arrivals = runtime["arrivals"]

        boundaries = {start_minute, end_minute}
        for event in self.events:
            event_minute = self._event_minute(event)
            if start_minute <= event_minute < end_minute:
                boundaries.add(event_minute)
            recovery_minute = event_minute + self._event_duration_min(event)
            if start_minute < recovery_minute < end_minute:
                boundaries.add(recovery_minute)
        for until in list(self.machine_down_until_minute.values()) + \
                list(self.link_down_until_minute.values()) + \
                list(self.downstream_down_until_minute.values()) + \
                list(self.machine_rate_factor_until_minute.values()):
            if start_minute < until < end_minute:
                boundaries.add(until)
        for windows in self.machine_rate_windows.values():
            for _window_start, window_end, _factor in windows:
                if start_minute < window_end < end_minute:
                    boundaries.add(window_end)
        for entry in self.sc.calendars:
            calendar_start, calendar_end = self.sc.calendar_interval_minutes(entry)
            for boundary in (calendar_start, calendar_end):
                if start_minute < boundary < end_minute:
                    boundaries.add(boundary)
        for job in self.jobs.values():
            canonical_start = getattr(job, "start_minute", None)
            canonical_end = getattr(job, "end_minute", None)
            if canonical_start is not None and \
                    start_minute < canonical_start < end_minute:
                boundaries.add(int(canonical_start))
            if canonical_end is not None and \
                    start_minute < canonical_end < end_minute:
                boundaries.add(int(canonical_end))
            for window in validated_execution_windows(job):
                window_end = window.start_minute + window.duration_min
                if start_minute < window.start_minute < end_minute:
                    boundaries.add(window.start_minute)
                if start_minute < window_end < end_minute:
                    boundaries.add(window_end)
        ordered = sorted(boundaries)

        stats = runtime["stats"]
        down_seen = runtime["down_seen"]
        links_down_seen = runtime["links_down_seen"]
        deviation = bool(runtime["deviation"])

        for index in range(len(ordered) - 1):
            interval_start, interval_end = ordered[index], ordered[index + 1]
            self.current_minute = interval_start
            self._expire_temporary_state(interval_start)
            self._apply_events_at(interval_start)

            down_seen.update(
                mid for mid, until in self.machine_down_until_minute.items()
                if interval_start < until)
            links_down_seen.update(
                link_id for link_id, until in self.link_down_until_minute.items()
                if interval_start < until)

            interval_load: Dict[str, float] = {}
            for job in sorted(self.jobs.values(), key=lambda item: item.id):
                if job.status in ("CANCELLED", "REJECTED", "FINISHED"):
                    continue
                rate = self._interval_job_rate(
                    job, interval_start, interval_end)
                if rate is None:
                    continue
                planned = self._planned_bucket_mass(job, bucket)
                if planned <= 0:
                    continue
                stat = stats.setdefault(job.id, {
                    "planned_t": planned,
                    "planned_output_t": self._planned_bucket_output(
                        job, bucket, planned),
                    "moved_t": 0.0,
                    "output_t": 0.0,
                    "notes": [],
                    "started": False,
                    "active_minutes": 0,
                    "first_minute": interval_start,
                    "last_minute": interval_start,
                })
                if job.status == "PLANNED":
                    job.status = "RUNNING"
                    self._log(
                        "DISPATCH",
                        f"job {job.id} started at minute {interval_start} "
                        f"({job.kind} {job.space}"
                        + (f" -> {job.dest_id}" if job.dest_id else "")
                        + ")", job.machine, job=job.id,
                        start_minute=interval_start)
                if not stat["started"]:
                    space = self.sc.space(job.space)
                    target_x = ((job.start_x + job.stop_x) / 2
                                if job.stop_x > job.start_x
                                else (space.x_from + space.x_to) / 2)
                    previous_space = self._prev_space.get(job.machine)
                    if previous_space != job.space or \
                            interval_start == job.start_bucket \
                            * self.bucket_width_min:
                        self.travel_m[job.machine] += abs(
                            self.machine_pos[job.machine] - target_x)
                        self.machine_pos[job.machine] = target_x
                        self.starts_stops[job.machine] += 1
                        self._prev_space[job.machine] = job.space
                    stat["started"] = True
                    stat["first_minute"] = interval_start

                remaining = max(
                    float(stat["planned_t"]) - float(stat["moved_t"]), 0.0)
                moved, output_moved, note = self._execute_job_interval(
                    job, bucket, interval_start, interval_end, rate, remaining,
                    float(stat["planned_output_t"]),
                    float(stat["planned_t"]), arrivals, interval_load)
                stat["active_minutes"] = int(stat["active_minutes"]) + \
                    interval_end - interval_start
                stat["moved_t"] = float(stat["moved_t"]) + moved
                stat["output_t"] = float(stat["output_t"]) + output_moved
                stat["last_minute"] = interval_end
                if note and note not in stat["notes"]:
                    stat["notes"].append(note)

            self._execute_exogenous_interval(
                bucket, interval_end - interval_start)

        self.current_minute = end_minute
        self._expire_temporary_state(end_minute)
        runtime["deviation"] = deviation
        if end_minute < bucket_end:
            return self.current_minute < self.T * self.bucket_width_min
        self.down_series.append(sorted(down_seen))
        self.link_down_series.append(sorted(links_down_seen))

        for job_id, stat in stats.items():
            job = self.jobs[job_id]
            moved = float(stat["moved_t"])
            planned = float(stat["planned_t"])
            output_moved = float(stat["output_t"])
            active_minutes = max(int(stat["active_minutes"]), 1)
            effective_rate = moved * 60.0 / active_minutes
            notes = list(stat["notes"])
            note = "; ".join(notes)
            if moved > 0:
                self.machine_active[job.machine][bucket] = 1
            if bucket == job.end_bucket and job.status == "RUNNING":
                job.status = "FINISHED"
            self.activity_log.append({
                "bucket": bucket,
                "start_minute": int(stat["first_minute"]),
                "end_minute": int(stat["last_minute"]),
                "job": job.id,
                "machine": job.machine,
                "kind": job.kind,
                "space": job.space,
                "dest_id": job.dest_id,
                "grade": job.grade,
                "moved_t": round(moved, 1),
                "output_t": round(output_moved, 1),
                "rate_tph": round(effective_rate, 1),
                "bucket_average_tph": round(moved / self.dt, 1),
                "active_minutes": active_minutes,
                "note": note,
            })
            if self.comms_ok(end_minute - 1):
                self.job_updates.append(JobUpdate(
                    job_id=job.id, bucket=bucket, status=job.status,
                    moved_t=round(moved, 1),
                    effective_rate_tph=round(effective_rate, 1),
                    machine_position_x=self.machine_pos[job.machine],
                    quality=dict(self.space_quality.get(job.space, {})),
                    forecast_end_bucket=job.end_bucket, note=note))
                if moved < planned * (1 - self.sc.sim.deviation_tolerance):
                    deviation = True
                    self._log(
                        "DEVIATION",
                        f"job {job.id}: moved {moved:.0f} t vs planned "
                        f"{planned:.0f} t ({note or 'interrupted/shortfall'})",
                        job.machine, job=job.id, moved=moved,
                        planned=planned)
            elif note or moved < planned * \
                    (1 - self.sc.sim.deviation_tolerance):
                self._log(
                    "INFO",
                    f"comms loss: deviation on {job.id} NOT reported "
                    "(stale data)", job.machine)

        # A hard event at the bucket start may cancel a job before it enters
        # ``stats``.  The event's pending reason still guarantees a replan.
        for key, parcel in arrivals.items():
            self.unstacked_t += max(parcel.mass_t, 0.0)
            if parcel.mass_t > 1.0:
                self._log(
                    "DEVIATION",
                    f"{parcel.mass_t:.0f} t of {key[1]} arrived at {key[0]} "
                    "and was NOT stacked (no stacker capacity)", key[0])
                deviation = True
        self.belts = [parcel for parcel in self.belts
                      if parcel.arrive_bucket > bucket]

        for space in self.sc.spaces:
            self.inv_series[space.id].append(self.inv[space.id])
            self.quality_series[space.id].append(
                dict(self.space_quality.get(space.id, {})))

        self.bucket += 1
        self._bucket_runtime = None
        if not self._mixed_enabled() and deviation and \
                self.comms_ok(end_minute) and self.auto_replan \
                and self.sc.sim.replan_on_deviation and self.bucket < self.T:
            self.replan(
                reason=self.pending_replan_reason or "execution deviation")
        elif not self._mixed_enabled() and self.pending_replan_reason \
                and self.auto_replan \
                and self.bucket < self.T:
            self.replan(reason=self.pending_replan_reason)
        if not self._mixed_enabled():
            self.pending_replan_reason = ""
            self.pending_replan_minute = None
        return self.bucket < self.T

    def advance_minutes(self, minutes: Optional[int] = None) -> bool:
        """Advance live physical state without forcing a native-hour jump.

        In mixed mode the default is the configured near-term step and every
        requested range is split at the rolling replan cadence.  Non-mixed
        callers retain the old behaviour by defaulting to the rest of the
        current native bucket.
        """
        if self.current_minute >= self.T * self.bucket_width_min:
            return False
        if minutes is None:
            if self._mixed_enabled():
                minutes = int(getattr(
                    self.sc.sim, "near_term_step_min", 0) or
                    self.bucket_width_min)
            else:
                minutes = (self.bucket + 1) * self.bucket_width_min \
                    - self.current_minute
        minutes = int(minutes)
        if minutes <= 0:
            raise ValueError("advance_minutes requires a positive duration")
        target = min(
            self.current_minute + minutes,
            self.T * self.bucket_width_min)
        while self.current_minute < target:
            bucket_end = (self.bucket + 1) * self.bucket_width_min
            stop = min(target, bucket_end)
            if self._mixed_enabled():
                stop = min(stop, self.next_replan_minute)
            before = self.current_minute
            self._advance_current_bucket(stop)
            if self.current_minute <= before:
                raise RuntimeError("minute execution made no progress")
            # Events exactly on the committed boundary apply before a rolling
            # solve whose new plan also begins on that boundary.
            self._expire_temporary_state(self.current_minute)
            self._apply_events_at(self.current_minute)
            self._finish_elapsed_jobs(self.current_minute)
            self._maybe_replan_at_cadence()
        return self.current_minute < self.T * self.bucket_width_min

    def step(self) -> bool:
        """Advance one legacy bucket, or one configured mixed-mode cadence."""
        if self._mixed_enabled():
            return self.advance_minutes()
        remaining = (self.bucket + 1) * self.bucket_width_min \
            - self.current_minute
        return self.advance_minutes(remaining)

    def run_to_end(self, max_steps: int = 10 ** 4):
        n = 0
        while self.step() and n < max_steps:
            n += 1

    # -------------------------------------------------------------- delivery
    def _hard_delivery_quality_issue(
            self, dest_id: str, quality: Dict[str, float],
            bucket: int) -> str:
        """Return a dispatch hold reason when actual assays lack certification."""
        try:
            self.sc.destination(dest_id)
        except StopIteration:
            return ""
        absolute_bucket = int(getattr(
            self.plan, "horizon_offset", 0) or 0) + int(bucket)
        for spec in self.sc.demand_spec(dest_id, absolute_bucket):
            if not spec.hard:
                continue
            value = quality.get(spec.param)
            if value is None or not math.isfinite(float(value)):
                return f"{spec.param} unassayed; receiving dispatch held"
            if (spec.pmin is not None
                    and float(value) < spec.pmin + spec.margin - 1e-9) or (
                    spec.pmax is not None
                    and float(value) > spec.pmax - spec.margin + 1e-9):
                return (f"{spec.param} outside hard receiving interval; "
                        "receiving dispatch held")
        return ""

    def _deliver(self, dest_id: str, t: int, moved: float, quality: Dict[str, float],
                 grade_id: str = "", material_class: str = ""):
        if dest_id not in self.delivered:
            return
        prev = self.delivered[dest_id][t] * self.dt
        tot = prev + moved
        if tot > 0:
            self.delivered_q[dest_id][t] = self._mix_quality(
                prev, self.delivered_q[dest_id][t], moved, quality,
                incoming_grade=grade_id, incoming_stage=material_class)
            incoming = str(grade_id or "").strip()
            existing = self.delivered_grade[dest_id][t]
            if incoming:
                self.delivered_grade[dest_id][t] = (
                    incoming if not existing or existing == incoming
                    else "MIXED")
        self.delivered[dest_id][t] = tot / self.dt

    # -------------------------------------------------------------- replan
    def snapshot_scenario(self) -> Scenario:
        """Scenario reflecting live state, for a replan from the current bucket."""
        snap = copy.deepcopy(self.sc)
        for s in snap.spaces:
            s.mass_t = self.inv[s.id]
            s.quality = dict(self.space_quality.get(s.id, {}))
        snap.config[UNKNOWN_STOCK_QUALITY_KEY] = sorted(
            s.id for s in snap.spaces if s.mass_t > 0 and not s.quality)
        # Replanning must carry the physical recipe tonnes actually stacked so
        # far. Aggregate inventory and elapsed time are never used to declare
        # a blend complete.
        update_chevron_campaign_state(
            snap,
            self.jobs.values(),
            final_inventory_by_space=self.inv,
            actual_tonnage_by_job=self.moved_by_job,
            completed_through_minute=self.current_minute,
        )
        from .domain import CalendarEntry

        def add_temporary_change(
                scope: str, until_minute: int, reason: str, *,
                action: str = "UNAVAILABLE",
                rate_tph: Optional[float] = None) -> None:
            """Project live temporary state into a recoverable plan window."""
            start_minute = int(self.current_minute)
            end_minute = int(until_minute)
            if end_minute <= start_minute:
                return
            start_bucket = start_minute // self.bucket_width_min
            end_bucket = max(
                int(math.ceil(end_minute / self.bucket_width_min)) - 1,
                start_bucket)
            snap.calendars.append(CalendarEntry(
                scope, start_bucket, end_bucket, reason,
                start_minute=start_minute, end_minute=end_minute,
                action=action, rate_tph=rate_tph))

        for m in snap.machines:
            add_temporary_change(
                m.id, self.machine_down_until_minute.get(m.id, -1),
                "unexpected machine failure")
            rate_windows = [
                (start, end, factor)
                for start, end, factor
                in self.machine_rate_windows.get(m.id, ())
                if self.current_minute < end]
            for _start, rate_end, window_factor in rate_windows:
                # Intersecting caps remain separate; the scenario applies the
                # tightest rate and restores each one at its own exact end.
                add_temporary_change(
                    m.id, rate_end, "unexpected machine derating",
                    action="RATE_CAP",
                    rate_tph=m.rate_max_tph * max(
                        float(window_factor), 0.0))
            rate_until = self.machine_rate_factor_until_minute.get(m.id, -1)
            if self.current_minute < rate_until and not rate_windows:
                # block only the remaining outage window — the machine returns
                factor = max(float(
                    self.machine_rate_factor.get(m.id, 1.0)), 0.0)
                add_temporary_change(
                    m.id, rate_until, "unexpected machine derating",
                    action="RATE_CAP", rate_tph=m.rate_max_tph * factor)
        for l in snap.links:
            # links have no calendar in the plan model: conservatively
            # unavailable for the remaining horizon while down (documented)
            add_temporary_change(
                f"LINK:{l.id}",
                self.link_down_until_minute.get(l.id, -1),
                "unexpected conveyor/route outage")
        for destination_id, until_minute in \
                self.downstream_down_until_minute.items():
            add_temporary_change(
                f"LINE:{destination_id}", until_minute,
                "unexpected receiving-plant interruption")
            try:
                destination = snap.destination(destination_id)
            except StopIteration:
                continue
            stage = str(getattr(destination, "process_stage", "") or "")
            if stage:
                add_temporary_change(
                    f"LINE:{stage}", until_minute,
                    "unexpected process-line interruption")
        snap.belt_profile = [BeltProfileEntry(p.link_id, p.arrive_bucket, p.mass_t,
                                              p.grade, dict(p.quality), material_id=p.material_id)
                             for p in self.belts if p.arrive_bucket >= self.bucket]
        if self.material_catalog:
            catalogue = copy.deepcopy(self.material_catalog)
            seeds, pending = {}, {}
            for sid, pools in self.material_mass.items():
                seeds[sid], pending[sid] = {}, {}
                for mid, mass in pools.items():
                    if mass <= 1e-9:
                        continue
                    record = {**catalogue[mid], "quality": dict(self._material_quality(sid, mid))}
                    live_mid = washing._register(snap, catalogue, record)
                    seeds[sid][live_mid] = seeds[sid].get(live_mid, 0.) + mass
                    pending[sid].setdefault(live_mid, []).extend(copy.deepcopy([
                        part for part in self.material_pending.get(sid, {}).get(mid, [])
                        if part["ready_minute"] > self.current_minute]))
            for parcel in snap.belt_profile:
                if parcel.material_id in catalogue:
                    record = {**catalogue[parcel.material_id], "quality": dict(parcel.quality)}
                    parcel.material_id = washing._register(snap, catalogue, record)
            snap.config["_WASHING_MODEL_CATALOG"] = catalogue
            snap.config["_WASHING_MODEL_INITIAL"] = seeds
            snap.config["_WASHING_MODEL_PENDING"] = pending
        snap.config[UNKNOWN_BELT_QUALITY_KEY] = [
            index for index, parcel in enumerate(snap.belt_profile)
            if parcel.mass_t > 0 and not parcel.quality]
        for (d, t), v in self.demand_override.items():
            requested_grade = snap.demand_grade(d, t)
            priority = snap.demand_priority(d, t)
            snap.demand = [e for e in snap.demand
                           if not (e.dest_id == d and e.bucket == t)]
            from .domain import DemandEntry
            snap.demand.append(DemandEntry(
                d, t, v, requested_grade=requested_grade, priority=priority))

        # Preserve orders from an interrupted, already elapsed hour.  The
        # optimiser balances demand bucket-by-bucket, so carry each outstanding
        # grade into the earliest future bucket with the same campaign grade.
        # A different-grade future row is never relabelled in place: its mass
        # must first be moved onto another row of its own grade.
        demand_backlog = self._elapsed_demand_backlog()
        snap.config["REPLAN_DEMAND_BACKLOG_T"] = {
            destination_id: float(detail["total_t"])
            for destination_id, detail in demand_backlog.items()
        }
        snap.config["REPLAN_DEMAND_BACKLOG_BY_GRADE_T"] = {
            destination_id: dict(detail["by_grade_t"])
            for destination_id, detail in demand_backlog.items()
        }
        first_future_bucket = self.bucket + (
            1 if self.current_minute % self.bucket_width_min else 0)
        from .domain import DemandEntry
        scheduled_backlog: Dict[str, Dict[str, float]] = {}
        unscheduled_backlog: Dict[str, Dict[str, float]] = {}

        def replace_demand(
                destination_id: str, bucket: int, rate_tph: float,
                requested_grade: str) -> None:
            priority = snap.demand_priority(destination_id, bucket)
            snap.demand = [entry for entry in snap.demand
                           if not (entry.dest_id == destination_id and
                                   entry.bucket == bucket)]
            snap.demand.append(DemandEntry(
                destination_id, bucket, rate_tph,
                requested_grade=requested_grade, priority=priority))

        for destination_id, detail in demand_backlog.items():
            occupied: Set[int] = set()
            grade_amounts = dict(detail["by_grade_t"])
            for grade, mass_t in grade_amounts.items():
                candidates = list(range(first_future_bucket, self.T))
                target_bucket = next((
                    bucket for bucket in candidates
                    if bucket not in occupied and
                    snap.demand_grade(destination_id, bucket) == grade
                ), None)
                if target_bucket is None:
                    # Free one slot by merging its existing campaign mass into
                    # a later row carrying that same grade.  This retains both
                    # the old backlog grade and the displaced future campaign.
                    relocation_bucket = None
                    for candidate in candidates:
                        if candidate in occupied:
                            continue
                        displaced_rate = float(snap.demand_tph(
                            destination_id, candidate))
                        displaced_grade = snap.demand_grade(
                            destination_id, candidate)
                        if displaced_rate <= 1e-9:
                            target_bucket = candidate
                            break
                        relocation_bucket = next((
                            later for later in candidates
                            if later > candidate and later not in occupied and
                            snap.demand_grade(destination_id, later) ==
                            displaced_grade), None)
                        if relocation_bucket is None:
                            continue
                        relocated_rate = float(snap.demand_tph(
                            destination_id, relocation_bucket))
                        replace_demand(
                            destination_id, relocation_bucket,
                            relocated_rate + displaced_rate, displaced_grade)
                        replace_demand(
                            destination_id, candidate, 0.0, grade)
                        target_bucket = candidate
                        break
                if target_bucket is None:
                    unscheduled_backlog.setdefault(destination_id, {})[grade] = \
                        float(mass_t)
                    continue
                occupied.add(target_bucket)
                base_rate = float(snap.demand_tph(
                    destination_id, target_bucket))
                base_grade = snap.demand_grade(
                    destination_id, target_bucket)
                replace_demand(
                    destination_id, target_bucket,
                    base_rate + float(mass_t) / self.dt,
                    grade or base_grade)
                scheduled_backlog.setdefault(destination_id, {})[grade] = \
                    float(mass_t)
        snap.config["REPLAN_SCHEDULED_BACKLOG_BY_GRADE_T"] = scheduled_backlog
        snap.config["REPLAN_UNSCHEDULED_BACKLOG_BY_GRADE_T"] = \
            unscheduled_backlog
        if self._mixed_enabled():
            # True rolling horizon: retain the configured look-ahead length
            # from the exact current minute instead of shrinking the planning
            # window after every execution commit.
            horizon_min = int(self.sc.sim.effective_horizon_minutes())
            snap.sim.horizon_minutes = horizon_min
            snap.sim.horizon_buckets = int(math.ceil(
                horizon_min / float(self.bucket_width_min)))
        else:
            snap.sim.horizon_buckets = self.T - self.bucket
            snap.sim.horizon_minutes = \
                snap.sim.horizon_buckets * self.bucket_width_min
        # frozen horizon + human/locked jobs become fixed jobs.
        # windows are clipped around calendar/outage blocks so a frozen job can
        # never contradict a machine outage (that would be INFEASIBLE by design).
        frozen_end = self.bucket + snap.sim.frozen_buckets - 1

        def first_free(mid: str, b0: int, b1: int):
            """First sub-window of [b0,b1] not blocked for machine mid, or None."""
            t = b0
            while t <= b1:
                blocked = snap.calendar_scope_blocked(mid, bucket=t)
                if not blocked:
                    t2 = t
                    while t2 + 1 <= b1 and not \
                            snap.calendar_scope_blocked(
                                mid, bucket=t2 + 1):
                        t2 += 1
                    return t, t2
                t += 1
            return None

        snap.fixed_jobs = list(snap.fixed_jobs)
        for j in self.jobs.values():
            if j.status in ("CANCELLED", "REJECTED", "FINISHED"):
                continue
            if self._mixed_enabled():
                physical_start = self._job_physical_start_minute(j)
                physical_end = self._job_physical_end_minute(j)
                commit_end = self.current_minute + max(int(getattr(
                    snap.sim, "commit_window_min", 0) or 0), 0)
                governed = j.origin == "HUMAN" or j.locked
                running = j.status == "RUNNING" and \
                    physical_end > self.current_minute
                committed_auto = (
                    j.origin == "AUTO" and
                    self.current_minute <= physical_start <= commit_end)
                if not (governed or running or committed_auto):
                    continue
                exact_start = max(physical_start, self.current_minute)
                exact_end = physical_end
                down_until = self.machine_down_until_minute.get(j.machine, -1)
                if exact_start < down_until:
                    exact_start = down_until
                if exact_end <= exact_start:
                    continue
                start_bucket = exact_start // self.bucket_width_min
                end_bucket = max(int(math.ceil(
                    exact_end / float(self.bucket_width_min))) - 1,
                    start_bucket)
                r0, r1 = x_interval_to_reperes(
                    snap.space(j.space), j.start_x, j.stop_x)
                snap.fixed_jobs.append(FixedJob(
                    id=f"FROZEN-{j.id}", machine=j.machine, kind=j.kind,
                    space=j.space, dest_id=j.dest_id, rate_tph=j.rate_tph,
                    start_bucket=start_bucket, end_bucket=end_bucket,
                    note="minute commitment / operator lock", grade=j.grade,
                    start_repere=r0, stop_repere=r1,
                    start_minute=exact_start, end_minute=exact_end,
                    advisory_rate=not (j.origin == "HUMAN" or j.locked)))
                continue
            keep = (j.origin == "HUMAN" or j.locked or
                    (j.start_bucket <= frozen_end and j.end_bucket >= self.bucket))
            if not keep:
                continue
            win = first_free(j.machine, max(j.start_bucket, self.bucket),
                             min(j.end_bucket, self.T - 1))
            if win is None:
                continue    # machine blocked for the whole window — cannot freeze
            r0, r1 = x_interval_to_reperes(
                snap.space(j.space), j.start_x, j.stop_x)
            snap.fixed_jobs.append(FixedJob(
                id=f"FROZEN-{j.id}", machine=j.machine, kind=j.kind,
                space=j.space, dest_id=j.dest_id, rate_tph=j.rate_tph,
                start_bucket=win[0], end_bucket=win[1],
                note="frozen-horizon / operator lock", grade=j.grade,
                start_repere=r0, stop_repere=r1,
                advisory_rate=not (j.origin == "HUMAN" or j.locked)))
        return snap

    def replan(self, reason: str = "deviation",
               start_minute: Optional[int] = None):
        old_jobs = [copy.deepcopy(j) for j in self.jobs.values()]
        snap = self.snapshot_scenario()
        exact_start = (
            self.current_minute if start_minute is None
            else int(start_minute))
        # State immediately before the new horizon prevents a running frozen
        # job from becoming an artificial stop/start at every replan.
        initial_machine_groups: Dict[str, Tuple[object, ...]] = {}
        active_before = sorted(
            [
                (
                self._job_physical_start_minute(job), job
                )
                for job in old_jobs
                if job.status not in {"CANCELLED", "REJECTED"}
                and self._job_physical_start_minute(job) < exact_start
                and self._job_physical_end_minute(job) >= exact_start
            ],
            key=lambda row: (row[0], row[1].machine, row[1].id))
        for _start, job in active_before:
            initial_machine_groups[job.machine] = job_group_key(job)

        initial_feed_gap: Dict[str, bool] = {}
        initial_open_feed_gap_minutes: Dict[str, float] = {}
        try:
            policies = parse_continuity_policies(
                snap.config.get("CONTINUITY_POLICIES", {}),
                (destination.id for destination in snap.destinations))
        except ValueError:
            policies = {}
        evaluated_buckets = min(
            int(math.ceil(exact_start / float(self.bucket_width_min))),
            self.T)
        if evaluated_buckets and policies:
            elapsed_h = exact_start / 60.0
            durations = [
                min(self.dt, max(elapsed_h - bucket * self.dt, 0.0))
                for bucket in range(evaluated_buckets)]
            continuity = compute_continuity_metrics(
                policies,
                {
                    destination.id: [
                        self.demand_tph(destination.id, bucket)
                        for bucket in range(evaluated_buckets)]
                    for destination in snap.destinations
                },
                {
                    destination.id: [
                        self.delivered[destination.id][bucket] * self.dt
                        for bucket in range(evaluated_buckets)]
                    for destination in snap.destinations
                },
                durations,
            )
            initial_feed_gap = dict(
                continuity["terminal_gap_by_destination"])
            initial_open_feed_gap_minutes = dict(
                continuity["terminal_open_gap_minutes"])
        opts = SolveOptions(
            horizon_offset=self.bucket,
            start_minute_offset=(exact_start if self._mixed_enabled()
                                 else None),
            banned=list(self.banned),
            # Temporary derates are exact RATE_CAP calendars in the snapshot.
            # A global factor would incorrectly last for the rest of the week.
            machine_rate_factor={},
            initial_machine_groups=initial_machine_groups,
            initial_feed_gap=initial_feed_gap,
            initial_open_feed_gap_minutes=
                initial_open_feed_gap_minutes)
        new_plan = solve_plan(snap, opts)
        if new_plan.status not in ("OPTIMAL", "FEASIBLE") and snap.fixed_jobs:
            # Frozen jobs are HARD (`AG[group, t] == 1`). Against a snapshot
            # taken mid-process they can contradict the state they were frozen
            # from: a frozen withdrawal needs upstream work-in-progress that
            # `sum(outs) <= inv[t]` says is not there yet, and the window is
            # then unsatisfiable. Measured 29-Sep-2026 on FULL_FLOW: frozen
            # jobs alone -> INFEASIBLE/0 jobs, mid-process WIP alone ->
            # INFEASIBLE/0 jobs, both relaxed -> 297 jobs. Neither is
            # individually at fault; the pair is.
            #
            # Retrying without the freeze trades the near-term commitment for
            # a plan that exists. A stale plan is worse: it keeps commanding
            # work against a world that has moved on, and it does so silently.
            # The degradation is recorded so the caller can see the commitment
            # was dropped rather than honoured.
            degraded = copy.deepcopy(snap)
            degraded.fixed_jobs = []
            retry = solve_plan(degraded, opts)
            if retry.status in ("OPTIMAL", "FEASIBLE"):
                retry.reasons.append(
                    "REPLAN_FREEZE_RELAXED: the constrained re-solve was "
                    f"{new_plan.status}; {len(snap.fixed_jobs)} frozen job(s) "
                    "were dropped to obtain a usable plan. Near-term "
                    "commitments were NOT preserved for this cycle.")
                self.replan_freeze_relaxed += 1
                new_plan = retry
        carried_backlog = snap.config.get("REPLAN_DEMAND_BACKLOG_T", {})
        if isinstance(carried_backlog, dict):
            new_plan.breakdown["replan_carried_demand_t"] = sum(
                max(float(value), 0.0)
                for value in carried_backlog.values())
        unscheduled_by_grade = snap.config.get(
            "REPLAN_UNSCHEDULED_BACKLOG_BY_GRADE_T", {})
        if isinstance(unscheduled_by_grade, dict):
            unscheduled_total = sum(
                max(float(mass_t), 0.0)
                for grade_amounts in unscheduled_by_grade.values()
                if isinstance(grade_amounts, dict)
                for mass_t in grade_amounts.values())
            new_plan.breakdown["replan_unscheduled_grade_backlog_t"] = \
                unscheduled_total
            if unscheduled_total > 1e-6:
                new_plan.reasons.append(
                    "REPLAN_GRADE_BACKLOG_UNSCHEDULED "
                    f"{unscheduled_total:.1f} t remains grade-specific unmet "
                    "because no same-grade future demand slot is available")
        self.replans += 1
        self.replan_solve_s.append(new_plan.solver_wall_s)
        if new_plan.status in ("OPTIMAL", "FEASIBLE"):
            kept = {jid: j for jid, j in self.jobs.items()
                    if j.status in ("FINISHED", "CANCELLED", "REJECTED")
                    or self._job_physical_end_minute(j) <= exact_start}
            # running frozen jobs stay under their old ids
            for jid, j in self.jobs.items():
                if jid in kept:
                    continue
                if j.origin == "HUMAN" or j.locked or j.status == "RUNNING":
                    trimmed = copy.deepcopy(j)
                    kept[jid] = trimmed
            for j in new_plan.jobs:
                j_start = (int(j.start_minute)
                           if j.start_minute is not None
                           else j.start_bucket * self.bucket_width_min)
                j_end = self._job_physical_end_minute(j)
                dup = False
                for kept_job in kept.values():
                    if kept_job.status not in ("RUNNING", "PLANNED") or \
                            kept_job.machine != j.machine or \
                            kept_job.space != j.space or \
                            kept_job.dest_id != j.dest_id:
                        continue
                    kept_start = (
                        int(kept_job.start_minute)
                        if kept_job.start_minute is not None
                        else kept_job.start_bucket * self.bucket_width_min)
                    kept_end = self._job_physical_end_minute(kept_job)
                    if not (j_end <= kept_start or j_start >= kept_end):
                        dup = True
                        break
                if not dup:
                    kept[j.id] = j
            self.jobs = kept
            self.plan = new_plan
            self._adopt_blend_plan(new_plan)
            cancelled = len([j for j in old_jobs
                             if j.status in ("PLANNED",) and j.id not in kept])
            self._log("REPLAN", f"replan #{self.replans} ({reason}): "
                      f"{len(new_plan.jobs)} new jobs, {cancelled} cancelled/moved, "
                      f"solve {new_plan.solver_wall_s:.2f}s, status {new_plan.status}",
                      detail_reason=reason)
        else:
            # A failed replan keeps the PREVIOUS plan. That is the safe
            # behaviour, but it must never be mistaken for a successful one:
            # ``replans`` counts attempts, so without this the simulation
            # reports "N replans" while still executing the plan it computed
            # at t=0, and plan_stability_pct reads 100% precisely because
            # nothing ever changed. Recorded here so callers and KPIs can see
            # it rather than having to read the log.
            self.replan_failures += 1
            self.last_replan_failure = {
                "bucket": self.bucket,
                "reason": reason,
                "status": new_plan.status,
                "solver_reasons": list(new_plan.reasons or []),
            }
            self._log("REPLAN", f"replan #{self.replans} ({reason}) FAILED: "
                      f"{new_plan.status} — {'; '.join(new_plan.reasons)}",
                      detail_reason=reason)
        self.plan_history.append((self.bucket, old_jobs,
                                  [copy.deepcopy(j) for j in self.jobs.values()],
                                  reason))
