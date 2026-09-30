"""Claude Design stockyard schematic bound to production simulator state.

The HTML/CSS/SVG drawing lives in ``claude_stockyard_template.html`` and is
the user-supplied Claude Design handoff.  This module performs the narrow data
translation needed to populate that visual layout from the canonical
``redesign_adapter`` snapshot.

The handoff contains visual occurrences for shared machines.  Those
occurrences keep their handoff slot IDs internally, but every row carries the
single canonical production identifier.  No visual occurrence is added to the
planner, scheduler, machine-coverage data or KPI calculations.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


_TEMPLATE_PATH = Path(__file__).with_name(
    "claude_stockyard_template.html")
_DATA_TOKEN = "__PRODUCTION_DATA__"
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PLANNING_COMPONENT = None

_PLANNING_COMPONENT_HTML = """
<iframe class="stockyard-planning-frame" title="Global stockyard planning diagram"></iframe>
"""

_PLANNING_COMPONENT_CSS = """
.stockyard-planning-frame {
  display: block;
  width: 100%;
  min-height: 640px;
  border: 0;
  background: #f4f5f6;
}
"""

_PLANNING_COMPONENT_JS = r"""
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  const frame = parentElement.querySelector('.stockyard-planning-frame');
  let disposed = false;

  // A percentage-height nested iframe resolves against an auto-height shadow
  // root in Streamlit v2 and can collapse to roughly its toolbar height. Use
  // the exact height reserved by Python before the inner drawing calculates
  // its Fit view.
  const viewerHeight = Math.max(640, Number(data.viewerHeight || 760));
  frame.style.height = viewerHeight + 'px';
  frame.style.minHeight = viewerHeight + 'px';

  const bind = () => {
    if (disposed || !frame.contentDocument) return;
    const document = frame.contentDocument;
    const stage = document.getElementById('stage');
    if (!stage) return;
    frame.contentWindow.requestAnimationFrame(() => {
      frame.contentWindow.dispatchEvent(new Event('resize'));
    });
    stage.addEventListener('click', (event) => {
      if (event.defaultPrevented) return;
      const node = event.target && event.target.closest
        ? event.target.closest('[data-asset-id]') : null;
      if (!node) return;
      const kind = node.getAttribute('data-asset-kind');
      if (kind === 'site') return;
      setTriggerValue('selected', {
        kind: kind,
        id: node.getAttribute('data-asset-id')
      });
    });
  };

  frame.addEventListener('load', bind, { once: true });
  frame.srcdoc = data.viewerHtml;
  return () => {
    disposed = true;
    frame.removeEventListener('load', bind);
  };
}
"""

_GRADE_LABELS = {
    "RAW-BT": "BT",
    "RAW-BT-HIGH-CD": "HIGH-CD",
    "RAW-BT-LOW-CD": "LOW-CD",
    "RAW-TBC": "RAW-PENDING",
}

_PILE_ALIASES = {
    "102MG-SP-01": "102MG-SP-01",
    "102MG-SP-02": "102MG-SP-02",
    "110LG-OL1": "110LG-OL1",
    "110LG-OL2": "110LG-OL2",
    "LT-P1": "LT-P1",
    "LT-P2": "LT-P2",
    "BG-MINE-P1": "BG-MINE-1",
    "BG-MINE-P2": "BG-MINE-2",
    "BG-RAW-P1": "BG-RAW-1",
    "BG-RAW-P2": "BG-RAW-2",
    "BG-P1": "BG-P1",
    "BG-P2": "BG-P2",
    "HP-01": "HP-01",
    "HP-05": "HP-05",
    "HP-02": "HP-02",
    "HP-06": "HP-06",
    "HP-03": "HP-03",
    "HP-07": "HP-07",
    "HP-04": "HP-04",
    "HP-08": "HP-08",
}

# visual occurrence: (canonical machine, visual rail, canonical rail,
#                     visual type, primary occurrence)
_MACHINE_ALIASES = {
    "102MG-ST-01": (
        "102MG-ST-01", "RL-L-MS-1", "102MG-ST1-R", "Stacker", True),
    "110MG-RC-01": (
        "110MG-RC-01", "RC-L-MS-1", "110MG-R", "Reclaimer", True),
    "102MG-ST-02": (
        "102MG-ST-02", "RL-L-MS-2", "102MG-ST2-R", "Stacker", True),
    "110MG-RC-02": (
        "110MG-RC-01", "RC-L-MS-2", "110MG-R", "Reclaimer", False),
    "110LG-ST-01": (
        "110LG-ST-01", "RL-L-RAW-1", "110LG-R", "Stacker", True),
    "120LG-RC-01": (
        "120LG-RC-01", "RC-L-RAW-1", "120LG-R1", "Reclaimer", True),
    "110LG-ST-02": (
        "110LG-ST-01", "RL-L-RAW-2", "110LG-R", "Stacker", False),
    "120LG-RC-02": (
        "120LG-RC-02", "RC-L-RAW-2", "120LG-R2", "Reclaimer", True),
    "130LG-ST-01": (
        "130LG-ST-01", "RL-L-WP-1", "130LG-R", "Stacker", True),
    "140LG-RC-01": (
        "140LG-RC-01", "RC-L-WP-1", "140LG-RL1", "Reclaimer", True),
    "130LG-ST-02": (
        "130LG-ST-01", "RL-L-WP-2", "130LG-R", "Stacker", False),
    "140LG-RC-02": (
        "140LG-RC-02", "RC-L-WP-2", "140LG-RL2", "Reclaimer", True),
    "BG-MINE-ST-1": (
        "BG-MINE-ST-1", "RL-B-MS-1", "BG-MINE-ST1-R", "Stacker", True),
    "BG-MINE-RC-1": (
        "BG-MINE-BWR", "RC-B-MS-1", "BG-MINE-R", "Reclaimer", True),
    "BG-MINE-ST-2": (
        "BG-MINE-ST-2", "RL-B-MS-2", "BG-MINE-ST2-R", "Stacker", True),
    "BG-MINE-RC-2": (
        "BG-MINE-BWR", "RC-B-MS-2", "BG-MINE-R", "Reclaimer", False),
    "BG-RAW-ST-1": (
        "BG-RAW-ST-1", "RL-B-RAW-1", "BG-RAW-ST1-R", "Stacker", True),
    "BG-RAW-RC-1": (
        "BG-RAW-BWR", "RC-B-RAW-1", "BG-RAW-R", "Reclaimer", True),
    "BG-RAW-ST-2": (
        "BG-RAW-ST-2", "RL-B-RAW-2", "BG-RAW-ST2-R", "Stacker", True),
    "BG-RAW-RC-2": (
        "BG-RAW-BWR", "RC-B-RAW-2", "BG-RAW-R", "Reclaimer", False),
    "BG-ST-3": ("ST3", "RL-B-WP-1", "BG-SL", "Stacker", True),
    "BG-RC-2": ("RC2", "RC-B-WP-1", "BG-RL-P1", "Reclaimer", True),
    "BG-ST-4": ("ST3", "RL-B-WP-2", "BG-SL", "Stacker", False),
    "BG-RC-3": ("RC3", "RC-B-WP-2", "BG-RL-P2", "Reclaimer", True),
    "M16-ST-A": (
        "ST01-A", "RL-M-B1", "M16A-A-T06", "Stacker", True),
    "M16-RC-A": (
        "RC01-A", "RC-M-B1", "M16A-A-T11", "Reclaimer", True),
    "M16-ST-B": (
        "ST01-B", "RL-M-B2", "M16A-B-T06", "Stacker", True),
    "M16-RC-B": (
        "RC01-A", "RC-M-B2", "M16A-A-T11", "Reclaimer", False),
    "M16-ST-C": (
        "ST01-B", "RL-M-B3", "M16A-B-T06", "Stacker", False),
    "M16-RC-C": (
        "RC01-B", "RC-M-B3", "M16A-B-T11", "Reclaimer", True),
    "M16-ST-D": (
        "ST01-C", "RL-M-B4", "M16A-C-T06", "Stacker", True),
    "M16-RC-D": (
        "RC01-B", "RC-M-B4", "M16A-B-T11", "Reclaimer", False),
}

_PROCESS_LINE_ALIASES = {
    "LINE L1": "LT-WASH-L1",
    "LINE L2": "LT-WASH-L2",
    "LINE 1": "BG-WASH-L1",
    "LINE 2": "BG-WASH-L2",
    "LINE 3": "BG-WASH-L3",
}

# visual route: (canonical route IDs, aggregation mode, from visual, to visual)
_ROUTE_SPECS = {
    "LO-HOP-1 FEED": (
        ("LT-102MG-ST1-FEED",), "single", "LO-HOP-1", "102MG-ST-01"),
    "LO-HOP-2 FEED": (
        ("LT-102MG-ST2-FEED",), "single", "LO-HOP-2", "102MG-ST-02"),
    "L-MS→RAW 1": (
        ("LT-102MG-BEN",), "single", "110MG-RC-01", "110LG-ST-02"),
    "L-MS→RAW 2": (
        ("LT-102MG-BEN",), "single", "110MG-RC-02", "110LG-ST-01"),
    "L1 FEED": (
        ("RB1/RB2",), "single", "120LG-RC-01", "LINE L1"),
    "L2 FEED": (
        ("RB3",), "single", "120LG-RC-02", "LINE L2"),
    "L1 PRODUCT": (
        ("SL11",), "single", "LINE L1", "130LG-ST-01"),
    "L2 PRODUCT": (
        ("SL11",), "single", "LINE L2", "130LG-ST-02"),
    "C1": (("C1",), "single", "140LG-RC-02", "IF-C1-M16"),
    "BG-HOP-1 FEED": (
        ("BG-HOP-TM1",), "single", "BG-HOP-1", "BG-MINE-ST-1"),
    "BG-HOP-2 FEED": (
        ("BG-HOP-TSM2",), "single", "BG-HOP-2", "BG-MINE-ST-1"),
    "BG-HOP-3 FEED": (
        ("BG-HOP-TSM3",), "single", "BG-HOP-3", "BG-MINE-ST-2"),
    "B-MS→RAW 1": (
        ("BG-MINE-SCREEN", "BG-SCREEN-RAW-1"), "series",
        "BG-MINE-RC-1", "BG-RAW-ST-1"),
    "B-MS→RAW 2": (
        ("BG-MINE-SCREEN", "BG-SCREEN-RAW-2"), "series",
        "BG-MINE-RC-2", "BG-RAW-ST-2"),
    "LINE 1 FEED": (
        ("BG-RAW-WASH",), "single", "BG-RAW-RC-1", "LINE 1"),
    "LINE 2 FEED": (
        ("BG-RAW-WASH",), "single", "BG-RAW-RC-2", "LINE 2"),
    "LINE 3 FEED": (
        ("BG-RAW-WASH",), "single", "BG-RAW-RC-2", "LINE 3"),
    "LINE 1 PRODUCT": (
        ("BG-WASH-YARD",), "single", "LINE 1", "BG-ST-3"),
    "LINE 2 PRODUCT": (
        ("BG-WASH-YARD",), "single", "LINE 2", "BG-ST-4"),
    "BG-TRAIN ROUTE": (
        ("BG-TRAIN-P",), "single", "BG-RC-2", "BG-TRAIN"),
    "C2": (("C2",), "single", "BG-RC-3", "IF-C2-M16"),
    "M16 IN-FEED": (
        ("T01/T02",), "single", "IF-C1-M16", "M16-ST-A"),
    "BAY 1 SPUR": (
        ("M16A-A-T06-feed",), "single", "M16 IN-FEED", "M16-ST-A"),
    "BAY 2 SPUR": (
        ("M16A-B-T06-feed",), "single", "M16 IN-FEED", "M16-ST-B"),
    "BAY 3 SPUR": (
        ("M16A-B-T06-feed",), "single", "M16 IN-FEED", "M16-ST-C"),
    "BAY 4 SPUR": (
        ("M16A-C-T06-feed",), "single", "M16 IN-FEED", "M16-ST-D"),
    "M16 OUT COLLECTOR": (
        ("M16A-A-T13", "M16A-B-T13"), "parallel",
        "M16-RC-B", "PAP-1 FEED"),
    "PAP-2 FEED": (
        ("H-PAP2",), "single", "M16 OUT COLLECTOR", "PAP-2"),
    "PAP-1 FEED": (
        ("H-PAP",), "single", "M16 OUT COLLECTOR", "PAP-1"),
    "DRY-FEED FEED": (
        ("H-DRY",), "single", "M16 OUT COLLECTOR", "DRY-FEED"),
}

_HOPPER_SPECS = {
    "LO-HOP-1": {
        "route": "LT-102MG-ST1-FEED",
        "canonical": "102MG feed interface 1",
        "feeds": "102MG-SP-01",
        "interface": True,
    },
    "LO-HOP-2": {
        "route": "LT-102MG-ST2-FEED",
        "canonical": "102MG feed interface 2",
        "feeds": "102MG-SP-02",
        "interface": True,
    },
    "BG-HOP-1": {
        "asset": "BG-HOP-TM1:HOPPER",
        "route": "BG-HOP-TM1",
        "canonical": "BG-HOP-TM1",
        "feeds": "BG-MINE-1",
    },
    "BG-HOP-2": {
        "asset": "BG-HOP-TSM2:HOPPER",
        "route": "BG-HOP-TSM2",
        "canonical": "BG-HOP-TSM2",
        "feeds": "BG-MINE-1",
    },
    "BG-HOP-3": {
        "asset": "BG-HOP-TSM3:HOPPER",
        "route": "BG-HOP-TSM3",
        "canonical": "BG-HOP-TSM3",
        "feeds": "BG-MINE-2",
    },
}


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _script_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), default=str)
    return (
        payload.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def _index_assets(metadata: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in metadata.get("assets", []):
        if isinstance(raw, Mapping) and raw.get("id"):
            result[str(raw["id"])] = dict(raw)
    for raw in metadata.get("destinations", []):
        if isinstance(raw, Mapping) and raw.get("id"):
            result[str(raw["id"])] = {
                **result.get(str(raw["id"]), {}),
                **dict(raw),
            }
    return result


def _grade_labels(metadata: Mapping[str, Any]) -> dict[str, str]:
    labels = dict(_GRADE_LABELS)
    for raw in metadata.get("grades", []):
        if not isinstance(raw, Mapping) or not raw.get("id"):
            continue
        grade_id = str(raw["id"])
        labels[grade_id] = str(
            raw.get("label") or _GRADE_LABELS.get(grade_id, grade_id))
    return labels


def _grade_colors(
    metadata: Mapping[str, Any],
    labels: Mapping[str, str],
) -> dict[str, str]:
    colors: dict[str, str] = {}
    for raw in metadata.get("grades", []):
        if not isinstance(raw, Mapping) or not raw.get("id"):
            continue
        grade_id = str(raw["id"])
        color = str(raw.get("color") or "").strip()
        if _HEX_COLOR.fullmatch(color):
            colors[labels.get(grade_id, grade_id)] = color
    return colors


def _normal_machine_state(value: object) -> str:
    state = str(value or "Idle")
    allowed = {
        "Running", "Stacking", "Reclaiming", "Travelling", "Idle",
        "Maintenance", "Unavailable", "Future",
    }
    if state in allowed:
        return state
    if state == "Reference only":
        return "Unavailable"
    if state.lower() in {"disabled", "planning disabled"}:
        return "Unavailable"
    return "Unavailable"


def _normal_route_state(value: object) -> str:
    state = str(value or "Idle")
    if state in {"Running", "Future", "Unavailable", "Idle"}:
        return state
    return "Unavailable"


def _normal_line_state(value: object) -> str:
    state = str(value or "Available")
    if state in {"Available", "Future", "Disabled"}:
        return state
    if state == "Unavailable":
        return "Disabled"
    return "Disabled"


def _pile_rows(
    assets: Mapping[str, Mapping[str, Any]],
    grade_labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for visual_id, canonical_id in _PILE_ALIASES.items():
        asset = dict(assets.get(canonical_id, {}))
        mass = max(0.0, _number(asset.get("massT")))
        capacity = max(1.0, _number(asset.get("capacityT"), 1.0))
        fill = max(
            0.0,
            min(100.0, _number(
                asset.get("fillPct"), 100.0 * mass / capacity)),
        )
        raw_bands = [
            dict(band) for band in asset.get("gradeBands", [])
            if isinstance(band, Mapping)
        ]
        weights = [
            max(0.0, _number(band.get("inventoryFraction")))
            for band in raw_bands
        ]
        weight_total = sum(weights)
        grades: list[dict[str, Any]] = []
        for band, weight in zip(raw_bands, weights):
            grade_id = str(band.get("gradeId") or band.get("label") or "")
            if not grade_id:
                continue
            inventory_share = (
                weight / weight_total if weight_total > 0 else 0.0)
            grade_mass = _optional_number(band.get("massT"))
            if grade_mass is None:
                grade_mass = mass * inventory_share
            grades.append({
                "gradeId": grade_id,
                "grade": grade_labels.get(
                    grade_id, _GRADE_LABELS.get(grade_id, grade_id)),
                "massT": max(0.0, grade_mass),
                "inventoryShare": inventory_share,
                "fraction": max(
                    0.0, min(1.0, fill / 100.0 * inventory_share)),
                "repereFrom": int(_number(band.get("repereFrom"))),
                "repereTo": int(_number(band.get("repereTo"))),
            })
        rows.append({
            "id": visual_id,
            "canonicalId": canonical_id,
            "label": asset.get("label") or canonical_id,
            "site": asset.get("site") or "",
            "site": asset.get("site") or "",
            "massT": mass,
            "capacityT": capacity,
            "fillPct": fill,
            "materialClass": str(asset.get("materialClass") or ""),
            "tsmEquivalentT": _optional_number(
                asset.get("tsmEquivalentT")),
            "tsmLabel": str(asset.get("tsmLabel") or ""),
            "repereFrom": int(_number(asset.get("repereFrom"))),
            "repereTo": int(_number(asset.get("repereTo"), 1000.0)),
            "grades": grades,
            "blendLocation": bool(asset.get("blendLocation", False)),
            "blendStatus": str(asset.get("blendStatus") or ""),
            "blendRecipe": str(asset.get("blendRecipe") or ""),
            "hasData": canonical_id in assets,
        })
    return rows


def _machine_rows(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for visual_id, spec in _MACHINE_ALIASES.items():
        canonical_id, visual_rail, canonical_rail, kind, primary = spec
        asset = dict(assets.get(canonical_id, {}))
        target = str(
            asset.get("targetId") or asset.get("destinationId") or "")
        rows.append({
            "id": visual_id,
            "canonicalId": canonical_id,
            "label": asset.get("label") or canonical_id,
            "isPrimary": primary,
            "type": kind,
            "railId": visual_rail,
            "canonicalRailId": canonical_rail,
            "state": (
                _normal_machine_state(asset.get("state"))
                if asset else "Unavailable"
            ),
            "canonicalState": asset.get("state") or "Idle",
            "jobId": asset.get("jobId"),
            "targetId": target or None,
            "positionRepere": int(_number(
                asset.get("positionRepere"), 500.0)),
            "rateTph": _number(asset.get("rateTph")),
            "maximumRateTph": _number(asset.get("maximumRateTph")),
            # Planning configuration is deliberately separate from the
            # instantaneous activity state above.  The Monitor viewer ignores
            # these fields; the Plan viewer uses them for persistent, explicit
            # availability/rate badges.
            "baselineState": asset.get("baselineState"),
            "baseRateTph": _optional_number(asset.get("baseRateTph")),
            "timedChangeCount": int(_number(
                asset.get("timedChangeCount"))),
            "timedChangeSummary": str(
                asset.get("timedChangeSummary") or ""),
            "configurable": bool(asset.get("configurable", False)),
            "hasData": bool(asset),
        })
    return rows


def _aggregate_route(
    route_assets: Sequence[Mapping[str, Any]],
    mode: str,
) -> tuple[str, float, float | None]:
    if not route_assets:
        return "Unavailable", 0.0, None
    states = [_normal_route_state(item.get("state")) for item in route_assets]
    flows = [_number(item.get("flowTph")) for item in route_assets]
    capacities = [
        value
        for item in route_assets
        if (value := _optional_number(item.get("capacityTph"))) is not None
    ]
    if mode == "parallel":
        state = (
            "Running" if "Running" in states else
            "Future" if states and all(s == "Future" for s in states) else
            "Unavailable" if states and all(
                s == "Unavailable" for s in states) else
            "Idle"
        )
        capacity = (
            sum(capacities)
            if len(capacities) == len(route_assets) else None
        )
        return state, sum(flows), capacity
    if mode == "series":
        state = (
            "Unavailable" if "Unavailable" in states else
            "Future" if "Future" in states else
            "Running" if states and all(s == "Running" for s in states) else
            "Idle"
        )
        return (
            state,
            min(flows) if flows else 0.0,
            (
                min(capacities)
                if len(capacities) == len(route_assets) else None
            ),
        )
    item = route_assets[0]
    return (
        _normal_route_state(item.get("state")),
        _number(item.get("flowTph")),
        _optional_number(item.get("capacityTph")),
    )


def _route_rows(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for visual_id, spec in _ROUTE_SPECS.items():
        canonical_ids, mode, from_id, to_id = spec
        route_assets = [
            assets[canonical_id] for canonical_id in canonical_ids
            if canonical_id in assets
        ]
        state, flow, capacity = _aggregate_route(route_assets, mode)
        member_link_ids = list(dict.fromkeys(
            str(link_id)
            for asset in route_assets
            for link_id in asset.get("linkIds", [])
            if link_id
        ))
        baseline_states = [
            str(asset.get("baselineState") or "")
            for asset in route_assets
            if asset.get("baselineState")
        ]
        baseline_state = (
            "MAINTENANCE" if baseline_states and all(
                item == "MAINTENANCE" for item in baseline_states) else
            "PARTIAL" if "PARTIAL" in baseline_states or (
                "AVAILABLE" in baseline_states
                and "UNAVAILABLE" in baseline_states) or
                "MAINTENANCE" in baseline_states else
            "UNAVAILABLE" if baseline_states and all(
                item == "UNAVAILABLE" for item in baseline_states) else
            "AVAILABLE" if baseline_states else None
        )
        base_rates = [
            value for asset in route_assets
            if (value := _optional_number(
                asset.get("baseRateTph"))) is not None
        ]
        base_rate = (
            sum(base_rates) if mode == "parallel" and
            len(base_rates) == len(route_assets) else
            min(base_rates) if base_rates else None
        )
        rows.append({
            "id": visual_id,
            "canonicalId": canonical_ids[0],
            "canonicalIds": list(canonical_ids),
            "aggregationMode": mode,
            "site": next((str(asset.get("site"))
                          for asset in route_assets
                          if asset.get("site")), ""),
            # Visual route IDs are not solver IDs.  This union is the only
            # safe edit target for grouped/aliased conveyors.
            "memberLinkIds": member_link_ids,
            "fromId": from_id,
            "toId": to_id,
            "state": state,
            "flowTph": flow,
            "capacityTph": capacity,
            "baselineState": baseline_state,
            "baseRateTph": base_rate,
            "timedChangeCount": sum(int(_number(
                asset.get("timedChangeCount"))) for asset in route_assets),
            "timedChangeSummary": " | ".join(dict.fromkeys(
                str(asset.get("timedChangeSummary") or "")
                for asset in route_assets
                if asset.get("timedChangeSummary")
            )),
            "configurable": bool(route_assets) and all(
                bool(asset.get("configurable", False))
                for asset in route_assets),
            "hasData": bool(route_assets),
        })
    return rows


def _process_line_rows(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for visual_id, canonical_id in _PROCESS_LINE_ALIASES.items():
        asset = dict(assets.get(canonical_id, {}))
        rows.append({
            "id": visual_id,
            "canonicalId": canonical_id,
            "label": asset.get("label") or visual_id,
            "site": asset.get("site") or "",
            "state": (
                _normal_line_state(asset.get("state"))
                if asset else "Disabled"
            ),
            "residenceMinutes": (
                _number(asset.get("residenceMinutes"))
                if asset.get("residenceMinutes") is not None else None
            ),
            "baselineState": asset.get("baselineState"),
            "baseRateTph": _optional_number(asset.get("baseRateTph")),
            "timedChangeCount": int(_number(
                asset.get("timedChangeCount"))),
            "timedChangeSummary": str(
                asset.get("timedChangeSummary") or ""),
            "configurable": bool(asset.get("configurable", False)),
            "hasData": bool(asset),
        })
    return rows


def _destination_row(
    visual_id: str,
    canonical_ids: Sequence[str],
    assets: Mapping[str, Mapping[str, Any]],
    grade_labels: Mapping[str, str],
) -> dict[str, Any]:
    members = [
        dict(assets[canonical_id]) for canonical_id in canonical_ids
        if canonical_id in assets
    ]
    lifecycle_states = [
        str(member.get("state") or member.get("lifecycleState") or "Disabled")
        for member in members
    ]
    lifecycle = (
        "Available" if "Available" in lifecycle_states else
        "Future" if "Future" in lifecycle_states else
        "Disabled"
    )
    active_members = sorted(
        members,
        key=lambda item: (
            -_number(item.get("demandRateTph")),
            0 if str(item.get("id", "")).endswith("-PROD") else 1,
        ),
    )
    grade_id = str(
        active_members[0].get("requestedGrade") or ""
        if active_members else "")
    return {
        "id": visual_id,
        "canonicalId": (
            canonical_ids[0] if len(canonical_ids) == 1 else visual_id),
        "canonicalIds": list(canonical_ids),
        "site": next((str(member.get("site")) for member in members
                      if member.get("site")), ""),
        "lifecycleState": lifecycle,
        "requestedGrade": grade_labels.get(grade_id, grade_id) or "—",
        "demandT": sum(
            _number(member.get("demandRateTph")) for member in members),
        "plannedDeliveryT": None,
        "activeDeliveryRateTph": sum(
            _number(member.get("activeDeliveryRateTph"))
            for member in members),
        "configuredDemandTph": sum(
            _number(member.get("configuredDemandRateTph"))
            for member in members),
        "baselineState": (
            "MAINTENANCE" if members and all(
                str(member.get("baselineState")) == "MAINTENANCE"
                for member in members) else
            "FUTURE" if members and all(
                str(member.get("baselineState")) == "FUTURE"
                for member in members) else
            "AVAILABLE" if members and all(
                str(member.get("baselineState")) == "AVAILABLE"
                for member in members) else
            "UNAVAILABLE" if members and all(
                str(member.get("baselineState")) in {
                    "UNAVAILABLE", "REFERENCE"}
                for member in members) else
            "PARTIAL" if members else None
        ),
        "baseRateTph": (
            min(value for member in members
                if (value := _optional_number(
                    member.get("baseRateTph"))) is not None)
            if any(_optional_number(member.get("baseRateTph")) is not None
                   for member in members) else None
        ),
        "timedChangeCount": sum(int(_number(
            member.get("timedChangeCount"))) for member in members),
        "timedChangeSummary": " | ".join(dict.fromkeys(
            str(member.get("timedChangeSummary") or "")
            for member in members
            if member.get("timedChangeSummary")
        )),
        "configurable": bool(members) and any(
            bool(member.get("configurable", False)) for member in members),
        "hasData": bool(members),
    }


def _destination_rows(
    assets: Mapping[str, Mapping[str, Any]],
    grade_labels: Mapping[str, str],
) -> list[dict[str, Any]]:
    return [
        _destination_row("PAP-2", ("PAP-2",), assets, grade_labels),
        _destination_row("PAP-1", ("PAP-1",), assets, grade_labels),
        _destination_row(
            "DRY-FEED", ("DRY-FEED",), assets, grade_labels),
        _destination_row(
            "BG-TRAIN", ("BG-TRAIN-PROD", "BG-TRAIN-RAW"),
            assets, grade_labels),
    ]


def _hopper_rows(
    assets: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for visual_id, spec in _HOPPER_SPECS.items():
        asset = dict(assets.get(str(spec.get("asset") or ""), {}))
        route = dict(assets.get(str(spec["route"]), {}))
        state = _normal_machine_state(
            asset.get("state") or route.get("state"))
        rows.append({
            "id": visual_id,
            "canonicalId": spec["canonical"],
            "label": asset.get("label") or spec["canonical"],
            "interfaceOnly": bool(spec.get("interface")),
            "state": state if asset or route else "Unavailable",
            "rateTph": _number(
                asset.get("rateTph", route.get("flowTph"))),
            "maximumRateTph": _number(
                asset.get("maximumRateTph", route.get("capacityTph"))),
            "feedsId": spec["feeds"],
            "hasData": bool(asset or route),
        })
    return rows


def handoff_snapshot(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Translate canonical adapter metadata to the Claude handoff contract."""
    source = dict(metadata or {})
    assets = _index_assets(source)
    grade_labels = _grade_labels(source)
    filters = (
        dict(source.get("filters", {}))
        if isinstance(source.get("filters"), Mapping) else {}
    )
    selected_grades = [
        grade_labels.get(str(grade_id), str(grade_id))
        for grade_id in filters.get("gradeIds", [])
    ]
    source_site = str(source.get("initialSite") or source.get("site") or "")
    return {
        "stateTime": source.get("timestamp") or "—",
        "basis": source.get("basis") or "",
        "planningMode": bool(source.get("planningMode", False)),
        "displayMode": str(source.get("displayMode") or "monitor"),
        "initialSelection": (
            dict(source.get("initialSelection", {}))
            if isinstance(source.get("initialSelection"), Mapping)
            else None
        ),
        "initialSite": (
            source_site if source_site in {"LOUTA", "BENGUERIR", "MPH"}
            else None
        ),
        "selectedGrades": selected_grades,
        "gradeColors": _grade_colors(source, grade_labels),
        "piles": _pile_rows(assets, grade_labels),
        "machines": _machine_rows(assets),
        "routes": _route_rows(assets),
        "processLines": _process_line_rows(assets),
        "destinations": _destination_rows(assets, grade_labels),
        "hoppers": _hopper_rows(assets),
    }


