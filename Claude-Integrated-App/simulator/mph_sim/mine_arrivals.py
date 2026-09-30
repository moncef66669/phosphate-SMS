"""Editable, grade-resolved external mine supply on an absolute civil-day grid.

Daily quantities are physical tonnes, not dry-solids tonnes or production
orders. They enter existing finite inlet buffers and never bypass stackers.
The initial profile is an explicit planning assumption, not measured supply.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .domain import Evidence, ExogenousFlow

CONFIG_KEY = "MINE_ARRIVALS"
FLOW_PREFIX = "MINE-ARRIVAL:"
#: Inlet-buffer opening levels as seeded BEFORE arrivals drew them down, so
#: disabling the feature can put the finite-source stock back.
_SEEDED_INLET_KEY = "_MINE_ARRIVALS_SEEDED_INLET_T"
SITE_LABELS = {"BENGUERIR": "Benguerir", "LOUTA": "Louta"}
SITE_SOURCE_SPACES = {
    "BENGUERIR": ("BG-HOP-TM1", "BG-HOP-TSM2", "BG-HOP-TSM3"),
    "LOUTA": ("LT-MINE-FEED-GLOBAL", "LT-MINE-FEED-LOW-CD"),
}
DEFAULT_PREFERENCES_PATH = Path(__file__).resolve().parents[1] / "config" / "mine_arrivals.json"
_LEGACY_TERMINAL = {
    "BG-MINE-1": 100000., "BG-MINE-2": 100000.,
    "BG-RAW-1": 100000., "BG-RAW-2": 100000.,
    "110LG-OL1": 123568.5, "110LG-OL2": 123568.5,
    "LT-P1": 95756., "LT-P2": 95756.,
}
_GRADE_ORDER = ("RAW-TBT", "RAW-BT", "RAW-BT-HIGH-CD", "RAW-BT-LOW-CD", "RAW-MT")
_OBSOLETE_GRADE_ALIASES = {"RAW-BT-NOMINAL": "RAW-BT"}
EVIDENCE = (
    "Editable planning assumption, not measured mine production: roughly "
    "24,000 physical t/day Benguerir and 12,000 t/day Louta, with daily "
    "variability, predominantly lower raw grades and about 5% RAW-MT. "
    "Benguerir matches its three washing lines at 8,000 t/day each; Louta "
    "matches its two lines. Arrivals are a mine-to-plant balance, so a spill "
    "means the receiving capacity or line demand is understated. "
    "Arrivals are spread over each civil day, enter the existing inlet "
    "buffers, and retain the configured grade assays and moisture."
)


def supported(sc) -> bool:
    ids = {space.id for space in sc.spaces}
    return all(sid in ids for values in SITE_SOURCE_SPACES.values() for sid in values)


def raw_grades(sc) -> list[str]:
    grades = {grade.id for grade in sc.grades
              if str(grade.material_class).startswith("raw")
              and not getattr(grade, "is_specification", False)}
    return [g for g in _GRADE_ORDER if g in grades] + sorted(grades - set(_GRADE_ORDER))


def _default_settings(sc) -> dict:
    start = sc.sim.bucket_dt(0)
    finish = start + timedelta(hours=float(sc.sim.dt_h) * int(sc.sim.horizon_buckets))
    days = max(7, (finish - timedelta(microseconds=1)).date().toordinal() - start.date().toordinal() + 1)
    # Benguerir's three washing lines each process 8,000 t/day, so 24,000 t/day
    # of arrivals is the matched mine-to-plant balance, not an oversupply
    # (project-team ruling 30-Sep-2026). Louta is likewise balanced: 84,000
    # t/week arriving against 83,114 t/week of washing draw.
    #
    # These figures are therefore NOT the lever for a spill problem. If arrivals
    # appear to overflow, the receiving capacity or the configured washing-line
    # demand is understated -- cutting arrivals would silently shrink the mine
    # instead of fixing it.
    # One mine hopper delivers 8,000 t/day and feeds one washing line at the
    # same rate (project-team ruling 30-Sep-2026). Benguerir has three hoppers
    # and three lines (24,000 t/day); Louta has two of each (16,000 t/day).
    # The daily rows vary around those means and each week's total matches the
    # site's washing draw.
    #
    # These figures are therefore a mine-to-plant balance, NOT the lever for a
    # spill problem: cutting arrivals to stop material piling up would silently
    # shrink the mine instead of fixing whatever is failing to draw it down.
    totals = {"BENGUERIR": (24000, 26400, 21600, 25200, 22800, 24000, 25200),
              "LOUTA": (16000, 14400, 17600, 16000, 15200, 16800, 16000)}
    # TBT, BT, high-Cd BT, low-Cd BT, MT. Every row sums to 100%.
    mixes = ((45, 25, 10, 15, 5), (48, 24, 10, 14, 4),
             (42, 27, 10, 15, 6), (46, 25, 10, 14, 5),
             (44, 26, 11, 15, 4), (47, 23, 10, 15, 5),
             (43, 26, 10, 15, 6))
    ids = ("RAW-TBT", "RAW-BT", "RAW-BT-HIGH-CD", "RAW-BT-LOW-CD", "RAW-MT")
    known = raw_grades(sc)
    rows = []
    for day in range(1, days + 1):
        for site in SITE_LABELS:
            total = totals[site][(day - 1) % 7]
            mix = mixes[(day - 1) % 7]
            if site == "LOUTA":
                mix = (mix[0] - 10, mix[1] + 10, *mix[2:])
            volumes = {grade: 0 for grade in known}
            for grade, pct in zip(ids, mix):
                if grade in volumes:
                    volumes[grade] = total * pct // 100
            rows.append(dict(day=day, site=site, volumes_t=volumes))
    return dict(version=1, enabled=True, stock_policy="OPERATING", days=rows, evidence=EVIDENCE)


def settings(sc) -> dict:
    return _merge_obsolete_grades(
        sc.config.get(CONFIG_KEY, _default_settings(sc)))


def _merge_obsolete_grades(cfg):
    """Fold deleted legacy grade columns into their canonical replacement.

    Valid numeric tonnage is conserved. Malformed values remain under the old
    key so validation reports them instead of silently discarding bad input.
    """
    migrated = deepcopy(cfg)
    if not isinstance(migrated, dict) or not isinstance(
            migrated.get("days"), list):
        return migrated
    for row in migrated["days"]:
        if not isinstance(row, dict) or not isinstance(
                row.get("volumes_t"), dict):
            continue
        volumes = row["volumes_t"]
        for obsolete, canonical in _OBSOLETE_GRADE_ALIASES.items():
            if obsolete not in volumes:
                continue
            value = volumes[obsolete]
            current = volumes.get(canonical, 0)
            valid_value = type(value) in (int, float) and math.isfinite(value)
            valid_current = (
                type(current) in (int, float) and math.isfinite(current))
            if valid_value and valid_current:
                volumes[canonical] = current + value
                del volumes[obsolete]
    return migrated


def _without_obsolete_zero_grades(sc, cfg):
    """Remove only unused columns for deleted grades; never discard supply.

    Defaults and saved profiles can predate an operator's grade catalogue.
    A zero-tonne column is not an operational reference. Unknown nonzero,
    malformed, and known-but-nonraw grades still require validation errors.
    """
    cleaned = _merge_obsolete_grades(cfg)
    if not isinstance(cleaned, dict) or not isinstance(cleaned.get("days"), list):
        return cleaned
    known = {grade.id for grade in sc.grades}
    for row in cleaned["days"]:
        if not isinstance(row, dict) or not isinstance(row.get("volumes_t"), dict):
            continue
        row["volumes_t"] = {
            grade: value for grade, value in row["volumes_t"].items()
            if not (grade not in known and type(value) in (int, float) and value == 0)
        }
    return cleaned


def reconcile_grade_catalog(sc) -> bool:
    """Repair retained sessions and conserve deleted-grade supply as RAW-BT."""
    if CONFIG_KEY not in sc.config:
        return False
    current = sc.config[CONFIG_KEY]
    cleaned = _without_obsolete_zero_grades(sc, current)
    if cleaned == current:
        return False
    sc.config[CONFIG_KEY] = cleaned
    return True


def _target(site, grade):
    if site == "LOUTA":
        return "LT-MINE-FEED-LOW-CD" if grade in ("RAW-BT-LOW-CD", "RAW-MT") else "LT-MINE-FEED-GLOBAL"
    if grade in ("RAW-BT-LOW-CD", "RAW-MT"):
        return "BG-HOP-TSM3"
    return "BG-HOP-TM1" if grade == "RAW-TBT" else "BG-HOP-TSM2"


def _validate(sc, cfg, horizon_offset=0):
    if not supported(sc):
        raise ValueError("Mine arrivals require the Benguerir and Louta inlet buffers.")
    if not isinstance(cfg, dict) or type(cfg.get("version")) is not int or cfg["version"] != 1:
        raise ValueError("Unsupported mine-arrival configuration version.")
    if type(cfg.get("enabled")) is not bool:
        raise ValueError("Mine-arrival enabled must be a boolean.")
    if cfg.get("stock_policy") not in ("OPERATING", "EXACT_DAILY"):
        raise ValueError("Choose OPERATING or EXACT_DAILY stock policy.")
    from . import washing
    if cfg["enabled"] and not washing.enabled(sc):
        raise ValueError("Enable washing's grade-resolved material model, or disable mine arrivals; mixed raw grades cannot use the legacy single-grade ledger.")
    if cfg["enabled"] and sc.sim.mixed_resolution_enabled:
        raise ValueError("Mine arrivals currently require the native uniform/weekly planning grid.")
    if not isinstance(cfg.get("days"), list) or not cfg["days"]:
        raise ValueError("Mine arrivals need a daily profile for both sites.")
    known = set(raw_grades(sc))
    seen = set()
    for row in cfg["days"]:
        if not isinstance(row, dict) or type(row.get("day")) is not int or row["day"] < 1:
            raise ValueError("Mine-arrival days must be positive integer civil-day numbers.")
        site = row.get("site")
        key = (row["day"], site)
        if site not in SITE_LABELS or key in seen:
            raise ValueError("Unknown mine site or duplicate site/day row.")
        seen.add(key)
        volumes = row.get("volumes_t")
        if not isinstance(volumes, dict):
            raise ValueError(f"{site} day {row['day']}: mine-arrival volumes must be a grade-to-tonnes table.")
        invalid = sorted(set(volumes) - known)
        if invalid:
            details = ", ".join(f"{grade} ({volumes[grade]!r} t)" for grade in invalid)
            raise ValueError(f"{site} day {row['day']}: mine arrivals must use known raw grades, "
                             f"not washed or receiving grades. Invalid entries: {details}.")
        by_target = {}
        for grade, value in volumes.items():
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0 or value != round(value):
                raise ValueError(f"{site} day {row['day']} {grade}: enter finite, non-negative whole physical tonnes.")
            target = _target(site, grade)
            by_target[target] = by_target.get(target, 0) + value
        # Existing upstream stackers are 2,100 physical t/h. Louta shares one;
        # BEN has separate hopper/stacker paths. This does not guarantee washing
        # feasibility, and neither creates capacity nor suppresses outages.
        if any(value > 2100 * 24 for value in by_target.values()) or (site == "LOUTA" and sum(by_target.values()) > 2100 * 24):
            raise ValueError(f"{site} day {row['day']}: supply exceeds the existing upstream 2,100 t/h stacking envelope.")
    start = sc.sim.bucket_dt(0)
    finish = start + timedelta(hours=(horizon_offset + int(sc.sim.horizon_buckets)) * float(sc.sim.dt_h))
    last_day = ((finish - timedelta(microseconds=1)).date() - start.date()).days + 1
    first_day = (sc.sim.bucket_dt(horizon_offset).date() - start.date()).days + 1
    if cfg["enabled"] and any((day, site) not in seen for day in range(first_day, last_day + 1) for site in SITE_LABELS):
        raise ValueError("Mine-arrival profile does not cover the planning horizon; add daily rows or shorten the horizon.")


def configuration_errors(sc) -> list[str]:
    if CONFIG_KEY not in sc.config:
        return []
    try:
        _validate(sc, sc.config[CONFIG_KEY])
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return [str(exc)]
    return []


def _stock_policy(sc, cfg):
    targets = dict(sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}))
    # Only legacy demonstration defaults are managed. Operator targets with
    # other values, minima, opening inventories and zero-WIP closure survive.
    for sid, baseline in _LEGACY_TERMINAL.items():
        if cfg["stock_policy"] == "OPERATING":
            if targets.get(sid) == baseline:
                targets.pop(sid)
        elif sid not in targets:
            targets[sid] = baseline
    sc.config["TERMINAL_INVENTORY_EXACT_T"] = targets


def _make_room_for_arrivals(sc, cfg) -> None:
    """Leave the inlet buffers room to receive the first day of arrivals.

    The inlet buffers are seeded at 100% of capacity, which is correct while
    arrivals are DISABLED: the buffer is then the whole finite source for the
    week. Enabling arrivals inverts that -- the week's material now comes from
    the mine, and the evidence note for this feature says arrivals "enter the
    existing inlet buffers". A buffer that is already full cannot receive
    anything, so every arriving tonne spills.

    Spill and unstacked material feed the EMERGENCY objective term, which is
    the highest lexicographic level and outranks customer service outright.
    Measured 30-Sep-2026 on FULL_FLOW: 36,000 t/day of arrivals into 0 t of
    headroom produced SPACE_OVERFLOW 36,000 t and drove weekly service to
    0.00% with the plan reporting FEASIBLE -- the solver was spending the
    whole budget on spill it could not avoid.

    Only the opening mass of this feature's OWN target spaces is touched. The
    level seeded before the first reduction is remembered, so switching
    arrivals back off restores the finite-source buffer instead of leaving it
    drained -- without that, toggling the feature silently destroyed opening
    stock the disabled configuration depends on.
    """
    saved = dict(sc.config.get(_SEEDED_INLET_KEY, {}))
    if not cfg.get("enabled"):
        if saved:
            for space in sc.spaces:
                if space.id in saved:
                    space.mass_t = float(saved[space.id])
            sc.config.pop(_SEEDED_INLET_KEY, None)
        return
    first_day = {}
    for row in cfg["days"]:
        if int(row["day"]) != 1:
            continue
        for grade, total in row["volumes_t"].items():
            if total <= 0:
                continue
            target = _target(row["site"], grade)
            first_day[target] = first_day.get(target, 0.0) + float(total)
    for space in sc.spaces:
        needed = first_day.get(space.id)
        if not needed:
            continue
        room = float(space.capacity_t) - float(space.mass_t)
        if room >= needed:
            continue
        saved.setdefault(space.id, float(space.mass_t))
        # Idempotent: re-running refresh computes the same ceiling.
        space.mass_t = max(float(space.capacity_t) - needed, 0.0)
    if saved:
        sc.config[_SEEDED_INLET_KEY] = saved


def refresh(sc, horizon_offset=0) -> None:
    """Rebuild only owned inflows after time-grid edits.

    Opening stock is left alone except in the inlet buffers this feature
    itself fills -- see ``_make_room_for_arrivals``.

    Each civil-day/grade amount uses cumulative whole-tonne rounding. Full
    days conserve the configured mass exactly, at hourly or subhourly grids.
    Absolute bucket indices prevent arrivals restarting at rolling boundaries.
    """
    if CONFIG_KEY not in sc.config:
        return
    cfg = _without_obsolete_zero_grades(sc, sc.config[CONFIG_KEY])
    _validate(sc, cfg, horizon_offset)
    flows = [flow for flow in sc.exogenous if not flow.id.startswith(FLOW_PREFIX)]
    if cfg["enabled"]:
        start = sc.sim.bucket_dt(0)
        midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
        dt = float(sc.sim.dt_h)
        for row in cfg["days"]:
            day_start = midnight + timedelta(days=row["day"] - 1)
            day_end = day_start + timedelta(days=1)
            previous = {}
            for bucket in range(horizon_offset, horizon_offset + int(sc.sim.horizon_buckets)):
                begin = max(day_start, start + timedelta(hours=bucket * dt))
                end = min(day_end, start + timedelta(hours=(bucket + 1) * dt))
                if end <= begin:
                    continue
                a = (begin - day_start).total_seconds() / 86400
                b = (end - day_start).total_seconds() / 86400
                for grade, total in row["volumes_t"].items():
                    mass = round(total * b) - round(total * a)
                    if mass <= 0:
                        continue
                    prior = previous.get(grade)
                    if prior is not None and prior.end_bucket == bucket - 1 and prior.rate_tph == mass / dt:
                        prior.end_bucket = bucket
                        continue
                    flows.append(ExogenousFlow(
                        f"{FLOW_PREFIX}{row['site']}:{row['day']}:{grade}:{bucket}",
                        _target(row["site"], grade), mass / dt, grade,
                        start_bucket=bucket, end_bucket=bucket,
                        evidence=Evidence(status="ASSUMED", note=str(cfg.get("evidence", EVIDENCE)))))
                    previous[grade] = flows[-1]
    sc.config[CONFIG_KEY] = cfg
    _stock_policy(sc, cfg)
    _make_room_for_arrivals(sc, cfg)
    sc.exogenous = flows


def staged_configuration(sc, cfg):
    staged = deepcopy(sc)
    staged.config[CONFIG_KEY] = deepcopy(cfg)
    refresh(staged)
    return staged


def _adopt_inlet_levels(sc, staged) -> bool:
    """Carry the staged inlet-buffer opening levels onto the live scenario.

    ``staged_configuration`` works on a deepcopy, so the headroom
    ``_make_room_for_arrivals`` creates lives only on that copy. Copying back
    just config and exogenous silently dropped it, which left the buffers full
    and every arriving tonne spilling.
    """
    staged_mass = {space.id: space.mass_t for space in staged.spaces}
    changed = False
    for space in sc.spaces:
        new = staged_mass.get(space.id)
        if new is not None and new != space.mass_t:
            space.mass_t = new
            changed = True
    return changed


def install_defaults(sc) -> bool:
    if not supported(sc) or CONFIG_KEY in sc.config:
        return False
    staged = staged_configuration(sc, _default_settings(sc))
    sc.config = staged.config
    sc.exogenous = staged.exogenous
    _adopt_inlet_levels(sc, staged)
    return True


def _path(path):
    return Path(path) if path is not None else Path(os.environ.get("MPH_MINE_ARRIVALS_PATH", DEFAULT_PREFERENCES_PATH))


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "scenarios": {}}
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError(f"Cannot read mine-arrival preferences {path}: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("scenarios"), dict):
        raise ValueError("Invalid mine-arrival preferences file; existing data was not replaced.")
    return data


def save_preferences(sc, path=None) -> Path:
    cfg = settings(sc)
    _validate(sc, cfg)
    target = _path(path)
    payload = _read(target)
    payload["scenarios"][sc.name] = cfg
    temporary = None
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"Cannot save mine-arrival preferences: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def apply_saved_preferences(sc, path=None) -> bool:
    cfg = _read(_path(path))["scenarios"].get(sc.name)
    if cfg is None:
        return False
    staged = staged_configuration(sc, cfg)
    changed = staged.config != sc.config or staged.exogenous != sc.exogenous
    sc.config = staged.config
    sc.exogenous = staged.exogenous
    return _adopt_inlet_levels(sc, staged) or changed
