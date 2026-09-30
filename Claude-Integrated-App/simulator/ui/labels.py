"""Single source of truth for site-qualified display labels.

The engine keeps short internal IDs (`ST01-A`, `140LG-RC-01`, `BG-P1`). Every
cross-site UI surface — Gantt axes, mimic tags, selectors, exports — shows the
SITE-QUALIFIED form `MPH · ST01-A` / `LOU · 140LG-RC-01` / `BEN · BG-P1` so that
three SMS yards on one timeline are never ambiguous. The physical/engineering
tag is preserved separately (never overwritten).

This module has NO dependency on theme, layout_geometry or Streamlit, so both
of those modules can import it without a cycle (the previous duplicate
`theme.machine_display_label` / `layout_geometry.site_tag` divergence is
resolved by re-exporting the functions defined HERE).
"""
from __future__ import annotations

#: canonical site -> compact code map (single definition)
SITE_PREFIXES = {"LOUTA": "LOU", "BENGUERIR": "BEN", "MPH": "MPH"}

#: backwards-compatible alias (layout_geometry historically used SITE_PREFIX)
SITE_PREFIX = SITE_PREFIXES


def site_prefix(site: str) -> str:
    """Compact site code used in cross-site asset labels (case-tolerant)."""
    key = str(site or "?").upper()
    return SITE_PREFIXES.get(key, key)


def site_tag(site: str, tag: str) -> str:
    """Site-qualified display label, e.g. site_tag('MPH','ST01-A') -> 'MPH · ST01-A'."""
    return f"{site_prefix(site)} · {tag}"


def machine_display_label(sc, machine_id: str) -> str:
    """Site-qualified label for a machine ID, resolving its site from the
    scenario. Engine IDs are never changed."""
    try:
        site = sc.machine(machine_id).site
    except (StopIteration, AttributeError):
        site = "?"
    return site_tag(site, machine_id)
