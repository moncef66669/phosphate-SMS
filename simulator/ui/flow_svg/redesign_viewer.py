"""Claude-redesign viewer shell for the production stockyard SVG.

The production renderer remains responsible for every physical asset, route,
state and label.  This module only wraps that SVG in a self-contained HTML
document suitable for ``streamlit.components.v1.html``.

Canonical metadata
------------------
``metadata["assets"]`` is the authoritative interaction catalogue.  Each item
may contain:

``id``
    Stable canonical asset identifier.
``kind``
    ``pile``, ``machine``, ``rail``, ``route``, ``processLine``,
    ``equipment``, ``destination``, ``hopper`` or ``corridor``.
``site``
    ``LOUTA``, ``BENGUERIR``, ``MPH`` or ``GLOBAL``.
``label``
    Operator-facing label.
``state``
    Current operating/lifecycle state.
``grades``
    List of represented grade identifiers.
``details``
    Mapping or list of ``[label, value]`` pairs.
``connections``
    Optional canonical IDs used for client-side connected highlighting.

Top-level ``timestamp``, ``basis``, ``view`` and ``grades`` populate the
read-only toolbar.  Extra or missing keys are deliberately tolerated.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any


_VIEWBOX = re.compile(
    r"""viewBox\s*=\s*["']([-\d.]+)\s+([-\d.]+)\s+"""
    r"""([-\d.]+)\s+([-\d.]+)["']""",
    re.IGNORECASE,
)
_SVG_OPEN = re.compile(r"<svg\b([^>]*)>", re.IGNORECASE)
_ROOT_DIMENSION = re.compile(
    r"""\s(?:id|width|height)\s*=\s*(?:"[^"]*"|'[^']*')""",
    re.IGNORECASE,
)


def _viewbox_of(svg: str) -> dict[str, float]:
    match = _VIEWBOX.search(svg)
    if not match:
        raise ValueError("production SVG has no viewBox")
    x, y, width, height = (float(value) for value in match.groups())
    if width <= 0 or height <= 0:
        raise ValueError("production SVG viewBox must have positive dimensions")
    return {"x": x, "y": y, "w": width, "h": height}


def _fill_svg(svg: str) -> str:
    """Give the trusted production SVG one predictable viewer root."""

    def replace(match: re.Match[str]) -> str:
        attrs = _ROOT_DIMENSION.sub("", match.group(1))
        return (
            '<svg id="sheet" width="100%" height="100%"'
            f"{attrs}>"
        )

    rendered, count = _SVG_OPEN.subn(replace, svg, count=1)
    if count != 1:
        raise ValueError("production SVG has no root <svg> element")
    return rendered


