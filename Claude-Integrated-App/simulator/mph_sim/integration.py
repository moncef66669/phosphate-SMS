"""Three-SMS integration layer — Louta / Benguerir / MPH.

USER-CONFIRMED project fact: Louta, Benguerir and MPH all use identical
Innomotics/YardMaster SMS solutions — same solution family, same ASP
job-planning concept, same plan-item and validation workflow — deployed as
THREE INDEPENDENT SMS INSTANCES (one per site).

The MPH FRS (Q80130-MZI-M16-TAK-45000183621-DG) defines the COMMON interface
profile; its application to the two identical mine-SMS deployments is
confirmed by the user. The MPH document itself does NOT cover the mine sites.

Site-specific and TBC until ICD/configuration exports are obtained:
endpoint/host · virtual host + credentials · exchange/queue/routing-key names
· Device IDs · Pile IDs · Material/Grade/SubGrade mappings · configured
AspRoutes · work calendars · minimum machine-distance parameters · final
telegram namespaces/version · site-specific permissions.

Design rules implemented here:
 - ONE adapter implementation / profile (`INNOMOTICS_YARDMASTER_ASP`), three
   independently configured instances — business logic is never duplicated.
 - `AspPlanItem` stays faithful to the common vendor telegram schema; SMS/site
   ownership lives ONLY in the `SmsPlanBatch` envelope + UI/export metadata.
 - Job ownership: `Job.machine -> Machine.site`, cross-checked against
   `Job.space -> StockSpace.site`; a mismatch or unknown owner goes to the
   UNMAPPED error queue — NEVER silently assigned to MPH.
 - Per-SMS validation runs the simulated eight-rule ASP gate on a SITE
   PROJECTION of the scenario, so FIFO / spatial-conflict / inventory /
   calendar checks never cross sites.
 - ASP plan submission/evaluation and JobUpdated remain OFFLINE SIMULATED.
 - A separate capability-specific registry can fetch read-only SpaceValue
   inventory when commissioned; it never falls back to simulated live data.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .adapters import (AspAdapter, AspIssue, AspPlanItem, RabbitMqAspAdapter,
                       SimulatedAspAdapter, job_to_plan_items)
from .domain import Evidence, Job, PlanResult, Scenario
from .rabbitmq_inventory import (
    LiveConfigBundle, LiveInventoryPullResult, RabbitMqInventoryConnector,
    RpcTransport, load_live_inventory_configs, pull_live_inventory_snapshot,
)
from .timing import VALIDATED, validate_execution_windows

COMMON_PROFILE = "INNOMOTICS_YARDMASTER_ASP"
SMS_SITES = ("LOUTA", "BENGUERIR", "MPH")

#: batch/item lifecycle stages (integration view-model — internal Job.status
#: is NOT overloaded; "simulated" suffixes make the offline nature explicit)
LIFECYCLE = ("Generated", "Partitioned", "Mapped", "Submitted (simulated)",
             "Evaluated (simulated)", "Accepted (simulated)",
             "Rejected (simulated)", "Dispatched (simulated)",
             "Running (simulated)", "Finished (simulated)", "Cancelled")

PLANNING_RESPONSIBILITY = {
    "LOUTA": "Louta stockyard stacking, reclaiming and corridor-dispatch jobs",
    "BENGUERIR": "Benguerir stockyard stacking, reclaiming, C2 dispatch and "
                 "applicable train-outlet jobs",
    "MPH": "M16 reception/stacking, pile reclaim and PAP/DRY-feed jobs",
}


# ------------------------------------------------------------- configuration
@dataclass
class SmsInstanceConfig:
    """One independently configured SMS instance (common adapter profile)."""
    site_id: str
    sms_id: str
    display_name: str
    endpoint: str = ""                       # TBC — awaiting ICD/config export
    adapter_profile: str = COMMON_PROFILE
    connection_mode: str = "offline-simulated"   # reference | offline-simulated
    #                                            # | configured | connected
    evidence: Evidence = field(default_factory=Evidence)
    configured: bool = False                 # endpoint/master data present?


def default_sms_configs() -> Dict[str, SmsInstanceConfig]:
    """Default OFFLINE configurations — endpoints blank/TBC, no credentials."""
    ev = Evidence(
        source="user confirmation — identical Innomotics/YardMaster SMS at "
               "Louta, Benguerir and MPH; MPH FRS defines the common profile",
        status="CONFIRMED", confidence="H",
        note="site endpoint/master data TBC until ICD/configuration exports")
    return {
        "LOUTA": SmsInstanceConfig("LOUTA", "LOUTA-SMS", "Louta SMS",
                                   evidence=copy.deepcopy(ev)),
        "BENGUERIR": SmsInstanceConfig("BENGUERIR", "BENGUERIR-SMS",
                                       "Benguerir SMS",
                                       evidence=copy.deepcopy(ev)),
        "MPH": SmsInstanceConfig(
            "MPH", "MPH-SMS", "MPH SMS",
            evidence=Evidence(source="MPH FRS Q80130-MZI-M16-TAK-45000183621-"
                                     "DG (detailed functional reference)",
                              status="CONFIRMED", confidence="H",
                              note="RabbitMQ ICD/credentials still TBC (H1)")),
    }


# --------------------------------------------------------------- partitioning
def partition_jobs_by_sms(sc: Scenario,
                          jobs: List[Job]) -> Dict[str, List[Job]]:
    """Assign every job to exactly one SMS owner.

    Ownership = `Job.machine -> Machine.site`, cross-checked against
    `Job.space -> StockSpace.site`. Unknown machine, unknown owner site or a
    machine/space site mismatch -> UNMAPPED (visible error queue). No job is
    lost, duplicated, or silently assigned to MPH.
    """
    out: Dict[str, List[Job]] = {s: [] for s in SMS_SITES}
    out["UNMAPPED"] = []
    msite = {m.id: m.site for m in sc.machines}
    ssite = {s.id: s.site for s in sc.spaces}
    for j in jobs:
        ms = msite.get(j.machine)
        ss = ssite.get(j.space)
        if ms not in SMS_SITES or (ss is not None and ss != ms):
            out["UNMAPPED"].append(j)
        else:
            out[ms].append(j)
    return out


def unmapped_reason(sc: Scenario, j: Job) -> str:
    msite = {m.id: m.site for m in sc.machines}
    ssite = {s.id: s.site for s in sc.spaces}
    ms = msite.get(j.machine)
    ss = ssite.get(j.space)
    if ms is None:
        return f"unknown machine '{j.machine}' — no Machine.site owner"
    if ms not in SMS_SITES:
        return f"machine site '{ms}' has no SMS instance"
    if ss is not None and ss != ms:
        return (f"machine/pile site mismatch: machine {j.machine} is "
                f"{ms}, pile {j.space} is {ss}")
    return "unmapped (unexpected)"


# ------------------------------------------------------------ site projection
def scenario_site_projection(sc: Scenario, site_id: str) -> Scenario:
    """Scenario restricted to ONE site's machines, spaces, segments and
    calendars, so the eight-rule ASP gate is evaluated per SMS instance —
    a Louta pile never participates in a Benguerir FIFO or spatial-conflict
    check; a Benguerir machine is never validated against an M16 work area."""
    p = copy.deepcopy(sc)
    keep_sp = {s.id for s in sc.spaces if s.site == site_id}
    keep_m = {m.id for m in sc.machines if m.site == site_id}
    p.spaces = [s for s in p.spaces if s.id in keep_sp]
    p.machines = [m for m in p.machines if m.id in keep_m]
    p.segments = [g for g in p.segments if g.space_id in keep_sp]
    p.calendars = [c for c in p.calendars
                   if c.scope == "GLOBAL" or c.scope in keep_m
                   or c.scope in keep_sp]
    return p


def m17_space_ids(sc: Scenario) -> set:
    """Domain B (M17 sulphur) spaces — identified by the M17 id/area prefix."""
    return {s.id for s in sc.spaces
            if s.id.startswith("M17") or s.area.startswith("M17")}


def job_domain(sc: Scenario, j: Job) -> str:
    return ("B — Sulphur (M17)" if j.space in m17_space_ids(sc)
            else "A — Phosphate")


# ------------------------------------------------------------------- batches
@dataclass
class SmsPlanBatch:
    """Site-targeted envelope: SmsPlanBatch -> target SMS -> AspPlanItem[].
    The AspPlanItem wire schema itself carries NO site/SMS field."""
    site_id: str
    sms_id: str
    plan_revision: str
    generated_at: str
    horizon_buckets: int
    items: List[AspPlanItem] = field(default_factory=list)
    jobs: List[Job] = field(default_factory=list)
    validation_issues: List[AspIssue] = field(default_factory=list)
    dispatch_state: str = "Generated"
    item_states: Dict[str, str] = field(default_factory=dict)
    # Wire item -> authoritative solver job.  Usually identical; validated
    # multi-window jobs use deterministic ``-Wnn`` item suffixes.
    item_parent_jobs: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    def issue_counts(self) -> Dict[str, int]:
        c = {"ERROR": 0, "WARNING": 0, "INFO": 0}
        for i in self.validation_issues:
            c[i.severity] = c.get(i.severity, 0) + 1
        return c


@dataclass
class SmsIntegrationState:
    configs: Dict[str, SmsInstanceConfig]
    batches: Dict[str, SmsPlanBatch]
    unmapped: List[Tuple[Job, str]]
    plan_revision: str = ""
    generated_at: str = ""


def plan_revision_of(jobs: List[Job]) -> str:
    """Stable hash of complete job payloads, independent of list ordering."""
    sig = []
    for job in jobs:
        payload = dataclasses.asdict(job)
        payload["bucket_flows"] = sorted(
            payload["bucket_flows"],
            key=lambda row: json.dumps(
                row, sort_keys=True, separators=(",", ":"), default=str))
        payload["execution_windows"] = sorted(
            payload["execution_windows"],
            key=lambda row: json.dumps(
                row, sort_keys=True, separators=(",", ":"), default=str))
        sig.append(payload)
    sig.sort(key=lambda row: (
        str(row.get("id", "")),
        json.dumps(
            row, sort_keys=True, separators=(",", ":"), default=str)))
    return hashlib.sha256(json.dumps(
        sig, sort_keys=True, separators=(",", ":"), default=str).encode()) \
        .hexdigest()[:8]


def _item_state(j: Job, has_error: bool) -> str:
    """Integration lifecycle for one item (simulated — never a real
    transmission). Internal planning state stays in Job.status."""
    if j.status == "CANCELLED":
        return "Cancelled"
    if has_error:
        return "Rejected (simulated)"
    if j.status == "FINISHED":
        return "Finished (simulated)"
    if j.status == "RUNNING":
        return "Running (simulated)"
    return "Accepted (simulated)"


def build_sms_batches(sc: Scenario, plan: Optional[PlanResult],
                      sim=None, min_distance_m: float = 50.0,
                      current_pile_only: bool = True,
                      validate: bool = True) -> SmsIntegrationState:
    """Partition the current jobs, map them with the common plan-item
    mapping and validate each site batch against its own site projection."""
    configs = default_sms_configs()
    present = {s.id for s in sc.sites}
    for site, cfg in configs.items():
        if site not in present:
            cfg.connection_mode = "reference"
    jobs = [j for j in (list(sim.jobs.values()) if sim else
                        (plan.jobs if plan else []))
            if j.status in ("PLANNED", "RUNNING", "FINISHED", "CANCELLED")]
    part = partition_jobs_by_sms(sc, jobs)
    rev = plan_revision_of([j for j in jobs if j.status != "CANCELLED"])
    now = _dt.datetime.now().isoformat(timespec="seconds")

    batches: Dict[str, SmsPlanBatch] = {}
    for site in SMS_SITES:
        sjobs = part[site]
        active = [j for j in sjobs if j.status != "CANCELLED"]
        items: List[AspPlanItem] = []
        item_parent_jobs: Dict[str, str] = {}
        issues: List[AspIssue] = []
        exact_active = [job for job in active if job.bucket_flows]
        timing_errors = [
            issue for issue in validate_execution_windows(sc, exact_active)
            if issue.severity == "ERROR"]
        timing_blocked = {
            issue.job_id for issue in timing_errors if issue.job_id}
        if any(not issue.job_id for issue in timing_errors):
            timing_blocked.update(job.id for job in exact_active)
        for issue in timing_errors:
            issues.append(AspIssue(
                "ERROR", "ExecutionTiming",
                f"{issue.code}: {issue.message}",
                issue.job_id))
        for job in active:
            mapped = (
                [] if job.id in timing_blocked
                else job_to_plan_items(sc, job))
            if job.bucket_flows and not mapped and \
                    job.id not in timing_blocked:
                statuses = sorted({
                    window.status for window in job.execution_windows})
                status_text = ", ".join(statuses) if statuses else "MISSING"
                issues.append(AspIssue(
                    "ERROR", "ExecutionTiming",
                    "Exact-profile job has unresolved minute execution "
                    f"timing ({status_text}); no ASP item was emitted. "
                    "Resolve the positioning/timing conflict and replan "
                    "before submission.",
                    job.id))
            items.extend(mapped)
            item_parent_jobs.update({item.JobId: job.id for item in mapped})
        if validate and items:
            adapter = SimulatedAspAdapter(
                scenario_site_projection(sc, site), site_id=site,
                min_distance_m=min_distance_m,
                current_pile_only=current_pile_only)
            issues.extend(adapter.submit_plan_items(items))
        err_items = {i.item_id for i in issues if i.severity == "ERROR"}
        err_jobs = {item_parent_jobs.get(item_id, item_id)
                    for item_id in err_items}
        states = {j.id: _item_state(j, j.id in err_jobs) for j in sjobs}
        if configs[site].connection_mode == "reference":
            short = sc.name.split(" —")[0].strip() or sc.name
            state = "Reference only"
            note = f"Reference instance — not included in {short}."
        elif not sjobs:
            state, note = "No jobs", \
                "No jobs required for this SMS in the selected horizon."
        elif err_items:
            state = "Evaluated (simulated) — rejected items present"
            note = ""
        else:
            state, note = "Evaluated (simulated) — accepted", ""
        batches[site] = SmsPlanBatch(
            site_id=site, sms_id=configs[site].sms_id, plan_revision=rev,
            generated_at=now, horizon_buckets=sc.sim.horizon_buckets,
            items=items, jobs=sjobs, validation_issues=issues,
            dispatch_state=state, item_states=states,
            item_parent_jobs=item_parent_jobs, note=note)

    unmapped = [(j, unmapped_reason(sc, j)) for j in part["UNMAPPED"]]
    return SmsIntegrationState(configs=configs, batches=batches,
                               unmapped=unmapped, plan_revision=rev,
                               generated_at=now)


# ------------------------------------------------------------------ feedback
def job_updates_for_site(sc: Scenario, sim, site_id: str) -> List[dict]:
    """Simulated `JobUpdated` telegrams owned by one SMS instance."""
    if sim is None:
        return []
    msite = {m.id: m.site for m in sc.machines}
    out = []
    for u in sim.job_updates:
        j = sim.jobs.get(u.job_id)
        if j is not None and msite.get(j.machine) == site_id:
            out.append({"job_id": u.job_id, "status": u.status,
                        "moved_t": u.moved_t,
                        "effective_rate_tph": u.effective_rate_tph,
                        "machine_position_x": u.machine_position_x,
                        "quality": u.quality,
                        "forecast_end_bucket": u.forecast_end_bucket,
                        "bucket": u.bucket})
    return out


def space_values_for_site(sc: Scenario, site_id: str, sim=None) -> List[dict]:
    """Simulated `SpaceValueRequest/Response` — ONLY spaces owned by this
    SMS. With a simulation attached, mass/quality reflect the current
    simulated state; segments come from the actual Segment register."""
    rows = []
    for s in sc.spaces:
        if s.site != site_id:
            continue
        mass = s.mass_t
        qual = dict(sc.quality_of(s.id))
        grade = s.grade
        if sim is not None:
            mass = sim.inv.get(s.id, mass)
            qual = dict(sim.space_quality.get(s.id, qual))
            try:
                grade = sim.sc.space(s.id).grade
            except Exception:
                pass
        segs = [g for g in sc.segments if g.space_id == s.id]
        rows.append({
            "pile": s.id, "x_from": s.x_from, "x_to": s.x_to,
            "mass_t": round(mass, 1), "prevalent_grade": grade,
            "quality": {k: round(v, 3) for k, v in qual.items()},
            "segments": len(segs),
            "age_days_max": max((g.age_days for g in segs), default=0.0)})
    return rows


# ------------------------------------------------------------------ registry
class MultiSmsAdapterRegistry:
    """site_id -> AspAdapter. ONE adapter implementation, three independently
    configured instances (never three copies of the class)."""

    def __init__(self, sc: Scenario,
                 configs: Optional[Dict[str, SmsInstanceConfig]] = None):
        self.configs = configs or default_sms_configs()
        present = {s.id for s in sc.sites}
        self._adapters: Dict[str, AspAdapter] = {}
        for site, cfg in self.configs.items():
            if site not in present:
                continue
            if cfg.configured and cfg.endpoint:
                self._adapters[site] = RabbitMqAspAdapter(
                    site_id=site, sms_id=cfg.sms_id, endpoint=cfg.endpoint)
            else:
                self._adapters[site] = SimulatedAspAdapter(
                    scenario_site_projection(sc, site), site_id=site)
            # sites absent from the scenario stay reference-only (no adapter)

    def adapter(self, site_id: str) -> Optional[AspAdapter]:
        return self._adapters.get(site_id)

    def health(self) -> Dict[str, dict]:
        out = {}
        for site, cfg in self.configs.items():
            a = self._adapters.get(site)
            out[site] = a.health() if a is not None else \
                {"mode": "reference", "ok": False, "site": site,
                 "detail": "not part of the loaded scenario"}
        return out


class MultiSmsInventoryRegistry:
    """Read-only live inventory capability; never falls back to simulation.

    Plan submission/JobUpdated remain on the separate ASP adapter surface.
    A missing or invalid live connector is reported as unavailable, not
    replaced by ``SimulatedAspAdapter`` under a live-looking status.
    """

    def __init__(
            self,
            sc: Scenario,
            bundle: Optional[LiveConfigBundle] = None,
            *,
            transport_factory=None,
            connector_factory=None,
            environ=None,
    ):
        self.sc = sc
        self.bundle = bundle or load_live_inventory_configs(environ)
        self.loaded_sites = sorted({sp.site for sp in sc.spaces})
        self._connectors: Dict[str, RabbitMqInventoryConnector] = {}
        for site in self.loaded_sites:
            cfg = self.bundle.configs.get(site)
            if cfg is None:
                continue
            if connector_factory is not None:
                connector = connector_factory(cfg)
            else:
                transport: Optional[RpcTransport] = (
                    transport_factory(cfg) if transport_factory else None)
                connector = RabbitMqInventoryConnector(
                    cfg, transport=transport, environ=environ)
            self._connectors[site] = connector

    def connector(self, site_id: str) -> Optional[RabbitMqInventoryConnector]:
        return self._connectors.get(site_id)

    def readiness(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for site in self.loaded_sites:
            connector = self._connectors.get(site)
            if connector is None:
                out[site] = {
                    "site": site, "mode": "not-configured", "ready": False,
                    "missing_or_invalid": [
                        "no site profile in the live inventory config bundle"],
                    "config_revision": self.bundle.revision_hash,
                }
            else:
                out[site] = connector.readiness()
        return out

    def probe(self, site_ids: Optional[List[str]] = None,
              timeout_s: float = 2.0) -> Dict[str, dict]:
        sites = site_ids or self.loaded_sites
        out: Dict[str, dict] = {}
        for site in sites:
            connector = self._connectors.get(site)
            out[site] = (connector.health(timeout_s=timeout_s)
                         if connector else self.readiness().get(site, {
                             "site": site, "mode": "not-configured",
                             "ready": False,
                         }))
        return out

    def pull(self, *, site_ids: Optional[List[str]] = None,
             max_site_skew_seconds: float = 60.0,
             max_age_hours: float = 0.5,
             require_complete: bool = True) -> LiveInventoryPullResult:
        return pull_live_inventory_snapshot(
            self.sc, self._connectors, sites=site_ids,
            max_site_skew_seconds=max_site_skew_seconds,
            max_age_hours=max_age_hours, require_complete=require_complete)
