"""Campaign / parcel / minimum-run policy — one definition for every layer.

Job fragmentation was not a charting defect: the hourly allocation model had
no notion of a *smallest sensible parcel*, no notion of a *minimum run*, and
no explicit representation of a machine that is held running between two
material bursts.  Three independent layers then disagreed:

* the optimiser could keep an activity group "active" for one physical tonne
  in an hourly bucket (the rate floor collapses to ``>= 1 t`` whenever
  ``Machine.rate_min_tph`` is zero, which is true of every stacker here);
* the minute overlay compressed each bucket's tonnes to the *shortest*
  feasible duration at the maximum rate, so a steady 500 t/h allocation on a
  2,500 t/h reclaimer became a 12-minute burst plus 48 blank minutes, hour
  after hour;
* start/stop accounting was evaluated on *hourly* activity, so a contiguous
  run of such buckets counted one start and one stop while the minute
  schedule — and the Gantt drawn from it — contained dozens of blank gaps.

This module owns the policy that resolves all three.  It is deliberately free
of solver, timing and UI imports so the optimiser, the minute overlay, the
KPI layer, exports and the schedule views read exactly the same numbers.

Every default here is ``ASSUMED`` for demonstration.  None of it is a
plant-approved operating limit.  ``basis`` strings state what evidence would
replace each number, and every field is adjustable per scenario, per machine
kind and per machine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Mapping, Optional


#: Scenario config key holding the whole policy.
CAMPAIGN_POLICY_KEY = "CAMPAIGN_POLICY"

#: Machine kinds recognised for kind-scoped overrides.  An unknown kind simply
#: falls back to the scenario default block.
POLICY_MACHINE_KINDS = (
    "stacker", "reclaimer", "shiploader", "wagon_loader", "conveyor")

#: Fields of one resolved policy block, with their demonstration defaults.
#:
#: ``min_parcel_t``
#:     Smallest material parcel worth commanding in one planning bucket.  A
#:     smaller parcel is possible only when ``allow_short_parcel`` is true;
#:     that exception is priced and labelled rather than hidden.
#: ``min_run_min``
#:     Shortest continuous material run worth starting a machine for.  The
#:     minute overlay lengthens a short burst up to this duration whenever the
#:     machine's own stable-rate floor and the bucket allow it.
#: ``max_hold_min``
#:     Longest blank gap between two material bursts of the *same* activity
#:     group on the same machine that is represented as the machine being
#:     deliberately held running (belt empty, drive on).  A longer gap is an
#:     actual stop followed by an actual start and is counted as such.
#: ``min_dwell_buckets``
#:     Minimum number of consecutive planning buckets a machine stays in one
#:     activity group once it has started it.  ``1`` disables the constraint.
#: ``prefer_continuous``
#:     When true, the minute overlay prefers the *longest* execution window
#:     that keeps the rate at or above the machine's stable minimum, so
#:     adjacent buckets of one campaign join into a single continuous run
#:     instead of a train of bursts separated by blank time.
#: ``allow_short_parcel``
#:     Whether a bucket may run BELOW ``min_parcel_t`` at all. True (default)
#:     keeps the floor relaxable: a smaller parcel is possible, priced by
#:     ``SHORT_PARCEL_PENALTY`` and reported, so a genuinely necessary short
#:     job is never lost and the model can never be made infeasible by the
#:     smoothing policy alone. False makes the floor a HARD constraint, which
#:     is what an operator should select once a real minimum parcel is
#:     approved — at the risk of infeasibility or lost service, which the
#:     planner then reports as unmet demand rather than hiding.
POLICY_FIELDS: Dict[str, object] = {
    "min_parcel_t": 250.0,
    "min_run_min": 20,
    "max_hold_min": 30,
    "min_dwell_buckets": 1,
    "prefer_continuous": True,
    "allow_short_parcel": True,
}

_NUMERIC_FIELDS = ("min_parcel_t",)
_INTEGER_FIELDS = ("min_run_min", "max_hold_min", "min_dwell_buckets")
_BOOL_FIELDS = ("prefer_continuous", "allow_short_parcel")

#: Penalty applied to every bucket in which an activity group runs below
#: ``min_parcel_t``.  It is scaled by the configured equipment-priority weight
#: exactly like a start or a changeover, so an operator who does not care
#: about smoothness can disable it with the same control.
#:
#: MEASURED on the earlier FULL_FLOW S0 baseline, not the operator-default
#: BLEND_SHOWCASE profile. At the original 40 the week still carried one
#: 1 t parcel; at 4,000 it carries none AND serves 3,689 t more demand, while a
#: HARD floor (``allow_short_parcel = False``) also removes it but costs
#: 16,226 t of service. The exception is therefore kept available and simply
#: priced properly. With the default w5 of 2 (scaled coefficient 20) one
#: below-floor bucket USED TO cost 4,000 x 20 = 80,000. Scheduling revision
#: v34 separates this cost from the equipment multiplier: the effective
#: weighted-comparison price is now 4,000 x WSCALE(10) = 40,000 (4 priority-1
#: unmet tonnes at default w1). In service_first this is a last-tier preference
#: subject to explicit incumbent service protection. The historical experiment
#: is not an economic calibration or a guarantee across operating scenarios.
SHORT_PARCEL_PENALTY_KEY = "SHORT_PARCEL_PENALTY"
SHORT_PARCEL_PENALTY_DEFAULT = 4_000

#: Absolute floor kept below ``min_parcel_t`` so a genuinely necessary small
#: parcel remains representable rather than silently discarded.  One physical
#: tonne is the integer resolution of the mass model.
ABSOLUTE_MIN_PARCEL_T = 1.0

CAMPAIGN_POLICY_BASIS = (
    "ASSUMED demonstration campaign/parcel policy — NOT a plant-approved "
    "operating limit. min_parcel_t 250 t is roughly six minutes of a "
    "2,100-2,500 t/h yard machine and is chosen only to be clearly larger "
    "than the integer 1 t activity floor that produced one-minute stacking "
    "runs; min_run_min 20 min and max_hold_min 30 min are round operating "
    "figures of the same order as the documented 30-minute washing-line "
    "residence time. Replace all four with dated values from the equipment "
    "datasheets and the operating philosophy: minimum stable belt/drive "
    "loading, permitted start frequency per hour, drive-thermal restart "
    "limits, and the maximum period the plant accepts running an empty belt "
    "rather than stopping it. min_dwell_buckets is 1 (disabled) because no "
    "approved campaign-length rule exists yet."
)


@dataclass(frozen=True)
class CampaignPolicy:
    """Resolved policy for one machine."""

    min_parcel_t: float = float(POLICY_FIELDS["min_parcel_t"])
    min_run_min: int = int(POLICY_FIELDS["min_run_min"])
    max_hold_min: int = int(POLICY_FIELDS["max_hold_min"])
    min_dwell_buckets: int = int(POLICY_FIELDS["min_dwell_buckets"])
    prefer_continuous: bool = bool(POLICY_FIELDS["prefer_continuous"])
    allow_short_parcel: bool = bool(POLICY_FIELDS["allow_short_parcel"])

    def parcel_floor_t(self, stable_bucket_t: float) -> float:
        """Preferred smallest parcel, never below the machine's own floor.

        ``stable_bucket_t`` is ``Machine.rate_min_tph × bucket hours``: the
        mass the machine must already move to run at its stable minimum for a
        whole bucket.  A configured parcel smaller than that would be a
        no-op, and one larger than that is the policy addition.
        """
        return max(float(self.min_parcel_t), float(stable_bucket_t), 0.0)


DISABLED_POLICY = CampaignPolicy(
    min_parcel_t=0.0, min_run_min=0, max_hold_min=0,
    min_dwell_buckets=1, prefer_continuous=False,
    allow_short_parcel=True)


def _block_errors(prefix: str, raw: object) -> List[str]:
    if not isinstance(raw, Mapping):
        return [f"{prefix} must be a mapping"]
    errors: List[str] = []
    for key in sorted(raw):
        if str(key) not in POLICY_FIELDS:
            errors.append(
                f"{prefix}.{key} is not a campaign-policy field "
                f"({', '.join(sorted(POLICY_FIELDS))})")
    for field_name in _NUMERIC_FIELDS:
        if field_name not in raw:
            continue
        value = raw[field_name]
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = float("nan")
        if isinstance(value, bool) or not math.isfinite(number) or number < 0:
            errors.append(
                f"{prefix}.{field_name} must be a finite nonnegative number")
    for field_name in _INTEGER_FIELDS:
        if field_name not in raw:
            continue
        value = raw[field_name]
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = float("nan")
        if (isinstance(value, bool) or not math.isfinite(number)
                or number < 0 or not number.is_integer()):
            errors.append(
                f"{prefix}.{field_name} must be a nonnegative whole number")
        elif field_name == "min_dwell_buckets" and number < 1:
            errors.append(
                f"{prefix}.min_dwell_buckets must be at least 1 "
                "(1 disables the dwell constraint)")
    for field_name in _BOOL_FIELDS:
        if field_name in raw and not isinstance(raw[field_name], bool):
            errors.append(f"{prefix}.{field_name} must be true or false")
    return errors


def campaign_policy_errors(
        raw: object,
        machine_ids: Optional[Iterable[str]] = None) -> List[str]:
    """Deterministic validation errors for ``CAMPAIGN_POLICY``."""
    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return [f"{CAMPAIGN_POLICY_KEY} must be a mapping"]
    known = (None if machine_ids is None
             else {str(machine_id) for machine_id in machine_ids})
    errors: List[str] = []
    allowed_sections = {"enabled", "default", "by_machine_kind", "by_machine",
                        "basis"}
    for key in sorted(raw):
        if str(key) not in allowed_sections:
            errors.append(
                f"{CAMPAIGN_POLICY_KEY}.{key} is not a policy section "
                f"({', '.join(sorted(allowed_sections))})")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append(f"{CAMPAIGN_POLICY_KEY}.enabled must be true or false")
    if "basis" in raw and not isinstance(raw["basis"], str):
        errors.append(f"{CAMPAIGN_POLICY_KEY}.basis must be text")
    if "default" in raw:
        errors.extend(_block_errors(
            f"{CAMPAIGN_POLICY_KEY}.default", raw["default"]))
    for section, label in (("by_machine_kind", "machine kind"),
                           ("by_machine", "machine")):
        block = raw.get(section)
        if block is None:
            continue
        if not isinstance(block, Mapping):
            errors.append(f"{CAMPAIGN_POLICY_KEY}.{section} must be a mapping")
            continue
        for raw_key in sorted(block, key=str):
            key = str(raw_key).strip()
            prefix = f"{CAMPAIGN_POLICY_KEY}.{section}[{key or '<blank>'}]"
            if not key:
                errors.append(f"{prefix} {label} must not be blank")
            elif section == "by_machine_kind" and key not in POLICY_MACHINE_KINDS:
                errors.append(
                    f"{prefix} is not a recognised machine kind "
                    f"({', '.join(POLICY_MACHINE_KINDS)})")
            elif section == "by_machine" and known is not None and \
                    key not in known:
                errors.append(f"{prefix} does not match a registered machine")
            errors.extend(_block_errors(prefix, block[raw_key]))
    return errors


def _merge(base: Mapping, override: object) -> Dict[str, object]:
    merged = dict(base)
    if isinstance(override, Mapping):
        for key, value in override.items():
            if str(key) in POLICY_FIELDS:
                merged[str(key)] = value
    return merged


def campaign_policy_enabled(scenario) -> bool:
    raw = scenario.config.get(CAMPAIGN_POLICY_KEY)
    if not isinstance(raw, Mapping):
        return False
    return bool(raw.get("enabled", True))


def campaign_policy_basis(scenario) -> str:
    raw = scenario.config.get(CAMPAIGN_POLICY_KEY)
    if isinstance(raw, Mapping) and isinstance(raw.get("basis"), str):
        return str(raw["basis"])
    return CAMPAIGN_POLICY_BASIS


def resolve_campaign_policy(scenario, machine_id: str) -> CampaignPolicy:
    """Resolve default → machine kind → machine for one machine.

    An absent or disabled policy yields :data:`DISABLED_POLICY`, which every
    consumer treats as "behave exactly as before this policy existed".
    """
    raw = scenario.config.get(CAMPAIGN_POLICY_KEY)
    if not isinstance(raw, Mapping) or not bool(raw.get("enabled", True)):
        return DISABLED_POLICY
    values = _merge(POLICY_FIELDS, raw.get("default"))
    kind = ""
    try:
        kind = str(getattr(scenario.machine(machine_id), "kind", "") or "")
    except (StopIteration, AttributeError, TypeError):
        kind = ""
    by_kind = raw.get("by_machine_kind")
    if isinstance(by_kind, Mapping) and kind:
        values = _merge(values, by_kind.get(kind))
    by_machine = raw.get("by_machine")
    if isinstance(by_machine, Mapping):
        values = _merge(values, by_machine.get(str(machine_id)))
    try:
        return CampaignPolicy(
            min_parcel_t=max(float(values["min_parcel_t"]), 0.0),
            min_run_min=max(int(float(values["min_run_min"])), 0),
            max_hold_min=max(int(float(values["max_hold_min"])), 0),
            min_dwell_buckets=max(int(float(values["min_dwell_buckets"])), 1),
            prefer_continuous=bool(values["prefer_continuous"]),
            allow_short_parcel=bool(values["allow_short_parcel"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return DISABLED_POLICY


def short_parcel_penalty(scenario) -> int:
    """Validated nonnegative integer penalty for a below-parcel bucket."""
    try:
        value = int(float(scenario.config.get(
            SHORT_PARCEL_PENALTY_KEY, SHORT_PARCEL_PENALTY_DEFAULT)))
    except (TypeError, ValueError, OverflowError):
        return SHORT_PARCEL_PENALTY_DEFAULT
    return max(value, 0)


def short_parcel_penalty_errors(scenario) -> List[str]:
    raw = scenario.config.get(
        SHORT_PARCEL_PENALTY_KEY, SHORT_PARCEL_PENALTY_DEFAULT)
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = float("nan")
    if (isinstance(raw, bool) or not math.isfinite(value) or value < 0
            or not value.is_integer()):
        return [f"{SHORT_PARCEL_PENALTY_KEY} must be a whole number >= 0"]
    return []


def default_campaign_policy_config() -> Dict[str, object]:
    """The installable demonstration policy, with its basis recorded."""
    return {
        "enabled": True,
        "default": dict(POLICY_FIELDS),
        # Reclaimers here already carry a 500 t/h stable minimum, so their
        # effective parcel floor comes from the machine itself; stackers have
        # no rate floor at all and are the source of the one-tonne runs.
        "by_machine_kind": {
            "stacker": {"min_parcel_t": 250.0, "min_run_min": 20},
            "reclaimer": {"min_parcel_t": 250.0, "min_run_min": 20},
        },
        "by_machine": {},
        "basis": CAMPAIGN_POLICY_BASIS,
    }