def _script_json(value: Any) -> str:
    """Serialize JSON safely inside a ``script[type=application/json]`` node."""

    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    # Escaping ``<`` is the important ``</script>`` defence.  The remaining
    # replacements also keep HTML parsers and JavaScript line parsing boring.
    return (
        payload.replace("&", r"\u0026")
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def html(
    svg: str,
    *,
    metadata: Mapping[str, Any] | None,
    height: int,
    dark: bool = False,
) -> str:
    """Return the self-contained redesign viewer HTML document.

    ``timestamp``, ``basis`` and current ``view``/site are read from
    ``metadata``.  The PAP-2 lifecycle is rendered as read-only status; this
    wrapper intentionally has no mock timeline, backend write or lifecycle
    mutation entry point.
    """

    if not isinstance(svg, str):
        raise TypeError("svg must be a string")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise TypeError("metadata must be a mapping or None")
    try:
        requested_height = max(320, int(height))
    except (TypeError, ValueError) as exc:
        raise TypeError("height must be an integer") from exc

    prepared_svg = _fill_svg(svg)
    base_json = _script_json(_viewbox_of(svg))
    metadata_json = _script_json(dict(metadata or {}))
    theme_class = "theme-dark" if dark else "theme-light"

    return (
        _HTML_TEMPLATE
        .replace("__THEME_CLASS__", theme_class)
        .replace("__REQUESTED_HEIGHT__", str(requested_height))
        .replace("__BASE_JSON__", base_json)
        .replace("__METADATA_JSON__", metadata_json)
        .replace("__PRODUCTION_SVG__", prepared_svg)
    )


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Global Stockyard Layout &amp; Flow</title>
<style>
  :root {
    --ink: #2f353b;
    --navy: #1f3a5f;
    --muted: #7f8991;
    --faint: #a4acb2;
    --line: #d7dbde;
    --line-soft: #e7eaec;
    --paper: #ffffff;
    --canvas: #f4f5f6;
    --canvas-dot: #dfe3e6;
    --panel: #ffffff;
    --panel-raised: #ffffff;
    --selected: #1f3a5f;
    --connected: #3f7fa6;
    --shadow: rgba(31,40,48,.15);
    --sans: ui-sans-serif, "Helvetica Neue", Helvetica, Arial, sans-serif;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas,
            "Liberation Mono", monospace;
  }
  body.theme-dark {
    --ink: #e1e7eb;
    --navy: #8eb6da;
    --muted: #a5b0b8;
    --faint: #78848d;
    --line: #3b4852;
    --line-soft: #2b3740;
    --paper: #182128;
    --canvas: #131a20;
    --canvas-dot: #2a353e;
    --panel: #1b242b;
    --panel-raised: #222d35;
    --selected: #8eb6da;
    --connected: #65a5d5;
    --shadow: rgba(0,0,0,.38);
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: var(--canvas);
    color: var(--ink);
    font-family: var(--sans);
  }
  button, select { font: inherit; }
  button:focus-visible, select:focus-visible {
    outline: 2px solid var(--connected);
    outline-offset: 2px;
  }
  #app {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: min(100vh, __REQUESTED_HEIGHT__px);
    min-height: 320px;
    overflow: hidden;
  }

  /* Claude handoff toolbar, condensed for a Streamlit content column. */
  #toolbar {
    position: relative;
    z-index: 30;
    flex: 0 0 auto;
    min-height: 52px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 14px;
    padding: 7px 14px;
    background: var(--paper);
    border-bottom: 1px solid var(--line);
  }
  .tb-group {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }
  .tb-group + .tb-group {
    border-left: 1px solid var(--line-soft);
    padding-left: 14px;
  }
  .tb-brand { gap: 8px; }
  .brand {
    color: var(--navy);
    font-size: 13px;
    font-weight: 750;
    letter-spacing: .09em;
    text-transform: uppercase;
  }
  .brand-sub, .tb-cap {
    color: var(--muted);
    font-size: 9px;
    letter-spacing: .13em;
    text-transform: uppercase;
  }
  .brand-sub { font-size: 10px; }
  #timeLabel, #basisLabel, #zoomLabel, .lifecycle {
    font-family: var(--mono);
    font-size: 10.5px;
  }
  #basisLabel {
    max-width: 130px;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--muted);
  }
  #siteTabs, #gradeChips {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 4px;
  }
  #gradeChips {
    max-width: min(510px, 31vw);
    overflow-x: auto;
    scrollbar-width: thin;
    padding: 2px 0;
  }
  .btn, .tab, .chip, select {
    min-height: 26px;
    border: 1px solid var(--line);
    border-radius: 3px;
    color: var(--ink);
    background: var(--paper);
  }
  .btn, .tab {
    padding: 5px 9px;
    cursor: pointer;
    font-size: 10px;
    font-weight: 650;
    letter-spacing: .055em;
    text-transform: uppercase;
  }
  .tab[aria-pressed="true"], .btn.on {
    color: var(--navy);
    border-color: var(--navy);
    background: color-mix(in srgb, var(--navy) 9%, var(--paper));
  }
  .btn.primary {
    color: #fff;
    background: var(--navy);
    border-color: var(--navy);
  }
  .btn.icon {
    min-width: 27px;
    padding: 4px 7px;
    font-family: var(--mono);
    font-size: 13px;
    line-height: 1;
  }
  .chip {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 7px 4px 5px;
    cursor: pointer;
    font-family: var(--mono);
    font-size: 9px;
  }
  .chip[aria-pressed="false"] {
    color: var(--faint);
    opacity: .62;
    background: transparent;
  }
  .swatch {
    width: 9px;
    height: 9px;
    flex: 0 0 auto;
    border: 1px solid rgba(0,0,0,.16);
    border-radius: 1px;
  }
  select {
    max-width: 146px;
    padding: 4px 24px 4px 7px;
    font-size: 10.5px;
    cursor: pointer;
  }
  .lifecycle {
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    padding: 3px 8px;
    color: var(--navy);
    border: 1px solid var(--line);
    border-radius: 3px;
    background: var(--canvas);
    letter-spacing: .035em;
    text-transform: uppercase;
  }
  .spacer {
    margin-left: auto;
    border-left: 0 !important;
    padding-left: 0 !important;
  }

  #stage {
    position: relative;
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
    cursor: grab;
    touch-action: none;
    background-color: var(--canvas);
    background-image: radial-gradient(var(--canvas-dot) 1px, transparent 1px);
    background-size: 28px 28px;
  }
  #stage.dragging { cursor: grabbing; }
  #sheet {
    position: absolute;
    inset: 0;
    display: block;
    background: transparent !important;
  }
  #sheet text { user-select: none; }
  #sheet .hover-target { cursor: pointer; }
  #sheet .asset {
    transition: opacity 110ms ease, filter 110ms ease;
  }
  #sheet .asset.is-filtered {
    opacity: .09 !important;
    pointer-events: none;
  }
  #sheet .asset.is-selection-dim {
    opacity: .23 !important;
  }
  #sheet .asset.is-connected {
    opacity: 1 !important;
    filter: drop-shadow(0 0 1.4px var(--connected));
  }
  #sheet .asset.is-selected {
    opacity: 1 !important;
    filter: drop-shadow(0 0 2.6px var(--selected));
  }
  #sheet .asset.keyboard-focus {
    filter: drop-shadow(0 0 2.8px var(--connected));
  }
  #sheet .lod2, #sheet .lod3 { display: none; }
  #stage.detail-2 #sheet .lod2 { display: inline; }
  #stage.detail-3 #sheet .lod2,
  #stage.detail-3 #sheet .lod3 { display: inline; }

  #hoverCard {
    position: absolute;
    z-index: 20;
    display: none;
    pointer-events: none;
    min-width: 190px;
    max-width: 300px;
    padding: 9px 11px 10px;
    color: var(--ink);
    background: var(--panel-raised);
    border: 1px solid var(--line);
    border-left: 4px solid var(--navy);
    border-radius: 4px;
    box-shadow: 0 8px 22px var(--shadow);
  }
  #hoverCard.show { display: block; }
  .card-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 6px;
  }
  .card-id {
    color: var(--navy);
    font-family: var(--mono);
    font-size: 12px;
    font-weight: 650;
  }
  .card-kind, .panel-kind {
    color: var(--muted);
    font-size: 9px;
    letter-spacing: .12em;
    text-transform: uppercase;
  }
  .kv {
    display: grid;
    grid-template-columns: minmax(70px, auto) minmax(80px, 1fr);
    gap: 3px 12px;
    align-items: baseline;
  }
  .kv .key {
    min-width: 0;
    color: var(--muted);
    font-size: 10.5px;
  }
  .kv .value {
    min-width: 0;
    overflow-wrap: anywhere;
    font-family: var(--mono);
    font-size: 10.5px;
    text-align: right;
  }

  #details {
    position: absolute;
    z-index: 25;
    top: 14px;
    right: 14px;
    bottom: 14px;
    width: min(306px, calc(100% - 28px));
    display: none;
    flex-direction: column;
    color: var(--ink);
    background: var(--panel-raised);
    border: 1px solid var(--line);
    border-radius: 5px;
    box-shadow: 0 10px 30px var(--shadow);
  }
  #details.open { display: flex; }
  .panel-head {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 13px 15px 11px;
    border-bottom: 1px solid var(--line-soft);
  }
  .panel-id {
    margin-top: 4px;
    color: var(--navy);
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 650;
  }
  #detailsClose {
    width: 26px;
    min-width: 26px;
    min-height: 26px;
    padding: 0;
    color: var(--muted);
    background: var(--paper);
    border: 1px solid var(--line);
    border-radius: 3px;
    cursor: pointer;
    font-size: 15px;
  }
  .panel-state {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 10px 15px;
    border-bottom: 1px solid var(--line-soft);
  }
  .state-lamp {
    width: 11px;
    height: 11px;
    flex: 0 0 auto;
    border: 1px solid rgba(255,255,255,.7);
    border-radius: 50%;
  }
  .panel-state strong {
    font-size: 11px;
    letter-spacing: .06em;
    text-transform: uppercase;
  }
  .panel-state span:last-child {
    margin-left: auto;
    color: var(--muted);
    font-family: var(--mono);
    font-size: 9.5px;
  }
  .panel-body {
    flex: 1 1 auto;
    overflow: auto;
    padding: 5px 15px 14px;
  }
  .panel-body .kv { gap: 0 14px; }
  .panel-body .key, .panel-body .value {
    padding: 7px 0;
    border-bottom: 1px solid var(--line-soft);
    font-size: 11px;
  }
  .panel-sub {
    margin: 15px 0 8px;
    color: var(--muted);
    font-size: 9px;
    letter-spacing: .13em;
    text-transform: uppercase;
  }
  #connectedTags {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  #connectedTags span {
    padding: 3px 7px;
    color: var(--ink);
    background: var(--canvas);
    border: 1px solid var(--line);
    border-radius: 3px;
    font-family: var(--mono);
    font-size: 9.5px;
  }

  #legend {
    position: absolute;
    z-index: 23;
    left: 14px;
    bottom: 14px;
    width: min(520px, calc(100% - 28px));
    display: none;
    padding: 13px 15px 14px;
    color: var(--ink);
    background: var(--panel-raised);
    border: 1px solid var(--line);
    border-radius: 5px;
    box-shadow: 0 10px 26px var(--shadow);
  }
  #legend.open { display: block; }
  .legend-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 11px;
  }
  .legend-head strong {
    color: var(--navy);
    font-size: 10px;
    letter-spacing: .14em;
    text-transform: uppercase;
  }
  .legend-head span {
    color: var(--muted);
    font-size: 9.5px;
  }
  .legend-cols {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px 22px;
  }
  .legend-cap {
    margin-bottom: 7px;
    color: var(--muted);
    font-size: 9px;
    letter-spacing: .13em;
    text-transform: uppercase;
  }
  .legend-items {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 10px;
  }
  .legend-item {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    font-size: 10px;
  }
  .legend-item code {
    overflow: hidden;
    text-overflow: ellipsis;
    font-family: var(--mono);
    font-size: 9.5px;
  }
  .legend-lamp {
    width: 14px;
    height: 14px;
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    border-radius: 50%;
    font-family: var(--mono);
    font-size: 7.5px;
  }
  .notation {
    grid-column: 1 / -1;
    padding-top: 10px;
    color: var(--muted);
    border-top: 1px solid var(--line-soft);
    font-size: 9.5px;
    line-height: 1.45;
  }

  .hint {
    position: absolute;
    z-index: 7;
    right: 12px;
    bottom: 12px;
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 5px 9px;
    color: var(--faint);
    background: color-mix(in srgb, var(--paper) 92%, transparent);
    border: 1px solid var(--line);
    border-radius: 4px;
    font-size: 9px;
    pointer-events: none;
  }
  .hint strong {
    color: var(--muted);
    font-family: var(--mono);
    font-weight: 450;
    letter-spacing: .035em;
  }

  @media (max-width: 1060px) {
    #toolbar { gap: 6px 10px; padding-inline: 10px; }
    .tb-group + .tb-group { padding-left: 10px; }
    .brand-sub { display: none; }
    #gradeChips { max-width: 44vw; }
    .hint span { display: none; }
  }
  @media (max-width: 720px) {
    .tb-brand, #timeGroup, #pap2Group { display: none; }
    #gradeChips { max-width: 54vw; }
    .legend-cols { grid-template-columns: 1fr; }
    .notation { grid-column: 1; }
  }
  @media (prefers-reduced-motion: reduce) {
    #sheet .asset { transition: none; }
  }
