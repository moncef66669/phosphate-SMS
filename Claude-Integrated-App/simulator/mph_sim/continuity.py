"""Shared policy and accounting helpers for continuous receiving feeds.

The planner, Monitor KPIs and future exports must use one definition of a
feed gap.  This module deliberately has no solver or UI dependency: policies
are parsed from ordinary mappings and metrics are calculated from physical
tonnes and bucket durations.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, Iterable, Mapping, Optional, Sequence


CONTINUITY_MODES = (
    "continuous_feed",
    "batch_delivery",
    "flexible_process_campaign",
    "none",
)


@dataclass(frozen=True)
class ContinuityPolicy:
    """Validated continuity policy for one destination.

    ``minimum_tph == 0`` retains the report's initial, data-independent gap
    definition: less than one physical tonne delivered in a demand-active
    bucket.  A positive value defines a minimum stable rate, capped at the
    requested rate so low-demand buckets are not impossible by definition.
    ``max_gap_min == 0`` means that no maximum has yet been approved.

    ``minimum_service_fraction`` exists because the absolute one-tonne rule
    *rewards token deliveries*: with ``minimum_tph == 0`` a single physical
    tonne — the integer activity floor of the mass model — closed the gap for
    a whole demand-active hour, so the cheapest way to earn a perfect
    continuous-feed KPI was to deliver one tonne per hour and leave the rest
    unmet.  The fraction is applied to *this bucket's own requested mass*, so
    it corrects the incentive without asserting an unapproved plant stable
    feed rate: it says only "a fraction of what was asked for", never "at
    least N t/h".  ``0.0`` keeps the historic absolute-tonne definition.
    """

    mode: str = "none"
    minimum_tph: float = 0.0
    max_gap_min: int = 0
    minimum_service_fraction: float = 0.0

    @property
    def scored(self) -> bool:
        return self.mode == "continuous_feed"


def continuity_gap_threshold_t(
        policy: "ContinuityPolicy", demand_tph: float,
        duration_h: float, model_demand_t: float) -> float:
    """Physical tonnes that must arrive in one bucket to avoid a feed gap.

    One definition, shared by the CP-SAT gap variable, the KPI layer and every
    export, so the planner cannot be rewarded for a delivery the reports would
    later call an interruption.  The threshold never exceeds the bucket's own
    integer model demand: a fully served bucket is never a gap.
    """
    model_demand_t = max(float(model_demand_t), 0.0)
    if model_demand_t <= 0:
        return 0.0
    candidates = [1.0]
    if policy.minimum_tph > 0:
        candidates.append(
            min(float(demand_tph), float(policy.minimum_tph))
            * float(duration_h))
    if policy.minimum_service_fraction > 0:
        candidates.append(
            float(policy.minimum_service_fraction) * model_demand_t)
    return min(max(candidates), model_demand_t)


def _destination_ids(destination_ids: Optional[Iterable[str]]) -> Optional[set[str]]:
    if destination_ids is None:
        return None
    return {str(destination_id) for destination_id in destination_ids}


def continuity_policy_errors(
        raw: object,
        destination_ids: Optional[Iterable[str]] = None) -> list[str]:
    """Return deterministic validation errors for ``CONTINUITY_POLICIES``.

    The optional destination register lets scenario validation reject stale or
    misspelled destination ids while keeping this helper independent of the
    :class:`~mph_sim.domain.Scenario` type.
    """

    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        return ["CONTINUITY_POLICIES must be a mapping"]

    known = _destination_ids(destination_ids)
    errors: list[str] = []
    for raw_destination_id, raw_policy in sorted(
            raw.items(), key=lambda item: str(item[0])):
        destination_id = str(raw_destination_id).strip()
        prefix = f"CONTINUITY_POLICIES[{destination_id or '<blank>'}]"
        if not destination_id:
            errors.append(f"{prefix} destination id must not be blank")
        elif known is not None and destination_id not in known:
            errors.append(f"{prefix} does not match a configured destination")
        if not isinstance(raw_policy, Mapping):
            errors.append(f"{prefix} must be a mapping")
            continue

        mode = str(raw_policy.get("mode", "none") or "none").strip().lower()
        if mode not in CONTINUITY_MODES:
            errors.append(
                f"{prefix}.mode must be one of {', '.join(CONTINUITY_MODES)}")

        minimum = raw_policy.get("minimum_tph", 0.0)
        try:
            minimum_value = float(minimum)
        except (TypeError, ValueError, OverflowError):
            minimum_value = float("nan")
        if isinstance(minimum, bool) or not math.isfinite(minimum_value) or \
                minimum_value < 0:
            errors.append(f"{prefix}.minimum_tph must be a finite nonnegative number")

        maximum_gap = raw_policy.get("max_gap_min", 0)
        try:
            maximum_gap_value = float(maximum_gap)
        except (TypeError, ValueError, OverflowError):
            maximum_gap_value = float("nan")
        if isinstance(maximum_gap, bool) or not math.isfinite(maximum_gap_value) or \
                maximum_gap_value < 0 or not maximum_gap_value.is_integer():
            errors.append(f"{prefix}.max_gap_min must be a nonnegative integer")

        fraction = raw_policy.get("minimum_service_fraction", 0.0)
        try:
            fraction_value = float(fraction)
        except (TypeError, ValueError, OverflowError):
            fraction_value = float("nan")
        if (isinstance(fraction, bool) or not math.isfinite(fraction_value)
                or fraction_value < 0 or fraction_value > 1):
            errors.append(
                f"{prefix}.minimum_service_fraction must be between 0 and 1")

        unknown = sorted(
            str(key) for key in raw_policy
            if str(key) not in {"mode", "minimum_tph", "max_gap_min",
                                "minimum_service_fraction"})
        if unknown:
            errors.append(
                f"{prefix} has unknown fields: {', '.join(unknown)}")
    return errors


def parse_continuity_policies(
        raw: object,
        destination_ids: Optional[Iterable[str]] = None,
        ) -> Dict[str, ContinuityPolicy]:
    """Parse a validated policy mapping, raising ``ValueError`` on bad input."""

    errors = continuity_policy_errors(raw, destination_ids)
    if errors:
        raise ValueError("; ".join(errors))
    if raw is None:
        return {}
    assert isinstance(raw, Mapping)  # established by validation
    return {
        str(destination_id).strip(): ContinuityPolicy(
            mode=str(policy.get("mode", "none") or "none").strip().lower(),
            minimum_tph=float(policy.get("minimum_tph", 0.0)),
            max_gap_min=int(float(policy.get("max_gap_min", 0))),
            minimum_service_fraction=float(
                policy.get("minimum_service_fraction", 0.0)),
        )
        for destination_id, policy in sorted(
            raw.items(), key=lambda item: str(item[0]))
    }


def _series_value(values: Sequence[float], index: int) -> float:
    if index < 0 or index >= len(values):
        return 0.0
    try:
        value = float(values[index])
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def compute_continuity_metrics(
        policies: Mapping[str, ContinuityPolicy] | object,
        demand_tph_by_destination: Mapping[str, Sequence[float]],
        delivered_t_by_destination: Mapping[str, Sequence[float]],
        durations_h: Sequence[float], *,
        offset: int = 0,
        initial_gap_by_destination: Optional[Mapping[str, bool]] = None,
        initial_open_gap_minutes: Optional[Mapping[str, float]] = None,
        ) -> Dict[str, object]:
    """Calculate continuous-feed gaps from physical delivery series.

    ``demand_tph_by_destination`` is on the canonical scenario timeline and is
    read at ``bucket + offset``.  Delivered values are local *physical tonnes*
    for the evaluated window.  ``durations_h`` therefore handles full, partial
    and fine-grid buckets without assuming a fixed resolution.

    Initial state is optional and primarily supports rolling-window merging.
    Supplying both the open flag and its elapsed minutes preserves episode
    frequency and maximum duration across a boundary.
    """

    if not isinstance(policies, Mapping) or any(
            not isinstance(policy, ContinuityPolicy)
            for policy in policies.values()):
        policies = parse_continuity_policies(policies)

    initial_gaps = initial_gap_by_destination or {}
    initial_minutes = initial_open_gap_minutes or {}
    per_destination: Dict[str, Dict[str, object]] = {}
    total_gap_minutes = 0.0
    total_episodes = 0
    total_token_buckets = 0
    total_token_t = 0.0
    maximum_episode_minutes = 0.0
    terminal_gap_by_destination: Dict[str, bool] = {}
    terminal_open_gap_minutes: Dict[str, float] = {}

    for destination_id, policy in sorted(policies.items()):
        if not policy.scored:
            continue
        demand_series = demand_tph_by_destination.get(destination_id, ())
        delivered_series = delivered_t_by_destination.get(destination_id, ())
        previous_gap = bool(initial_gaps.get(destination_id, False))
        open_gap_minutes = (
            max(float(initial_minutes.get(destination_id, 0.0)), 0.0)
            if previous_gap else 0.0)
        gap_minutes = 0.0
        episode_count = 0
        max_episode = open_gap_minutes
        token_buckets = 0
        token_t = 0.0

        for local_bucket, raw_duration_h in enumerate(durations_h):
            try:
                duration_h = float(raw_duration_h)
            except (TypeError, ValueError, OverflowError):
                duration_h = 0.0
            if not math.isfinite(duration_h) or duration_h <= 0:
                current_gap = False
            else:
                demand_tph = max(_series_value(
                    demand_series, local_bucket + int(offset)), 0.0)
                delivered_t = max(_series_value(
                    delivered_series, local_bucket), 0.0)
                # The optimizer balances whole physical tonnes and quantizes
                # each bucket's requested mass with round(). Cap the policy
                # threshold at that same model demand so a fully served 10 t
                # integer request derived from 10.5 t/h is not later reported
                # as a gap. Fractional actual delivery is otherwise compared
                # against the physical rate threshold without rounding.
                model_demand_t = max(int(round(
                    demand_tph * duration_h)), 0)
                threshold_t = continuity_gap_threshold_t(
                    policy, demand_tph, duration_h, model_demand_t)
                current_gap = bool(
                    threshold_t > 0 and delivered_t + 1e-9 < threshold_t)
                # A delivery that is positive yet below the threshold is a
                # token delivery: it must be reported, not quietly counted as
                # either full service or a complete outage.
                if current_gap and delivered_t > 1e-9:
                    token_buckets += 1
                    token_t += delivered_t

            if current_gap:
                duration_min = duration_h * 60.0
                gap_minutes += duration_min
                if not previous_gap:
                    episode_count += 1
                    open_gap_minutes = 0.0
                open_gap_minutes += duration_min
                max_episode = max(max_episode, open_gap_minutes)
            else:
                open_gap_minutes = 0.0
            previous_gap = current_gap

        exceeded = bool(
            policy.max_gap_min > 0 and max_episode > policy.max_gap_min + 1e-9)
        row: Dict[str, object] = {
            "gap_minutes": gap_minutes,
            "gap_hours": gap_minutes / 60.0,
            "episode_count": episode_count,
            "max_episode_minutes": max_episode,
            "terminal_gap": previous_gap,
            "terminal_open_gap_minutes": open_gap_minutes,
            "minimum_tph": policy.minimum_tph,
            "minimum_service_fraction": policy.minimum_service_fraction,
            "max_gap_min": policy.max_gap_min,
            "max_gap_exceeded": exceeded,
            "token_delivery_buckets": token_buckets,
            "token_delivery_t": token_t,
        }
        per_destination[destination_id] = row
        terminal_gap_by_destination[destination_id] = previous_gap
        terminal_open_gap_minutes[destination_id] = open_gap_minutes
        total_gap_minutes += gap_minutes
        total_episodes += episode_count
        total_token_buckets += token_buckets
        total_token_t += token_t
        maximum_episode_minutes = max(maximum_episode_minutes, max_episode)

    return {
        "per_destination": per_destination,
        "gap_minutes": total_gap_minutes,
        "gap_hours": total_gap_minutes / 60.0,
        "episode_count": total_episodes,
        "token_delivery_buckets": total_token_buckets,
        "token_delivery_t": total_token_t,
        "max_episode_minutes": maximum_episode_minutes,
        "terminal_gap": any(terminal_gap_by_destination.values()),
        "terminal_gap_by_destination": terminal_gap_by_destination,
        "terminal_open_gap_minutes": terminal_open_gap_minutes,
    }
