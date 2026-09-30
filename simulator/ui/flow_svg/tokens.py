"""Design tokens for the operations-console preview.

Rationale — why this is not just "a darker theme":

ISA-101 / high-performance-HMI practice separates the CONSOLE CHROME from the
PROCESS GRAPHIC:

  * the chrome (navigation, session, KPI, alarm) is low-luminance graphite so
    it recedes and never competes with the drawing for attention;
  * the process graphic keeps a light-medium grey canvas — that is the
    ISA-101 recommendation, not a dark one — with desaturated line work;
  * saturated red / amber are reserved EXCLUSIVELY for abnormal conditions.
    Nothing decorative may use them.

Two encodings already carried by the production drawing are preserved
unchanged, because neutralising them would destroy information:

  * grade-coloured pile fill (process information), and
  * the evidence palette (CONFIRMED / DERIVED / INFERRED / ASSUMED / TBC).

Both are already redundantly encoded by text, icon or line pattern, which is
what the accessibility rule in the drawing-conventions note promises.
"""
from __future__ import annotations

# ---------------------------------------------------------------- chrome -----
INK_900 = "#10161c"          # console backdrop
INK_800 = "#161e25"          # title bar
INK_700 = "#1c262e"          # panel
INK_600 = "#232f39"          # raised panel / input
INK_500 = "#2d3b47"          # hairline strong
INK_400 = "#3a4a58"          # hairline
TXT_100 = "#e6edf3"          # primary text on dark
TXT_300 = "#a9bccb"          # secondary
TXT_500 = "#7c8f9e"          # tertiary / captions
TXT_700 = "#5b6b78"          # disabled

# ------------------------------------------------------------- canvas --------
CANVAS = "#e8ecef"           # process-graphic plot area (ISA-101 light grey)
CANVAS_PAPER = "#dfe4e8"     # graphic margin
CANVAS_INK = "#37424a"       # drawing line work / labels

CANVAS_DARK = "#141a20"      # optional control-room skin
CANVAS_DARK_PAPER = "#0e1319"
CANVAS_DARK_INK = "#c8d4de"

# ------------------------------------------------- state / condition ---------
# Saturated variants are ALARM-ONLY. The muted variants carry normal state.
OK = "#3f8f5a"
OK_MUTED = "#2f6b46"
CAUTION = "#c99424"          # caution / assumed / degraded
ALARM = "#cf4436"            # trip, hard violation, blocking error
INFO = "#4c86bd"             # derived / selection / planning focus
NEUTRAL = "#7d8a97"          # idle, stopped, TBC
FUTURE = "#8974c4"           # lifecycle, never live state

# Product-family accents kept from the report palette so the preview still
# reads as the same product.
ACCENT = "#e08a2e"           # primary action / hub
NAVY = "#2e6da4"

EVIDENCE = {
    "CONFIRMED": OK,
    "DERIVED": INFO,
    "INFERRED": ACCENT,
    "ASSUMED": CAUTION,
    "TBC": NEUTRAL,
    "OPEN": CAUTION,
}

SEVERITY = {"ALARM": ALARM, "CAUTION": CAUTION, "ADVISORY": INFO,
            "NORMAL": OK}

# --------------------------------------------------------------- metrics -----
FONT = ('"IBM Plex Sans", "Segoe UI", "Roboto", system-ui, '
        "-apple-system, sans-serif")
MONO = ('"IBM Plex Mono", "JetBrains Mono", "Cascadia Mono", '
        'ui-monospace, "Consolas", monospace')

RADIUS = "3px"               # industrial: near-square, no soft cards
GAP = "8px"

#: Graphic heights per density preset (px) — used by the reference drawing,
#: whose geometry has a fixed aspect.
GRAPHIC_HEIGHT = {"Comfortable": 760, "Standard": 660, "Dense": 560}

#: The new flow diagram sizes itself from its CONTENT instead: pixels per
#: layout unit. A row is ~7.6 units, so 4.5 gives a ~34 px row — legible for a
#: pile cross-section — and a four-bay yard lands near 660 px. A three-site
#: end-to-end view is genuinely taller than one screen and scrolls.
GRAPHIC_PPU = {"Comfortable": 4.8, "Standard": 3.9, "Dense": 3.2}

#: Nominal rendered width (px) of the SVG sheet per density preset. The sheet
#: scales to its container; this only sets the iframe height via the sheet's
#: own aspect ratio, because components.html cannot auto-size.
SVG_WIDTH = {"Comfortable": 1255, "Standard": 1140, "Dense": 980}

#: Fixed frame height (px) for the pan/zoom SVG viewer. The sheet zooms inside
#: this frame rather than the frame resizing to the sheet.
VIEWER_H = {"Comfortable": 780, "Standard": 660, "Dense": 520}