</style>
</head>
<body class="__THEME_CLASS__">
<div id="app">
  <div id="toolbar" role="toolbar" aria-label="Stockyard drawing tools">
    <div class="tb-group tb-brand">
      <span class="brand">Global Stockyard</span>
      <span class="brand-sub">Layout &amp; Flow</span>
    </div>
    <div class="tb-group">
      <span class="tb-cap">Site</span>
      <div id="siteTabs" aria-label="Site filter"></div>
    </div>
    <div class="tb-group" id="timeGroup">
      <span class="tb-cap">State time</span>
      <span id="timeLabel">—</span>
      <span id="basisLabel"></span>
    </div>
    <div class="tb-group">
      <span class="tb-cap">Grade</span>
      <div id="gradeChips" aria-label="Grade filters"></div>
    </div>
    <div class="tb-group">
      <label class="tb-cap" for="stateFilter">Equipment</label>
      <select id="stateFilter" aria-label="Equipment state filter"></select>
    </div>
    <div class="tb-group" id="pap2Group">
      <span class="tb-cap">PAP-2</span>
      <span id="pap2Lifecycle" class="lifecycle" aria-live="off">—</span>
    </div>
    <div class="tb-group spacer">
      <button class="btn icon" id="zoomOut" title="Zoom out" aria-label="Zoom out">−</button>
      <button class="btn icon" id="zoomIn" title="Zoom in" aria-label="Zoom in">+</button>
      <span id="zoomLabel">100%</span>
      <button class="btn primary" id="fitBtn">Fit view</button>
      <button class="btn" id="legendBtn" aria-expanded="false">Legend</button>
    </div>
  </div>

  <div id="stage">
    __PRODUCTION_SVG__

    <div id="hoverCard" role="tooltip" aria-hidden="true">
      <div class="card-head">
        <span class="card-id"></span>
        <span class="card-kind"></span>
      </div>
      <div class="kv"></div>
    </div>

    <aside id="details" aria-label="Selected asset details" aria-hidden="true">
      <div class="panel-head">
        <div style="flex:1 1 auto;min-width:0">
          <div class="panel-kind"></div>
          <div class="panel-id"></div>
        </div>
        <button id="detailsClose" aria-label="Close details">×</button>
      </div>
      <div class="panel-state">
        <span class="state-lamp"></span>
        <strong></strong>
        <span class="state-code"></span>
      </div>
      <div class="panel-body">
        <div class="kv"></div>
        <div class="panel-sub">Connected path</div>
        <div id="connectedTags"></div>
      </div>
    </aside>

    <section id="legend" aria-label="Drawing legend" aria-hidden="true">
      <div class="legend-head">
        <strong>Legend</strong>
        <span>state is paired with text, code or pattern</span>
      </div>
      <div class="legend-cols">
        <div>
          <div class="legend-cap">Material grade</div>
          <div class="legend-items" id="legendGrades"></div>
        </div>
        <div>
          <div class="legend-cap">Equipment state</div>
          <div class="legend-items" id="legendStates"></div>
        </div>
        <div class="notation">
          Dashed = future/reference · dimmed = filtered · navy halo = selected
          · blue halo = directly connected. Geometry remains schematic and
          not to scale.
        </div>
      </div>
    </section>

    <div class="hint">
      <strong>SCHEMATIC · NOT TO SCALE</strong>
      <span>wheel zoom · drag pan · double-click fit</span>
    </div>
  </div>