def html(
    *,
    metadata: Mapping[str, Any] | None,
    height: int,
    dark: bool = False,
) -> str:
    """Return the exact Claude handoff drawing populated with production data."""
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping or None")
    try:
        requested_height = max(320, int(height))
    except (TypeError, ValueError) as exc:
        raise TypeError("height must be an integer") from exc
    if dark:
        # The supplied target is a light industrial planning canvas.
        raise ValueError("the Claude handoff has no approved dark variant")

    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    if template.count(_DATA_TOKEN) != 1:
        raise ValueError("Claude layout template has an invalid data token")
    document = template.replace(
        _DATA_TOKEN, _script_json(handoff_snapshot(metadata)))
    return document.replace(
        '<div id="app">',
        f'<div id="app" data-viewer-height="{requested_height}">',
        1,
    )


def planning_component(
    *,
    metadata: Mapping[str, Any],
    height: int,
    key: str,
):
    """Mount the Monitor handoff as a bidirectional Plan component.

    The production Monitor continues to use :func:`html` unchanged.  This
    small v2 wrapper loads that exact same document in an inner frame and
    relays stable ``data-asset-*`` clicks to Python.  Keeping the bridge here
    avoids maintaining a second schematic or duplicating its interaction
    logic in Streamlit widgets.
    """
    import streamlit as st

    global _PLANNING_COMPONENT
    if _PLANNING_COMPONENT is None:
        _PLANNING_COMPONENT = st.components.v2.component(
            "stockyard_planning_flow",
            html=_PLANNING_COMPONENT_HTML,
            css=_PLANNING_COMPONENT_CSS,
            js=_PLANNING_COMPONENT_JS,
        )
    viewer_html = html(metadata=metadata, height=height, dark=False)
    mount_args = {
        "data": {"viewerHtml": viewer_html, "viewerHeight": height},
        "key": key,
        "height": height,
        "on_selected_change": lambda: None,
    }
    try:
        return _PLANNING_COMPONENT(**mount_args)
    except ValueError as exc:
        # Streamlit AppTest creates a fresh component registry while Python
        # modules can remain cached between test runs. Re-register only for
        # that stale-registry case; normal app reruns keep one definition.
        if "is not registered" not in str(exc):
            raise
        _PLANNING_COMPONENT = st.components.v2.component(
            "stockyard_planning_flow",
            html=_PLANNING_COMPONENT_HTML,
            css=_PLANNING_COMPONENT_CSS,
            js=_PLANNING_COMPONENT_JS,
        )
        return _PLANNING_COMPONENT(**mount_args)


__all__ = ["handoff_snapshot", "html", "planning_component"]
