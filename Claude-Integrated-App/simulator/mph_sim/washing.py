"""Provisional washing calibration and grade-resolved material accounting.

Chemistry is a dry-solids concentration; moisture is a wet-basis percentage.
No existing stock is retroactively treated or assigned missing assays.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math

from .domain import DemandEntry, Evidence, Grade, SpecRange, UNKNOWN_BELT_QUALITY_KEY

CONFIG_KEY = "WASHING_TREATMENTS"
MODEL_VERSION = 7
COST_UNIT = "$"
PAP_DESTINATION_IDS = ("PAP-1", "PAP-2")
PARAMETERS = ("BPL", "Cd", "MgO", "SiO2", "CO2", "moisture")
# Project-team supplied G10 receiving profile (19-Sep-2026).  The source gives
# no useful upper BPL bound, so 100% is the neutral concentration ceiling.  The
# H2O limit is a receiving constraint only: the raw-grade moisture and the
# established +20% relative treatment response remain unchanged.
# Cd carries a cap only.  The transcribed profile stated 12 ppm as a lower
# bound, which would have made PAP reject cleaner-than-required material and
# put G10 in permanent conflict with every BG train-export profile (all of
# which cap Cd at 7-12 ppm).  Project-team ruling 25-Sep-2026: read as a cap.
DEFAULT_LIMITS = {"BPL": [62.5, 100.], "Cd": [0., 18.], "MgO": [0., .8],
                  "SiO2": [0., 7.5], "CO2": [6., 6.5],
                  "moisture": [0., 25.]}
#: Superseded Cd interval retained so stored v6 overlays migrate exactly once.
_V6_CD_LIMIT = [12., 18.]
DEFAULT_MODES = {
    "simple": {"name": "Simple washing", "dry_solids_yield": .85,
               "changes_pct": dict(zip(
                   PARAMETERS, (12., -5., -30., -28., -26., 20.))),
               "cost_per_tsm_t": .63, "residence_min": None,
               "available": True},
    "full": {"name": "Full washing", "dry_solids_yield": .65,
             "changes_pct": dict(zip(
                 PARAMETERS, (23., -20., -64., -50., -27., 20.))),
             "cost_per_tsm_t": 1.04, "residence_min": None,
             "available": True},
}
_V4_PERMISSIVE_CHANGES = {
    "simple": dict(zip(
        PARAMETERS, (17., -20., -30., -60., -50., 20.))),
    "full": dict(zip(
        PARAMETERS, (45., -60., -60., -80., -70., 20.))),
}
_V5_SELECTIVE_CHANGES = {
    "simple": dict(zip(
        PARAMETERS, (12., -15., -15., -45., -50., 20.))),
    "full": dict(zip(
        PARAMETERS, (24., -40., -45., -70., -60., 20.))),
}
_V5_LIMITS = {"BPL": [65., 90.], "Cd": [0., 18.], "MgO": [0., 1.2],
              "SiO2": [0., 6.], "CO2": [0., 4.5],
              "moisture": [0., 20.]}


#: Receivers that may *request* G10 without being bound by the PAP contract.
#: The dryer serves the same specification as PAP; it is not a PAP line, so it
#: must not acquire PAP's forced requested_grade/spec in ``synchronize_pap``.
DEFAULT_G10_ADDITIONAL_RECEIVERS = ("DRY-FEED",)


def defaults():
    return dict(model_version=MODEL_VERSION, enabled=False,
                evidence=(
                    "PROVISIONAL_G10_BEHAVIOR_CALIBRATION_NOT_PLANT_VALIDATED"),
                modes=deepcopy(DEFAULT_MODES), g10_limits=deepcopy(DEFAULT_LIMITS),
                cost_unit=COST_UNIT,
                pap_destinations=list(PAP_DESTINATION_IDS),
                g10_additional_receivers=list(
                    DEFAULT_G10_ADDITIONAL_RECEIVERS))


def _merged_settings(raw):
    """Merge defaults and migrate only recognizable legacy demo defaults."""
    result = defaults()
    if not isinstance(raw, dict):
        return result
    result.update(deepcopy(raw))
    # Project economics use USD throughout. Retain the serialized field for
    # backward compatibility, but never allow a scenario overlay to redefine
    # the currency presented or used by downstream KPI tables.
    result["cost_unit"] = COST_UNIT
    try:
        version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= MODEL_VERSION:
        return result

    modes = deepcopy(result.get("modes", {}))
    if isinstance(modes, dict):
        simple = modes.get("simple", {})
        full = modes.get("full", {})
        if isinstance(simple, dict) and isinstance(full, dict):
            if simple.get("dry_solids_yield") == .70:
                simple["dry_solids_yield"] = .85
            if full.get("dry_solids_yield") == .60:
                full["dry_solids_yield"] = .65
            simple_changes = simple.get("changes_pct", {})
            full_changes = full.get("changes_pct", {})
            if isinstance(simple_changes, dict):
                if simple_changes.get("BPL") == 35.:
                    simple_changes["BPL"] = 17.
                if simple_changes.get("moisture") == 0.:
                    simple_changes["moisture"] = 20.
            if (isinstance(full_changes, dict)
                    and full_changes.get("moisture") == 0.):
                full_changes["moisture"] = 20.
            # Replace a complete untouched v4/v5 built-in vector only. This
            # preserves operator-calibrated modes while migrating the baseline
            # to the user-supplied TBT/G10 chemistry calibration.
            for key, changes in (
                    ("simple", simple_changes), ("full", full_changes)):
                if changes in (
                        _V4_PERMISSIVE_CHANGES[key],
                        _V5_SELECTIVE_CHANGES[key]):
                    replacement = deepcopy(
                        DEFAULT_MODES[key]["changes_pct"])
                    if key == "simple":
                        simple["changes_pct"] = replacement
                        simple_changes = replacement
                    else:
                        full["changes_pct"] = replacement
                        full_changes = replacement
            for key, mode in (("simple", simple), ("full", full)):
                if "cost_per_tsm_t" not in mode:
                    legacy_cost = mode.pop("cost_per_dry_feed_t", None)
                    if legacy_cost is None:
                        mode["cost_per_tsm_t"] = \
                            DEFAULT_MODES[key]["cost_per_tsm_t"]
                    else:
                        mode["cost_per_tsm_t"] = (
                            float(legacy_cost)
                            / float(mode["dry_solids_yield"]))
            result["modes"] = modes
    if result.get("evidence") == "SYNTHETIC_DEMO_NOT_PLANT_VALIDATED":
        result["evidence"] = (
            "PROVISIONAL_G10_BEHAVIOR_CALIBRATION_NOT_PLANT_VALIDATED")
    limits = deepcopy(result.get("g10_limits", {}))
    if (isinstance(limits, dict)
            and limits.get("BPL") == [71., 90.]):
        limits["BPL"] = [65., 90.]
    if limits == _V5_LIMITS:
        limits = deepcopy(DEFAULT_LIMITS)
    # Drop the superseded Cd lower bound without disturbing an operator who
    # deliberately narrowed the interval to something else.
    if isinstance(limits, dict) and limits.get("Cd") == _V6_CD_LIMIT:
        limits["Cd"] = list(DEFAULT_LIMITS["Cd"])
    result["g10_limits"] = limits
    result["model_version"] = MODEL_VERSION
    return result


def settings(sc):
    raw = sc.config.get(CONFIG_KEY, {})
    return _merged_settings(raw)


def enabled(sc):
    return settings(sc).get("enabled") is True


def _number(value, name, lower, upper=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < lower
            or (upper is not None and value > upper)):
        raise ValueError(f"{name}: expected a finite number in [{lower}, {upper}]")
    return float(value)


def configuration_errors(sc_or_settings):
    raw = (sc_or_settings.config.get(CONFIG_KEY, {})
           if hasattr(sc_or_settings, "config") else sc_or_settings)
    if raw == {}:
        return []
    try:
        if not isinstance(raw, dict):
            raise ValueError("washing configuration must be an object")
        cfg = _merged_settings(raw)
        if type(cfg["enabled"]) is not bool:
            raise ValueError("enabled must be boolean")
        modes = cfg["modes"]
        if not isinstance(modes, dict) or set(modes) != {"simple", "full"}:
            raise ValueError("modes must contain simple and full")
        for key, mode in modes.items():
            if not isinstance(mode, dict) or type(mode.get("available")) is not bool:
                raise ValueError(f"{key}: invalid mode or availability")
            yield_ = _number(mode.get("dry_solids_yield"), f"{key} yield", 0, 1)
            if yield_ == 0:
                raise ValueError(f"{key}: yield must be positive")
            changes = mode.get("changes_pct")
            if not isinstance(changes, dict) or set(changes) != set(PARAMETERS):
                raise ValueError(f"{key}: provide changes for all six characteristics")
            for parameter, change in changes.items():
                _number(change, f"{key}/{parameter} change", -100, 1000)
            if yield_ * (1 + changes["BPL"] / 100) > 1 + 1e-9:
                raise ValueError(f"{key}: BPL enrichment × dry yield exceeds feed phosphorus")
            for field in ("cost_per_tsm_t", "residence_min"):
                if mode.get(field) is not None:
                    _number(mode[field], f"{key}/{field}", 0, 1_000_000)
            if mode.get("residence_min") is not None and int(mode["residence_min"]) != mode["residence_min"]:
                raise ValueError(f"{key}: residence must be whole minutes")
        limits = cfg["g10_limits"]
        available_costs = [mode["cost_per_tsm_t"] for mode in modes.values() if mode["available"]]
        if any(cost is not None for cost in available_costs) and any(cost is None for cost in available_costs):
            raise ValueError("provide costs for every available option, or leave all costs unconfigured; an unpriced option is not free")
        if not isinstance(limits, dict) or set(limits) != set(PARAMETERS):
            raise ValueError("provide Demo G10 limits for all six characteristics")
        for parameter, bounds in limits.items():
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ValueError(f"G10/{parameter}: expected [min, max]")
            lo = _number(bounds[0], f"G10/{parameter} min", 0)
            hi = _number(bounds[1], f"G10/{parameter} max", lo, None if parameter == "Cd" else 100)
            if parameter == "moisture" and hi >= 100:
                raise ValueError("G10 moisture must be below 100%")
        if not isinstance(cfg.get("pap_destinations"), list) or any(not isinstance(x, str) for x in cfg["pap_destinations"]):
            raise ValueError("pap_destinations must be an identifier list")
        if cfg["enabled"] and hasattr(sc_or_settings, "spaces"):
            for space in sc_or_settings.spaces:
                grade = sc_or_settings.grade(space.grade)
                if space.mass_t > 0 and grade is not None and grade.is_specification:
                    raise ValueError(f"{space.id}: a receiving specification is not physical stock; provide its actual material grade and assays")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return ["WASHING_TREATMENTS " + str(exc)]
    return []


def treated_quality(raw_quality, mode):
    """Relative concentration changes, never additive percentage points."""
    result = dict(raw_quality)
    for parameter, change in mode["changes_pct"].items():
        if parameter not in raw_quality:
            continue  # Missing assays remain missing, never zero-valued diluent.
        value = _number(raw_quality[parameter], parameter, 0) * (1 + change / 100)
        if value < 0 or (parameter != "Cd" and value > 100) or (parameter == "moisture" and value >= 100):
            raise ValueError(f"{parameter}: treatment produces an invalid concentration")
        result[parameter] = round(value, 12)
    # A ratio cannot be transformed from CO2 alone; CaO is not supplied.
    result.pop("CaO_CO2", None)
    return result


def output_factor(raw_quality, product_quality, dry_yield):
    raw_dry = 1 - _number(raw_quality.get("moisture"), "feed moisture", 0, 99.999999) / 100
    product_dry = 1 - _number(product_quality.get("moisture"), "product moisture", 0, 99.999999) / 100
    factor = raw_dry * dry_yield / product_dry
    if not 0 < factor <= 1 + 1e-9:
        raise ValueError("physical washing output exceeds feed mass; external water is not modelled")
    return min(factor, 1.)


def g10_spec(cfg):
    return [SpecRange(parameter, pmin=float(bounds[0]), pmax=float(bounds[1]),
                      target=min(max(75., bounds[0]), bounds[1]) if parameter == "BPL" else None, hard=True)
            for parameter, bounds in cfg["g10_limits"].items()]


def synchronize_pap(sc):
    """Make G10 the permanent PAP receiving contract; never relabel stock.

    The treatment toggle controls whether washing decisions are generated. It
    does not change what PAP is allowed to request or receive.
    """
    cfg = settings(sc)
    selected = set(cfg["pap_destinations"])
    if not any(destination.id in selected for destination in sc.destinations):
        return False
    changed = False
    specification = g10_spec(cfg)
    # Who may *request* G10 is wider than who is bound by the PAP contract:
    # the dryer draws the same specification but keeps its own demand grades.
    existing = {destination.id for destination in sc.destinations}
    allowed = sorted(selected | {
        destination_id
        for destination_id in cfg.get("g10_additional_receivers", []) or []
        if destination_id in existing})
    if sc.grade("G10") is None:
        sc.grades.append(Grade("G10", "Demo G10 — receiving specification only",
                               material_class="specification", is_specification=True,
                               receiving_spec=specification,
                               receiving_destinations=allowed,
                               evidence=Evidence(status="ASSUMED", note=cfg["evidence"])))
        changed = True
    elif not getattr(sc.grade("G10"), "is_specification", False):
        raise ValueError("G10 already exists as a material grade; do not overwrite it")
    else:
        grade = sc.grade("G10")
        if grade.receiving_spec != specification or \
                grade.receiving_destinations != allowed:
            changed = True
        grade.receiving_spec = specification
        grade.receiving_destinations = allowed
    for destination in sc.destinations:
        if destination.id in selected:
            if destination.requested_grade != "G10" or \
                    destination.spec != specification:
                changed = True
            destination.requested_grade = "G10"
            destination.spec = deepcopy(specification)
    for entry in sc.demand:
        if entry.dest_id in selected:
            if entry.requested_grade != "G10":
                changed = True
            entry.requested_grade = "G10"
    return changed


def reconcile_derived_policy(sc):
    """Replace only untouched preset feed forecasts, not operator commitments.

    A fixed old-yield raw quota + fixed replenished stock + fixed washed output
    can contradict a new recovery. Output orders keep their existing semantics. Replenishment
    balances determine actual upstream raw handling; preset feed rows become
    ceilings sufficient for either available option.
    """
    if not enabled(sc) or not sc.name.startswith("S3 —"):
        return
    from .datasets import end_to_end, apply_planning_demand_profile, PLANNING_DEMAND_PROFILES
    profile = sc.config.get("ACTIVE_PLANNING_DEMAND_PROFILE")
    if profile not in PLANNING_DEMAND_PROFILES:
        return
    from . import prewash_blending
    transfers_by_stage = {}
    for transfer in sc.process_transfers:
        if is_washing_transfer(transfer):
            transfers_by_stage.setdefault(
                transfer.process_stage, []).append(transfer)
    feed_ids = {dest.id for dest in sc.destinations if dest.demand_kind == "process_feed"
                and dest.process_stage in transfers_by_stage}
    current_rows = [asdict(entry) for entry in sc.demand if entry.dest_id in feed_ids]
    targets = deepcopy(sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}))
    backup = sc.config.get("_WASHING_PREVIOUS_DERIVED_POLICY", {})
    if (backup and current_rows == backup.get("installed_rows")
            and targets == backup.get("installed_targets")):
        original_rows = backup["rows"]
    else:
        baseline = end_to_end()
        # Compare like-for-like preset forecasts. A shortened study horizon
        # (or another civil-day origin/grid) changes both feed rows and the
        # derived output targets; comparing it with the default 168 h week
        # incorrectly labels untouched forecasts as operator commitments.
        baseline.sim = deepcopy(sc.sim)
        apply_planning_demand_profile(baseline, profile)
        expected_rows = [asdict(entry) for entry in baseline.demand if entry.dest_id in feed_ids]
        expected_targets = baseline.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {})
        if current_rows != expected_rows or targets != expected_targets:
            sc.config["_WASHING_DERIVED_POLICY_NOTE"] = (
                "Custom raw-feed/production commitments retained. Review their compatibility with the configured washing recovery.")
            return
        original_rows = current_rows
        backup = dict(rows=deepcopy(original_rows), targets=deepcopy(targets))
    cfg = settings(sc)
    new_rows = deepcopy(original_rows)
    for dest in sc.destinations:
        if dest.id not in feed_ids or not targets.get(dest.process_stage):
            continue
        transfers = transfers_by_stage[dest.process_stage]
        blend_sources = prewash_blending.source_spaces_for_stage(
            sc, dest.process_stage)
        if blend_sources:
            source_spaces = sorted(blend_sources)
        else:
            requested = str(dest.requested_grade or "")
            matching = [
                transfer.source_space for transfer in transfers
                if not requested
                or sc.space(transfer.source_space).grade == requested]
            source_spaces = matching[:1] or [transfers[0].source_space]
        # Feed rows are conservative ceilings. With an optimized blend they
        # must cover the lowest physical output factor reachable inside the
        # configured source/mode envelope, independent of transfer ordering.
        factors = []
        for source_space in source_spaces:
            quality = sc.quality_of(source_space)
            factors.extend(
                output_factor(
                    quality, treated_quality(quality, mode),
                    mode["dry_solids_yield"])
                for mode in cfg["modes"].values() if mode["available"])
        if not factors:
            continue
        total_feed = math.ceil(float(targets[dest.process_stage]) / min(factors) - 1e-9)
        indices = [index for index, row in enumerate(new_rows) if row["dest_id"] == dest.id]
        weights = [max(float(new_rows[index]["rate_tph"]), 0.) for index in indices]
        total_weight = sum(weights)
        if total_weight <= 0:
            continue
        amounts = [total_feed * weight / total_weight for weight in weights]
        whole = [math.floor(amount) for amount in amounts]
        order = sorted(range(len(amounts)), key=lambda index: (-(amounts[index] - whole[index]), index))
        for index in order[:total_feed - sum(whole)]:
            whole[index] += 1
        for index, mass in zip(indices, whole):
            new_rows[index]["rate_tph"] = mass / sc.sim.dt_h
    # Replenished raw inventories already enforce stack = reclaim = feed.
    # Their old, preset-derived exact tonnages cannot also fix the new yield.
    new_targets = {stage: amount for stage, amount in targets.items()
                   if not stage.startswith(("BEN-MINE-STACK", "BEN-MINE-RECLAIM", "BEN-RAW-STACK"))}
    sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = new_targets
    replacements = {(row["dest_id"], row["bucket"]): row for row in new_rows}
    sc.demand = [DemandEntry(**replacements[(entry.dest_id, entry.bucket)])
                 if (entry.dest_id, entry.bucket) in replacements else entry for entry in sc.demand]
    backup.update(installed_rows=deepcopy(new_rows), installed_targets=deepcopy(new_targets))
    sc.config["_WASHING_PREVIOUS_DERIVED_POLICY"] = backup
    sc.config["_WASHING_DERIVED_POLICY_NOTE"] = (
        "Untouched preset raw-feed forecasts are yield-aware ceilings; actual upstream replenishment follows material balances. "
        "Output/corridor orders retain their hard-or-forecast status; terminal-stock constraints and operator-fixed jobs remain hard.")


def stage_configuration(sc, cfg):
    errors = configuration_errors(cfg)
    if errors:
        raise ValueError(" · ".join(errors))
    staged = deepcopy(sc)
    previously = enabled(sc)
    staged.config[CONFIG_KEY] = deepcopy(cfg)
    for key in list(staged.config):
        if key.startswith("_WASHING_MODEL_"):
            del staged.config[key]
    staged.config.pop("_WASHING_PREVIOUS_PAP", None)
    synchronize_pap(staged)
    if cfg["enabled"]:
        staged.config["OBJECTIVE_MODE"] = "service_first"
        reconcile_derived_policy(staged)
    elif previously:
        policy = staged.config.pop("_WASHING_PREVIOUS_DERIVED_POLICY", {})
        if policy and ([asdict(entry) for entry in staged.demand
                       if entry.dest_id in {row["dest_id"] for row in policy["rows"]}] == policy.get("installed_rows")
                       and staged.config.get("PROCESS_STAGE_OUTPUT_TARGET_T") == policy.get("installed_targets")):
            staged.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = policy["targets"]
            rows = {(row["dest_id"], row["bucket"]): row for row in policy["rows"]}
            staged.demand = [DemandEntry(**rows[(entry.dest_id, entry.bucket)])
                             if (entry.dest_id, entry.bucket) in rows else entry for entry in staged.demand]
        staged.config.pop("_WASHING_DERIVED_POLICY_NOTE", None)
    return staged


def is_washing_transfer(transfer):
    stage = str(transfer.process_stage).upper()
    return (stage.startswith(("LOU-WASH-L", "BEN-WASH-L")) or
            stage.startswith("BG-L") and stage.endswith("-WASH") or
            str(getattr(transfer, "treatment_family", "")) == "washing")


def _register(sc, catalogue, record):
    encoded = json.dumps(record, sort_keys=True, allow_nan=False)
    mid = "MAT-" + hashlib.sha256(encoded.encode()).hexdigest()[:20]
    catalogue.setdefault(mid, record)
    return mid


def _integer_pools(pools, physical_t):
    """Quantize replay floats without gaining/losing a physical tonne."""
    clean = {mid: _number(mass, f"pool {mid} mass", 0) for mid, mass in pools.items()}
    if not math.isclose(sum(clean.values()), physical_t, abs_tol=1., rel_tol=1e-8):
        raise ValueError("material-pool mass does not match physical inventory")
    total = int(round(physical_t))
    whole = {mid: math.floor(mass + 1e-9) for mid, mass in clean.items()}
    order = sorted(clean, key=lambda mid: (-(clean[mid] - whole[mid]), mid))
    remainder = total - sum(whole.values())
    if remainder < 0 or remainder > len(order):
        raise ValueError("material-pool rounding is inconsistent with physical inventory")
    for mid in order[:remainder]:
        whole[mid] += 1
    return whole


def prepare_materials(sc, base_candidates):
    """Expand actual reachable streams, retaining one physical inventory cap.

    Material pools are an explicit selective/campaign withdrawal approximation,
    not a prediction of spatial mixing, stockpile faces or layer progression.
    """
    out = deepcopy(sc)
    catalogue = deepcopy(out.config.get("_WASHING_MODEL_CATALOG", {}))
    seeds = deepcopy(out.config.get("_WASHING_MODEL_INITIAL", {}))
    possible = {space.id: {mid for mid, mass in seeds.get(space.id, {}).items() if mass > 0}
                for space in out.spaces}
    for space in out.spaces:
        if space.id not in seeds:
            quality = dict(out.quality_of(space.id))
            mid = _register(out, catalogue, dict(grade_id=space.grade, quality=quality,
                            material_class=space.material_class, treatment_id="",
                            source_grade_id=space.grade))
            seeds[space.id] = {mid: min(round(space.mass_t), round(space.capacity_t))}
            # An empty pile's label is not an actual material source. Its
            # reachable grades come from upstream stocks/flows, not the label.
            if seeds[space.id][mid] > 0:
                possible[space.id].add(mid)
        else:
            seeds[space.id] = _integer_pools(seeds[space.id], min(space.mass_t, space.capacity_t))
    exogenous = {}
    for index, flow in enumerate(out.exogenous):
        grade = out.grade(flow.grade)
        quality = dict(flow.quality or (grade.quality if grade else {}))
        record = dict(grade_id=flow.grade, quality=quality,
                      material_class=grade.material_class if grade else "",
                      treatment_id="", source_grade_id=flow.grade)
        mid = _register(out, catalogue, record)
        if flow.source_space:
            matches = [key for key in possible[flow.source_space]
                       if catalogue[key]["grade_id"] == flow.grade]
            if len(matches) != 1:
                raise ValueError(f"exogenous flow {index}: selective source grade must identify one material pool")
            mid = matches[0]
        exogenous[str(index)] = mid
        if flow.target_space in possible:
            possible[flow.target_space].add(mid)
    for parcel_index, parcel in enumerate(out.belt_profile):
        grade = out.grade(parcel.grade)
        quality = (dict(parcel.quality) if parcel.quality or parcel_index in
                   out.config.get(UNKNOWN_BELT_QUALITY_KEY, [])
                   else dict(grade.quality if grade else {}))
        record = dict(grade_id=parcel.grade,
                      quality=quality,
                      material_class=grade.material_class if grade else "washed",
                      treatment_id="", source_grade_id=parcel.grade)
        mid = _register(out, catalogue, record)
        if parcel.material_id in catalogue:
            mid = parcel.material_id
            record = catalogue[mid]
        parcel.material_id = mid
        for candidate in base_candidates:
            if candidate.kind == "stack" and candidate.exit_node == out.link(parcel.link_id).dst:
                if record["material_class"] in ("", "washed", "dry"):
                    possible[candidate.space].add(mid)
    cfg = settings(out)
    transfers = {transfer.id: transfer for transfer in out.process_transfers}
    process_only = {link for transfer in out.process_transfers for link in transfer.path}
    built = {}

    def transformed(candidate, mid, mode_key=""):
        record = catalogue[mid]
        transfer = transfers[candidate.transfer_id]
        if mode_key:
            if not record["material_class"].startswith("raw"):
                return None
            mode = cfg["modes"][mode_key]
            q = treated_quality(record["quality"], mode)
            factor = output_factor(record["quality"], q, mode["dry_solids_yield"])
            gid = f"WASH-{mode_key.upper()}-{record['grade_id']}"
            result = dict(grade_id=gid, quality=q, material_class="washed",
                          treatment_id=mode_key, source_grade_id=record["grade_id"])
            if out.grade(gid) is None:
                out.grades.append(Grade(gid, f"{mode['name']} · {record['grade_id']} · DEMO",
                    quality=q, material_class="washed", source_grade_id=record["grade_id"],
                    beneficiation_yield=mode["dry_solids_yield"],
                    raw_moisture_pct=record["quality"]["moisture"],
                    product_moisture_pct=q["moisture"],
                    evidence=Evidence(status="ASSUMED", note=cfg["evidence"])))
        elif transfer.mass_conversion == "drying":
            factor = 1 - float(record["quality"].get("moisture", 0)) / 100
            result = {**record, "grade_id": record["grade_id"] + "-DRY",
                      "quality": {**record["quality"], "moisture": 0.}, "material_class": "dry"}
            if out.grade(result["grade_id"]) is None:
                out.grades.append(Grade(result["grade_id"], result["grade_id"],
                    quality=result["quality"], material_class="dry", evidence=Evidence(status="ASSUMED")))
        else:
            factor = candidate.yield_factor
            result = record  # screening, conveyors and stacking preserve assays
        omid = _register(out, catalogue, result)
        return omid, factor

    # Fixed-point propagation of only physically reachable material streams.
    for iteration in range(len(out.spaces) + 2):
        changed = False
        for index, candidate in enumerate(base_candidates):
            if candidate.kind == "process":
                transfer = transfers[candidate.transfer_id]
                mode_keys = [key for key, mode in cfg["modes"].items() if mode["available"]] if is_washing_transfer(transfer) else [""]
                for mid in sorted(possible[candidate.source_space]):
                    for mode_key in mode_keys:
                        transformed_result = transformed(candidate, mid, mode_key)
                        if transformed_result is None:
                            continue
                        omid, factor = transformed_result
                        if omid not in possible[candidate.target_space]:
                            possible[candidate.target_space].add(omid)
                            changed = True
                        built[(index, mid, mode_key)] = (omid, factor)
            elif candidate.kind == "dispatch":
                for receiver in base_candidates:
                    if receiver.kind != "stack" or receiver.exit_node != candidate.exit_node:
                        continue
                    for mid in sorted(possible[candidate.space]):
                        if catalogue[mid]["material_class"] in ("", "washed", "dry") and mid not in possible[receiver.space]:
                            possible[receiver.space].add(mid)
                            changed = True
        if not changed:
            break
    else:
        raise ValueError("washing material graph does not reach a finite fixed point")

    expanded = []
    seen = set()
    from .blending import chevron_is_demand_driven, chevron_settings
    managed = set(chevron_settings(out)["active_spaces"])
    dynamic_managed = set()
    if chevron_is_demand_driven(out):
        blend_settings = chevron_settings(out)
        dynamic_managed = set(managed) | {
            str(sid) for sid, state in
            blend_settings["campaign_states_by_space"].items()
            if str(state.get("status", "")) in {"BUILDING", "COMPLETE"}}
    for index, candidate in enumerate(base_candidates):
        if candidate.kind == "reclaim" and candidate.space in dynamic_managed:
            if process_only.intersection(candidate.path.links):
                continue
            # A completed blend is one physical mixture.  It must not appear
            # as independently selectable constituent-grade material pools.
            # The optimizer qualifies its actual mixture against the chosen
            # receiver's limits and records any receiving-grade certification.
            expanded.append(replace(
                candidate, grade=f"BLEND-{candidate.space}",
                material_id="", output_material_id="", quality_override=None))
            continue
        if candidate.kind == "process":
            items = [(mid, mode, omid, factor) for (i, mid, mode), (omid, factor) in built.items() if i == index]
        elif candidate.kind == "stack":
            # The original anti-contamination/recipe eligibility remains authoritative
            # for existing campaigns. New washed products use ordinary product piles.
            items = [(mid, "", mid, 1.) for mid in sorted(possible[candidate.space])
                     if catalogue[mid]["grade_id"] == candidate.grade or
                     candidate.space not in managed and catalogue[mid]["treatment_id"]]
        else:
            if candidate.kind == "reclaim" and process_only.intersection(candidate.path.links):
                continue  # ordinary conveyors may not bypass a washing boundary
            items = [(mid, "", mid, 1.) for mid in sorted(possible[candidate.space])]
        for mid, mode_key, omid, factor in items:
            record = catalogue[mid]
            signature = (candidate.kind, candidate.machine, candidate.space, candidate.dest_id,
                         candidate.transfer_id, tuple(candidate.path.links), mid, mode_key)
            if signature in seen:
                continue
            seen.add(signature)
            copy_ = replace(candidate, grade=record["grade_id"], material_id=mid,
                            output_material_id=omid, quality_override=deepcopy(record["quality"]),
                            treatment_id=mode_key, yield_factor=factor,
                            output_grade=catalogue[omid]["grade_id"])
            if candidate.kind == "stack" and candidate.space in managed:
                copy_.purpose = (candidate.purpose + " · Chevron component").strip(" ·")
            if not mode_key and candidate.kind != "stack":
                upstream = [transfer for transfer in transfers.values()
                            if transfer.target_space == candidate.input_space()
                            and is_washing_transfer(transfer)]
                if upstream and record["treatment_id"]:
                    parent = upstream[0]
                    parent_mode = cfg["modes"][record["treatment_id"]]
                    delay = parent_mode["residence_min"]
                    copy_.depends_on_stage = parent.process_stage
                    copy_.dependency_delay_min = int(parent.residence_min if delay is None else delay)
                    copy_.source_space = candidate.input_space()
                elif copy_.depends_on_stage and record["treatment_id"]:
                    parent_mode = cfg["modes"][record["treatment_id"]]
                    if parent_mode["residence_min"] is not None:
                        copy_.dependency_delay_min = int(parent_mode["residence_min"])
            if mode_key:
                override = cfg["modes"][mode_key]["residence_min"]
                if override is not None:
                    copy_.residence_min = int(override)
                dry_cap = float(transfers[candidate.transfer_id].dry_feed_capacity_tph or 0)
                if dry_cap:
                    raw_dry = 1 - record["quality"]["moisture"] / 100
                    copy_.path = replace(copy_.path, capacity_tph=min(
                        min(out.link(link).capacity_tph for link in copy_.path.links), dry_cap / raw_dry))
            expanded.append(copy_)
    out.config["_WASHING_MODEL_CATALOG"] = catalogue
    out.config["_WASHING_MODEL_INITIAL"] = seeds
    out.config["_WASHING_MODEL_POSSIBLE"] = {sid: sorted(mids) for sid, mids in possible.items()}
    out.config["_WASHING_MODEL_EXOGENOUS"] = exogenous
    return out, expanded