</div>

<script id="viewer-metadata" type="application/json">__METADATA_JSON__</script>
<script>
(function () {
  "use strict";

  var BASE = __BASE_JSON__;
  var metadataNode = document.getElementById("viewer-metadata");
  var metadata = {};
  try {
    metadata = JSON.parse(metadataNode.textContent || "{}");
  } catch (_error) {
    metadata = {};
  }

  var svg = document.getElementById("sheet");
  var stage = document.getElementById("stage");
  var hoverCard = document.getElementById("hoverCard");
  var details = document.getElementById("details");
  var activeSite = "ALL";
  var activeGrades = new Set();
  var selectedId = "";
  var stateFilter = "ALL STATES";
  var vb = {x: BASE.x, y: BASE.y, w: BASE.w, h: BASE.h};
  var pointer = null;

  var STATE = {
    "RUNNING": ["#2f7d4f", "R"],
    "OPERATING": ["#2f7d4f", "O"],
    "AVAILABLE": ["#2f7d4f", "A"],
    "STACKING": ["#2f7d4f", "S"],
    "RECLAIMING": ["#2f7d4f", "C"],
    "TRAVELLING": ["#2b5f9e", "T"],
    "IDLE": ["#8a9099", "I"],
    "STOPPED": ["#8a9099", "S"],
    "CONFIGURED": ["#6d7c87", "C"],
    "MAINTENANCE": ["#c08a1e", "M"],
    "WARNING": ["#c08a1e", "W"],
    "DEGRADED": ["#c08a1e", "D"],
    "UNAVAILABLE": ["#a83232", "U"],
    "DISABLED": ["#a83232", "D"],
    "TRIPPED": ["#a83232", "X"],
    "FAULT": ["#a83232", "X"],
    "FUTURE": ["#6b4fa8", "F"],
    "PLANNING DISABLED": ["#7d858c", "P"],
    "REFERENCE": ["#7d858c", "R"]
  };
  var GRADE = {
    "GLOBAL": "#3f7fa6",
    "RAW-BT-GLOBAL": "#3f7fa6",
    "HIGH-CD": "#8e4a63",
    "RAW-BT-HIGH-CD": "#8e4a63",
    "LOW-CD": "#c1592c",
    "RAW-BT-LOW-CD": "#c1592c",
    "RAW-TBC": "#7d858c",
    "TBT": "#1c6b73",
    "BT": "#e0891f",
    "MT": "#6b4fa8",
    "LT": "#f0b429"
  };
  var KIND = {
    "pile": "Stockpile",
    "machine": "Machine",
    "rail": "Machine rail",
    "route": "Conveyor route",
    "processline": "Process line",
    "equipment": "Equipment",
    "destination": "Destination",
    "hopper": "Mine hopper",
    "corridor": "Corridor interface"
  };
  var EQUIPMENT_KINDS = new Set(["machine", "equipment", "hopper"]);

  function arrayOf(value) {
    if (Array.isArray(value)) return value;
    if (value === null || value === undefined || value === "") return [];
    return [value];
  }
  function upper(value) {
    return String(value || "").trim().toUpperCase();
  }
  function kindKey(value) {
    return String(value || "equipment").replace(/[\s_-]+/g, "").toLowerCase();
  }
  function displayKind(value) {
    var key = kindKey(value);
    return KIND[key] || String(value || "Asset");
  }
  function detailPairs(value) {
    if (Array.isArray(value)) {
      return value
        .map(function (pair) {
          if (Array.isArray(pair) && pair.length >= 2)
            return [String(pair[0]), String(pair[1] ?? "—")];
          if (pair && typeof pair === "object") {
            var key = pair.key ?? pair.label ?? pair.name;
            if (key !== null && key !== undefined)
              return [String(key), String(pair.value ?? "—")];
          }
          return null;
        })
        .filter(Boolean);
    }
    if (value && typeof value === "object") {
      return Object.keys(value).map(function (key) {
        return [String(key), String(value[key] ?? "—")];
      });
    }
    return [];
  }
  function normaliseAsset(raw, index) {
    raw = raw && typeof raw === "object" ? raw : {};
    var id = String(raw.id || raw.assetId || raw.label || ("asset-" + index));
    var connectionFields = [
      raw.connections,
      raw.connected,
      raw.servedPileIds,
      raw.machineIds,
      raw.linkIds,
      raw.endpointAssetIds,
      raw.destinationIds,
      raw.visualIds
    ];
    var connections = [];
    connectionFields.forEach(function (field) {
      arrayOf(field).forEach(function (value) {
        var text = String(value);
        if (text && connections.indexOf(text) < 0) connections.push(text);
      });
    });
    return {
      id: id,
      kind: String(raw.kind || "equipment"),
      kindKey: kindKey(raw.kind),
      site: upper(raw.site || raw.siteId || "GLOBAL"),
      label: String(raw.label || id),
      state: String(
        raw.state || raw.operatingState || raw.lifecycleState || ""),
      grades: arrayOf(raw.grades || raw.gradeIds || raw.grade).map(String),
      details: detailPairs(raw.details),
      connections: connections,
      selected: Boolean(raw.selected)
    };
  }

  var assets = new Map();
  function mergeAssets(current, incoming) {
    if (!current) return incoming;
    var detailMap = new Map();
    current.details.concat(incoming.details).forEach(function (pair) {
      detailMap.set(upper(pair[0]), pair);
    });
    return {
      id: current.id,
      kind: incoming.kind || current.kind,
      kindKey: incoming.kindKey || current.kindKey,
      site: incoming.site && incoming.site !== "GLOBAL"
        ? incoming.site : current.site,
      label: incoming.label || current.label,
      state: incoming.state || current.state,
      grades: Array.from(new Set(current.grades.concat(incoming.grades))),
      details: Array.from(detailMap.values()),
      connections: Array.from(
        new Set(current.connections.concat(incoming.connections))),
      selected: current.selected || incoming.selected
    };
  }
  arrayOf(metadata.assets).map(normaliseAsset).forEach(function (asset) {
    assets.set(asset.id, mergeAssets(assets.get(asset.id), asset));
  });
  var assetList = Array.from(assets.values());
  var labels = new Map();
  assetList.forEach(function (asset) {
    labels.set(upper(asset.label), asset);
    labels.set(upper(asset.id), asset);
  });
  var adjacency = new Map();
  function connect(left, right) {
    if (!left || !right || left === right
        || !assets.has(left) || !assets.has(right)) return;
    if (!adjacency.has(left)) adjacency.set(left, new Set());
    if (!adjacency.has(right)) adjacency.set(right, new Set());
    adjacency.get(left).add(right);
    adjacency.get(right).add(left);
  }
  assetList.forEach(function (asset) {
    asset.connections.forEach(function (other) {
      connect(asset.id, other);
    });
  });
  assetList.forEach(function (asset) {
    asset.connections = Array.from(adjacency.get(asset.id) || []);
  });
  var requestedSelection = metadata.filters
    && metadata.filters.selectedAssetId;
  if (requestedSelection && assets.has(String(requestedSelection))) {
    selectedId = String(requestedSelection);
  } else {
    var initiallySelected = assetList.find(function (asset) {
      return asset.selected && asset.kindKey === "machine";
    }) || assetList.find(function (asset) { return asset.selected; });
    if (initiallySelected) selectedId = initiallySelected.id;
  }

  function tooltipText(node) {
    if (!node) return "";
    return node.getAttribute("data-tooltip")
      || node.getAttribute("aria-label")
      || "";
  }
  function firstLine(value) {
    return String(value || "").split(/\r?\n/)[0].trim();
  }
  function inferredAsset(node) {
    var wrapper = node && node.closest
      ? node.closest("[data-asset-id]")
      : null;
    if (wrapper) {
      var direct = assets.get(wrapper.getAttribute("data-asset-id"));
      if (direct) return direct;
    }
    var label = firstLine(tooltipText(node));
    return labels.get(upper(label)) || null;
  }

  var nodesByAsset = new Map();
  function rememberNode(id, node) {
    if (!id || !node) return;
    if (!nodesByAsset.has(id)) nodesByAsset.set(id, []);
    if (nodesByAsset.get(id).indexOf(node) < 0) nodesByAsset.get(id).push(node);
  }
  svg.querySelectorAll(".asset[data-asset-id]").forEach(function (node) {
    var id = node.getAttribute("data-asset-id");
    rememberNode(id, node);
    if (!assets.has(id)) {
      var fallback = normaliseAsset({
        id: id,
        kind: node.getAttribute("data-asset-kind") || "equipment",
        site: node.getAttribute("data-site") || "GLOBAL",
        state: node.getAttribute("data-state") || "",
        grades: String(node.getAttribute("data-grade") || "")
          .split(/\s*[|,]\s*/).filter(Boolean)
      }, assets.size);
      assets.set(id, fallback);
      assetList.push(fallback);
      labels.set(upper(fallback.id), fallback);
    }
  });
  svg.querySelectorAll(".hover-target").forEach(function (target) {
    var asset = inferredAsset(target);
    if (!asset) return;
    target.setAttribute("data-viewer-asset-id", asset.id);
    rememberNode(asset.id, target.closest(".asset") || target);
  });

  // One canonical asset may have a wrapper in several painter layers.  Keep
  // one keyboard stop while every layer still receives visual classes.
  nodesByAsset.forEach(function (nodes, id) {
    var preferred = nodes.find(function (node) {
      return node.matches(".hover-target") || node.querySelector(".hover-target");
    }) || nodes[nodes.length - 1];
    nodes.forEach(function (node) {
      if (node === preferred) {
        node.setAttribute("tabindex", "0");
        node.setAttribute("role", "button");
        node.addEventListener("focus", function () {
          nodes.forEach(function (part) { part.classList.add("keyboard-focus"); });
        });
        node.addEventListener("blur", function () {
          nodes.forEach(function (part) { part.classList.remove("keyboard-focus"); });
        });
        node.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            selectAsset(assets.get(id) || null);
          }
        });
      } else {
        node.removeAttribute("tabindex");
        node.removeAttribute("role");
      }
    });
  });

  function metaValue(keys, fallback) {
    for (var i = 0; i < keys.length; i += 1) {
      var value = metadata[keys[i]];
      if (value !== null && value !== undefined && value !== "") return value;
    }
    return fallback;
  }
  var timestamp = String(metaValue(
    ["timestamp", "stateTime", "state_time", "time"], "—"));
  var basis = String(metaValue(
    ["basis", "stateBasis", "state_basis"], "Current state"));
  var rawView = metadata.site;
  if (!rawView && metadata.view && typeof metadata.view === "object")
    rawView = metadata.view.id || metadata.view.site || metadata.view.label;
  if (!rawView) rawView = metadata.view;
  var initialView = upper(rawView || "ALL");
  document.getElementById("timeLabel").textContent = timestamp;
  document.getElementById("basisLabel").textContent = "· " + basis;
  document.getElementById("basisLabel").title = basis;

  function gradeDefinition(raw) {
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      var id = String(raw.id || raw.value || raw.label || "");
      return {
        id: id,
        label: String(raw.label || id),
        color: String(raw.color || GRADE[upper(id)] || "#7d858c")
      };
    }
    var value = String(raw || "");
    return {id: value, label: value, color: GRADE[upper(value)] || "#7d858c"};
  }
  var gradeDefs = arrayOf(metadata.grades || metadata.gradeIds)
    .map(gradeDefinition)
    .filter(function (item) { return item.id; });
  if (!gradeDefs.length) {
    var collectedGrades = new Set();
    assetList.forEach(function (asset) {
      asset.grades.forEach(function (grade) { collectedGrades.add(grade); });
    });
    gradeDefs = Array.from(collectedGrades).map(gradeDefinition);
  }
  var configuredGradeIds = arrayOf(
    metadata.filters && metadata.filters.gradeIds).map(String);
  gradeDefs.forEach(function (item) {
    if (!configuredGradeIds.length || configuredGradeIds.indexOf(item.id) >= 0)
      activeGrades.add(item.id);
  });

  function orderedSites() {
    var values = new Set();
    assetList.forEach(function (asset) {
      if (asset.site && asset.site !== "GLOBAL" && asset.site !== "ALL")
        values.add(asset.site);
    });
    var order = ["LOUTA", "BENGUERIR", "MPH"];
    return order.filter(function (site) { return values.has(site); })
      .concat(Array.from(values).filter(function (site) {
        return order.indexOf(site) < 0;
      }).sort());
  }
  var sites = orderedSites();
  activeSite = sites.indexOf(initialView) >= 0 ? initialView : "ALL";

  function siteLabel(site) {
    if (site === "ALL") return "All";
    if (site === "MPH") return "MPH M16";
    return site.charAt(0) + site.slice(1).toLowerCase();
  }
  function buildSiteTabs() {
    var host = document.getElementById("siteTabs");
    ["ALL"].concat(sites).forEach(function (site) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "tab";
      button.textContent = siteLabel(site);
      button.setAttribute("aria-pressed", String(site === activeSite));
      button.addEventListener("click", function () {
        activeSite = site;
        host.querySelectorAll("button").forEach(function (item) {
          item.setAttribute("aria-pressed", String(item === button));
        });
        applyAssetClasses();
        fitCurrent();
      });
      host.appendChild(button);
    });
  }

  function buildGradeChips() {
    var host = document.getElementById("gradeChips");
    gradeDefs.forEach(function (grade) {
      var button = document.createElement("button");
      var swatch = document.createElement("span");
      var label = document.createElement("span");
      button.type = "button";
      button.className = "chip";
      button.setAttribute(
        "aria-pressed", String(activeGrades.has(grade.id)));
      button.setAttribute("aria-label", "Toggle grade " + grade.label);
      swatch.className = "swatch";
      swatch.style.backgroundColor = grade.color;
      label.textContent = grade.label;
      button.appendChild(swatch);
      button.appendChild(label);
      button.addEventListener("click", function () {
        if (activeGrades.has(grade.id)) activeGrades.delete(grade.id);
        else activeGrades.add(grade.id);
        button.setAttribute(
          "aria-pressed", String(activeGrades.has(grade.id)));
        applyAssetClasses();
      });
      host.appendChild(button);
    });
    if (!gradeDefs.length) {
      var empty = document.createElement("span");
      empty.className = "tb-cap";
      empty.textContent = "No grade metadata";
      host.appendChild(empty);
    }
  }

  function buildStateFilter() {
    var states = new Set();
    assetList.forEach(function (asset) {
      if (EQUIPMENT_KINDS.has(asset.kindKey) && asset.state)
        states.add(upper(asset.state));
    });
    var select = document.getElementById("stateFilter");
    ["ALL STATES"].concat(Array.from(states).sort()).forEach(function (state) {
      var option = document.createElement("option");
      option.value = state;
      option.textContent = state === "ALL STATES"
        ? "All states"
        : state.charAt(0) + state.slice(1).toLowerCase();
      select.appendChild(option);
    });
    select.addEventListener("change", function () {
      stateFilter = select.value;
      applyAssetClasses();
    });
  }

  function detailValue(asset, keys) {
    var wanted = keys.map(upper);
    var row = asset.details.find(function (pair) {
      return wanted.indexOf(upper(pair[0])) >= 0;
    });
    return row ? row[1] : "";
  }
  function showPap2Lifecycle() {
    var pap2 = assetList.find(function (asset) {
      return upper(asset.id) === "PAP-2" || upper(asset.label) === "PAP-2";
    });
    var lifecycle = String(metaValue(
      ["pap2Lifecycle", "pap2_lifecycle", "pap2State", "pap2_state"],
      pap2
        ? (pap2.state || detailValue(pap2, ["Lifecycle", "Lifecycle state"]))
        : (
          metadata.lifecycle && metadata.lifecycle["PAP-2"]
          ? metadata.lifecycle["PAP-2"]
          : "Not configured"
        )
    ));
    var output = document.getElementById("pap2Lifecycle");
    output.textContent = lifecycle || "Not configured";
    output.style.borderColor = stateColor(lifecycle);
    output.title = "Read-only lifecycle from the current scenario";
    output.setAttribute(
      "aria-label", "PAP-2 lifecycle, read only: " + output.textContent);
  }

  function passesFilters(asset) {
    var sitePass = activeSite === "ALL"
      || asset.site === activeSite
      || asset.site === "GLOBAL";
    var gradePass = !asset.grades.length
      || asset.grades.some(function (grade) { return activeGrades.has(grade); });
    var statePass = stateFilter === "ALL STATES"
      || !EQUIPMENT_KINDS.has(asset.kindKey)
      || upper(asset.state) === stateFilter;
    return sitePass && gradePass && statePass;
  }
  function applyAssetClasses() {
    var selected = assets.get(selectedId) || null;
    var connected = new Set(selected ? selected.connections : []);
    nodesByAsset.forEach(function (nodes, id) {
      var asset = assets.get(id);
      var filtered = asset ? !passesFilters(asset) : false;
      nodes.forEach(function (node) {
        node.classList.toggle("is-filtered", filtered);
        node.classList.toggle(
          "is-selection-dim",
          Boolean(selected) && id !== selectedId && !connected.has(id));
        node.classList.toggle("is-selected", id === selectedId);
        node.classList.toggle("is-connected", connected.has(id));
      });
    });
  }

  function stateInfo(state) {
    var key = upper(state);
    if (STATE[key]) return STATE[key];
    if (key.indexOf("FUTURE") >= 0) return STATE.FUTURE;
    if (key.indexOf("DISABLED") >= 0) return STATE.DISABLED;
    if (key.indexOf("FAULT") >= 0 || key.indexOf("TRIP") >= 0)
      return STATE.FAULT;
    if (key.indexOf("MAINT") >= 0) return STATE.MAINTENANCE;
    if (key.indexOf("RUN") >= 0) return STATE.RUNNING;
    return ["#8a9099", key ? key.charAt(0) : "—"];
  }
  function stateColor(state) {
    return stateInfo(state)[0];
  }
  function rowsFor(asset, compact) {
    var rows = asset.details.slice();
    function contains(label) {
      return rows.some(function (pair) {
        return upper(pair[0]) === upper(label);
      });
    }
    if (asset.state && !contains("State"))
      rows.unshift(["State", asset.state]);
    if (asset.grades.length && !contains("Grade") && !contains("Grades"))
      rows.push(["Grades", asset.grades.join(" · ")]);
    if (!compact && asset.site && asset.site !== "GLOBAL" && !contains("Site"))
      rows.push(["Site", asset.site]);
    return compact ? rows.slice(0, 5) : rows;
  }
  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }
  function renderPairs(host, pairs) {
    clearNode(host);
    pairs.forEach(function (pair) {
      var key = document.createElement("span");
      var value = document.createElement("span");
      key.className = "key";
      value.className = "value";
      key.textContent = pair[0];
      value.textContent = pair[1];
      host.appendChild(key);
      host.appendChild(value);
    });
  }

  function assetFromEvent(event) {
    var node = event.target && event.target.closest
      ? event.target.closest(
          "[data-viewer-asset-id],.asset[data-asset-id],.hover-target")
      : null;
    if (!node) return null;
    var id = node.getAttribute("data-viewer-asset-id")
      || node.getAttribute("data-asset-id");
    if (id && assets.has(id)) return assets.get(id);
    return inferredAsset(node);
  }
  function showHover(event) {
    if (pointer && pointer.moved) {
      hideHover();
      return;
    }
    var asset = assetFromEvent(event);
    if (!asset || !passesFilters(asset)) {
      hideHover();
      return;
    }
    hoverCard.querySelector(".card-id").textContent = asset.label;
    hoverCard.querySelector(".card-kind").textContent = displayKind(asset.kind);
    renderPairs(hoverCard.querySelector(".kv"), rowsFor(asset, true));
    hoverCard.style.borderLeftColor = stateColor(asset.state);
    hoverCard.classList.add("show");
    hoverCard.setAttribute("aria-hidden", "false");
    var stageRect = stage.getBoundingClientRect();
    var gap = 14;
    var edge = 8;
    var left = event.clientX - stageRect.left + gap;
    var top = event.clientY - stageRect.top + gap;
    if (left + hoverCard.offsetWidth > stageRect.width - edge)
      left = event.clientX - stageRect.left - hoverCard.offsetWidth - gap;
    if (top + hoverCard.offsetHeight > stageRect.height - edge)
      top = stageRect.height - hoverCard.offsetHeight - edge;
    hoverCard.style.left = Math.max(edge, left) + "px";
    hoverCard.style.top = Math.max(edge, top) + "px";
  }
  function hideHover() {
    hoverCard.classList.remove("show");
    hoverCard.setAttribute("aria-hidden", "true");
  }

  function renderDetails(asset) {
    if (!asset) {
      details.classList.remove("open");
      details.setAttribute("aria-hidden", "true");
      return;
    }
    var info = stateInfo(asset.state);
    details.querySelector(".panel-kind").textContent = displayKind(asset.kind);
    details.querySelector(".panel-id").textContent = asset.label;
    details.querySelector(".state-lamp").style.backgroundColor = info[0];
    details.querySelector(".panel-state strong").textContent =
      asset.state || "Configured";
    details.querySelector(".state-code").textContent =
      asset.state ? ("code " + info[1]) : asset.site;
    renderPairs(details.querySelector(".panel-body .kv"), rowsFor(asset, false));
    var tags = document.getElementById("connectedTags");
    clearNode(tags);
    if (asset.connections.length) {
      asset.connections.forEach(function (id) {
        var tag = document.createElement("span");
        tag.textContent = assets.has(id) ? assets.get(id).label : id;
        tags.appendChild(tag);
      });
    } else {
      var none = document.createElement("span");
      none.textContent = "No connected metadata";
      tags.appendChild(none);
    }
    details.classList.add("open");
    details.setAttribute("aria-hidden", "false");
  }
  function selectAsset(asset) {
    selectedId = asset ? asset.id : "";
    applyAssetClasses();
    renderDetails(asset);
    hideHover();
  }

  function visibleSiteBounds(site) {
    if (site === "ALL") return null;
    var box = null;
    assetList.filter(function (asset) {
      return asset.site === site;
    }).forEach(function (asset) {
      (nodesByAsset.get(asset.id) || []).forEach(function (node) {
        if (node.classList.contains("is-filtered")) return;
        try {
          var current = node.getBBox();
          if (!current.width && !current.height) return;
          if (!box) {
            box = {
              x: current.x, y: current.y,
              x2: current.x + current.width, y2: current.y + current.height
            };
          } else {
            box.x = Math.min(box.x, current.x);
            box.y = Math.min(box.y, current.y);
            box.x2 = Math.max(box.x2, current.x + current.width);
            box.y2 = Math.max(box.y2, current.y + current.height);
          }
        } catch (_error) {
          // SVG nodes without rendered geometry simply do not affect fitting.
        }
      });
    });
    if (!box) return null;
    var width = Math.max(1, box.x2 - box.x);
    var height = Math.max(1, box.y2 - box.y);
    var pad = Math.max(18, Math.max(width, height) * .07);
    return {x: box.x - pad, y: box.y - pad, w: width + 2 * pad, h: height + 2 * pad};
  }
  function aspectCorrect(box) {
    var rect = stage.getBoundingClientRect();
    var ratio = rect.width / Math.max(1, rect.height);
    var result = {x: box.x, y: box.y, w: box.w, h: box.h};
    if (result.w / result.h > ratio) {
      var wantedH = result.w / ratio;
      result.y -= (wantedH - result.h) / 2;
      result.h = wantedH;
    } else {
      var wantedW = result.h * ratio;
      result.x -= (wantedW - result.w) / 2;
      result.w = wantedW;
    }
    return result;
  }
  function applyView() {
    svg.setAttribute(
      "viewBox", [vb.x, vb.y, vb.w, vb.h].join(" "));
    var zoom = BASE.w / vb.w;
    document.getElementById("zoomLabel").textContent =
      Math.round(zoom * 100) + "%";
    stage.classList.toggle("detail-2", zoom >= 1.25 && zoom < 2);
    stage.classList.toggle("detail-3", zoom >= 2);
  }
  function fitAll() {
    vb = {x: BASE.x, y: BASE.y, w: BASE.w, h: BASE.h};
    applyView();
  }
  function fitCurrent() {
    var bounds = visibleSiteBounds(activeSite);
    if (!bounds) {
      fitAll();
      return;
    }
    vb = aspectCorrect(bounds);
    applyView();
  }
  function clampView() {
    var zoom = BASE.w / vb.w;
    if (zoom < .65) {
      vb.w = BASE.w / .65;
      vb.h = BASE.h / .65;
    }
    if (zoom > 14) {
      vb.w = BASE.w / 14;
      vb.h = BASE.h / 14;
    }
    var marginX = BASE.w * .9;
    var marginY = BASE.h * .9;
    vb.x = Math.min(
      Math.max(vb.x, BASE.x - marginX),
      BASE.x + BASE.w + marginX - vb.w);
    vb.y = Math.min(
      Math.max(vb.y, BASE.y - marginY),
      BASE.y + BASE.h + marginY - vb.h);
  }
  function zoomAt(factor, clientX, clientY) {
    var rect = stage.getBoundingClientRect();
    var px = (clientX - rect.left) / Math.max(1, rect.width);
    var py = (clientY - rect.top) / Math.max(1, rect.height);
    var anchorX = vb.x + vb.w * px;
    var anchorY = vb.y + vb.h * py;
    vb.w /= factor;
    vb.h /= factor;
    vb.x = anchorX - vb.w * px;
    vb.y = anchorY - vb.h * py;
    clampView();
    applyView();
  }
  function zoomCentre(factor) {
    var rect = stage.getBoundingClientRect();
    zoomAt(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
  }

  function buildLegend() {
    var gradeHost = document.getElementById("legendGrades");
    gradeDefs.forEach(function (grade) {
      var item = document.createElement("div");
      var swatch = document.createElement("span");
      var label = document.createElement("code");
      item.className = "legend-item";
      swatch.className = "swatch";
      swatch.style.backgroundColor = grade.color;
      label.textContent = grade.label;
      item.appendChild(swatch);
      item.appendChild(label);
      gradeHost.appendChild(item);
    });
    var presentStates = new Set();
    assetList.forEach(function (asset) {
      if (asset.state) presentStates.add(upper(asset.state));
    });
    var stateHost = document.getElementById("legendStates");
    Array.from(presentStates).sort().forEach(function (state) {
      var info = stateInfo(state);
      var item = document.createElement("div");
      var lamp = document.createElement("span");
      var label = document.createElement("span");
      item.className = "legend-item";
      lamp.className = "legend-lamp";
      lamp.style.backgroundColor = info[0];
      lamp.textContent = info[1];
      label.textContent = state.charAt(0) + state.slice(1).toLowerCase();
      item.appendChild(lamp);
      item.appendChild(label);
      stateHost.appendChild(item);
    });
  }

  stage.addEventListener("wheel", function (event) {
    event.preventDefault();
    hideHover();
    zoomAt(
      event.deltaY < 0 ? 1.16 : 1 / 1.16,
      event.clientX,
      event.clientY
    );
  }, {passive: false});

  stage.addEventListener("pointerdown", function (event) {
    if (event.button !== 0) return;
    pointer = {
      id: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      viewX: vb.x,
      viewY: vb.y,
      asset: assetFromEvent(event),
      moved: false
    };
    stage.setPointerCapture(event.pointerId);
    hideHover();
  });
  stage.addEventListener("pointermove", function (event) {
    if (!pointer) {
      showHover(event);
      return;
    }
    var dx = event.clientX - pointer.x;
    var dy = event.clientY - pointer.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) {
      pointer.moved = true;
      stage.classList.add("dragging");
    }
    if (!pointer.moved) return;
    var rect = stage.getBoundingClientRect();
    vb.x = pointer.viewX - dx * vb.w / Math.max(1, rect.width);
    vb.y = pointer.viewY - dy * vb.h / Math.max(1, rect.height);
    clampView();
    applyView();
  });
  stage.addEventListener("pointerup", function (event) {
    if (!pointer) return;
    var wasMoved = pointer.moved;
    var asset = pointer.asset;
    pointer = null;
    stage.classList.remove("dragging");
    try { stage.releasePointerCapture(event.pointerId); } catch (_error) {}
    if (!wasMoved) selectAsset(asset);
  });
  stage.addEventListener("pointercancel", function () {
    pointer = null;
    stage.classList.remove("dragging");
  });
  stage.addEventListener("pointerleave", hideHover);
  stage.addEventListener("dblclick", function (event) {
    event.preventDefault();
    fitCurrent();
  });

  document.getElementById("zoomIn").addEventListener(
    "click", function () { zoomCentre(1.35); });
  document.getElementById("zoomOut").addEventListener(
    "click", function () { zoomCentre(1 / 1.35); });
  document.getElementById("fitBtn").addEventListener("click", fitCurrent);
  document.getElementById("detailsClose").addEventListener(
    "click", function () { selectAsset(null); });

  var legend = document.getElementById("legend");
  var legendButton = document.getElementById("legendBtn");
  legendButton.addEventListener("click", function () {
    var open = !legend.classList.contains("open");
    legend.classList.toggle("open", open);
    legendButton.classList.toggle("on", open);
    legendButton.setAttribute("aria-expanded", String(open));
    legend.setAttribute("aria-hidden", String(!open));
  });
  window.addEventListener("keydown", function (event) {
    var tag = event.target && event.target.tagName
      ? event.target.tagName.toLowerCase()
      : "";
    if (tag === "select" || tag === "button") return;
    if (event.key === "+" || event.key === "=") zoomCentre(1.35);
    else if (event.key === "-") zoomCentre(1 / 1.35);
    else if (event.key === "0") fitCurrent();
    else if (event.key === "Escape") {
      selectAsset(null);
      legend.classList.remove("open");
      legendButton.classList.remove("on");
      legendButton.setAttribute("aria-expanded", "false");
      legend.setAttribute("aria-hidden", "true");
    }
  });

  buildSiteTabs();
  buildGradeChips();
  buildStateFilter();
  showPap2Lifecycle();
  buildLegend();
  applyAssetClasses();
  applyView();
  var initialSelection = metadata.filters
    ? metadata.filters.selectedAssetId
    : "";
  if (initialSelection && assets.has(String(initialSelection)))
    selectAsset(assets.get(String(initialSelection)));
})();
</script>
</body>
</html>
"""
