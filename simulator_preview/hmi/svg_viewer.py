"""Zoom / pan viewer for an SVG sheet, with label decluttering by zoom.

Why this exists
---------------
Two complaints the static sheet could not answer:

* it was not zoomable or pannable — a yard drawing has to be inspectable;
* label text overcrowded the drawing while the elements were still small.

Both are the same problem: a schematic cannot show every label at fit-zoom.
The renderer tags each label with a level-of-detail tier
(``class="lod1|lod2|lod3"``), and this viewer reveals tiers as the operator
zooms in:

    fit … 1.25×   structure only — bay/zone titles, destination boxes,
                  title block, machine state lamps
    1.25 … 2.0×   + machine tags, pile identity/tonnage, grade bands,
                  repère R0/R500/R1000, corridor labels
    2.0× …        + rail IDs, conveyor tags/rates, gate numbers, evidence
                  and TBC prose, drawing stamps, quarter repères

Self-contained: one inline stylesheet and one inline script, no network, so it
works inside a Streamlit ``components.html`` iframe (which is origin-isolated
and inherits no parent CSS).
"""
from __future__ import annotations

import re
from typing import Tuple

from . import tokens as t

_VIEWBOX = re.compile(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"')

#: Zoom at which each detail tier switches on.
LOD_THRESHOLDS = {2: 1.25, 3: 2.0}


def _fill_svg(svg: str) -> str:
    """Make the sheet fill its frame so the viewBox controls the zoom."""
    svg = re.sub(r'\swidth="[^"]*"', "", svg, count=1)
    return svg.replace(
        "<svg ", '<svg id="sheet" width="100%" height="100%" ', 1)


def viewbox_of(svg: str) -> Tuple[float, float, float, float]:
    m = _VIEWBOX.search(svg)
    if not m:                                        # pragma: no cover
        raise ValueError("sheet has no viewBox")
    return tuple(float(g) for g in m.groups())       # type: ignore[return-value]


def html(svg: str, *, height: int, dark: bool = False,
         min_zoom: float = 1.0, max_zoom: float = 14.0) -> str:
    """Wrap ``svg`` in a pan/zoom viewer sized to ``height`` px."""
    x, y, w, h = viewbox_of(svg)
    sheet_bg = "#131a20" if dark else "#eef1f3"
    bar_bg = t.INK_700 if dark else "#dfe4e8"
    bar_ink = t.TXT_300 if dark else "#41505c"
    btn_bg = t.INK_600 if dark else "#ffffff"
    line = t.INK_400 if dark else "#b3bec6"

    return f"""
<style>
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%;
                background: {sheet_bg};
                font-family: 'IBM Plex Sans','Segoe UI',Roboto,sans-serif; }}
  #wrap {{ display: flex; flex-direction: column; height: 100%; }}
  #bar {{ display: flex; align-items: center; gap: 6px; flex: 0 0 30px;
          background: {bar_bg}; border-bottom: 1px solid {line};
          padding: 0 8px; color: {bar_ink}; font-size: 11px;
          user-select: none; }}
  #bar button {{ font: 600 11px/1 inherit; color: {bar_ink};
                 background: {btn_bg}; border: 1px solid {line};
                 border-radius: 2px; height: 20px; min-width: 24px;
                 padding: 0 7px; cursor: pointer; }}
  #bar button:hover {{ border-color: {t.ACCENT}; color: {t.ACCENT}; }}
  #bar .sep {{ width: 1px; height: 16px; background: {line}; }}
  #bar .ro {{ font-family: 'IBM Plex Mono',Consolas,monospace;
              min-width: 52px; text-align: right; }}
  #bar .hint {{ margin-left: auto; opacity: .75; }}
  #stage {{ flex: 1 1 auto; overflow: hidden; cursor: grab;
            background: {sheet_bg}; touch-action: none; }}
  #stage.drag {{ cursor: grabbing; }}
  /* label decluttering — tiers appear as the viewer zooms in */
  .lod2, .lod3 {{ display: none; }}
  #stage.d2 .lod2 {{ display: inline; }}
  #stage.d3 .lod2, #stage.d3 .lod3 {{ display: inline; }}
</style>
<div id="wrap">
  <div id="bar">
    <button id="out" title="zoom out (−)">−</button>
    <button id="in" title="zoom in (+)">+</button>
    <button id="fit" title="fit the sheet (0 / double-click)">Fit</button>
    <span class="ro" id="ro">100%</span>
    <span class="sep"></span>
    <span id="lod">detail: structure</span>
    <span class="hint">wheel = zoom · drag = pan · double-click = fit</span>
  </div>
  <div id="stage">{_fill_svg(svg)}</div>
</div>
<script>
(function () {{
  var BASE = {{x: {x}, y: {y}, w: {w}, h: {h}}};
  var MIN = {min_zoom}, MAX = {max_zoom};
  var vb = Object.assign({{}}, BASE);
  var svg = document.getElementById('sheet');
  var stage = document.getElementById('stage');
  var ro = document.getElementById('ro'), lodOut = document.getElementById('lod');
  var T2 = {LOD_THRESHOLDS[2]}, T3 = {LOD_THRESHOLDS[3]};

  function apply() {{
    svg.setAttribute('viewBox', vb.x + ' ' + vb.y + ' ' + vb.w + ' ' + vb.h);
    var z = BASE.w / vb.w;
    ro.textContent = Math.round(z * 100) + '%';
    stage.classList.toggle('d2', z >= T2 && z < T3);
    stage.classList.toggle('d3', z >= T3);
    lodOut.textContent = 'detail: ' + (z >= T3 ? 'full (tags, rates, evidence)'
                         : z >= T2 ? 'assets (ids, tonnage, repere)'
                         : 'structure');
  }}
  function clamp() {{
    var z = BASE.w / vb.w;
    if (z < MIN) {{ vb.w = BASE.w / MIN; vb.h = BASE.h / MIN; }}
    if (z > MAX) {{ vb.w = BASE.w / MAX; vb.h = BASE.h / MAX; }}
    // keep the sheet from being panned entirely out of view
    var mx = BASE.w * 0.9, my = BASE.h * 0.9;
    vb.x = Math.min(Math.max(vb.x, BASE.x - mx), BASE.x + BASE.w + mx - vb.w);
    vb.y = Math.min(Math.max(vb.y, BASE.y - my), BASE.y + BASE.h + my - vb.h);
  }}
  function zoomAt(f, cx, cy) {{
    var r = stage.getBoundingClientRect();
    var px = (cx - r.left) / r.width, py = (cy - r.top) / r.height;
    var ax = vb.x + vb.w * px, ay = vb.y + vb.h * py;
    vb.w /= f; vb.h /= f;
    vb.x = ax - vb.w * px; vb.y = ay - vb.h * py;
    clamp(); apply();
  }}
  function fit() {{ vb = Object.assign({{}}, BASE); apply(); }}

  stage.addEventListener('wheel', function (ev) {{
    ev.preventDefault();
    zoomAt(ev.deltaY < 0 ? 1.16 : 1 / 1.16, ev.clientX, ev.clientY);
  }}, {{passive: false}});

  var drag = null;
  stage.addEventListener('pointerdown', function (ev) {{
    drag = {{x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y}};
    stage.classList.add('drag'); stage.setPointerCapture(ev.pointerId);
  }});
  stage.addEventListener('pointermove', function (ev) {{
    if (!drag) return;
    var r = stage.getBoundingClientRect();
    vb.x = drag.vx - (ev.clientX - drag.x) * vb.w / r.width;
    vb.y = drag.vy - (ev.clientY - drag.y) * vb.h / r.height;
    clamp(); apply();
  }});
  stage.addEventListener('pointerup', function () {{
    drag = null; stage.classList.remove('drag');
  }});
  stage.addEventListener('dblclick', fit);

  document.getElementById('in').onclick = function () {{
    var r = stage.getBoundingClientRect();
    zoomAt(1.35, r.left + r.width / 2, r.top + r.height / 2);
  }};
  document.getElementById('out').onclick = function () {{
    var r = stage.getBoundingClientRect();
    zoomAt(1 / 1.35, r.left + r.width / 2, r.top + r.height / 2);
  }};
  document.getElementById('fit').onclick = fit;
  window.addEventListener('keydown', function (ev) {{
    if (ev.key === '+' || ev.key === '=') document.getElementById('in').click();
    else if (ev.key === '-') document.getElementById('out').click();
    else if (ev.key === '0') fit();
  }});
  apply();
}})();
</script>
"""
