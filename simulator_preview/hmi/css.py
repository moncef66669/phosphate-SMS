"""Console stylesheet.

Self-contained: this module never imports ``ui.theme``, so the production
palette/CSS is not loaded and cannot fight these rules. The Streamlit widget
base colours come from ``simulator_preview/.streamlit/config.toml``; this
sheet supplies the console structure, density and micro-typography.
"""
from __future__ import annotations

import streamlit as st

from . import tokens as t

_CSS = f"""
<style>
  /* ============================ shell ==================================== */
  header[data-testid="stHeader"] {{ display: none; }}
  [data-testid="stToolbar"] {{ display: none; }}
  [data-testid="stDecoration"] {{ display: none; }}
  footer {{ display: none; }}
  #MainMenu {{ visibility: hidden; }}

  .stApp {{ background: {t.INK_900}; }}
  html, body, .stApp, [class*="css"] {{ font-family: {t.FONT}; }}

  .block-container {{
     max-width: 100% !important;
     padding: 6px 14px 10px 14px !important; }}
  div[data-testid="stVerticalBlock"] {{ gap: 6px; }}
  div[data-testid="stHorizontalBlock"] {{ gap: 8px; }}
  hr {{ margin: 4px 0; border-color: {t.INK_500}; }}

  /* ============================ title bar =============================== */
  .cx-title {{
     display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
     background: {t.INK_800}; border: 1px solid {t.INK_500};
     border-bottom: 2px solid {t.NAVY};
     border-radius: {t.RADIUS}; padding: 7px 12px; }}
  .cx-mark {{ display: flex; flex-direction: column; line-height: 1.15; }}
  .cx-mark b {{ color: {t.TXT_100}; font-size: .93rem; font-weight: 700;
     letter-spacing: .01em; }}
  .cx-mark span {{ color: {t.TXT_500}; font-size: .66rem;
     text-transform: uppercase; letter-spacing: .09em; }}
  .cx-title-sep {{ width: 1px; height: 26px; background: {t.INK_400}; }}
  .cx-title-right {{ margin-left: auto; display: flex; align-items: center;
     gap: 10px; }}
  .cx-clock {{ font-family: {t.MONO}; color: {t.TXT_300}; font-size: .74rem;
     text-align: right; line-height: 1.3; }}
  .cx-clock b {{ color: {t.TXT_100}; font-size: .82rem; }}

  /* ============================ chips =================================== */
  .cx-chip {{ display: inline-flex; align-items: center; gap: 5px;
     border: 1px solid {t.INK_400}; border-radius: 2px;
     background: {t.INK_600}; color: {t.TXT_300};
     padding: 2px 7px; font-size: .7rem; white-space: nowrap; }}
  .cx-chip b {{ color: {t.TXT_100}; font-weight: 650; }}
  .cx-chip i {{ font-style: normal; font-size: .78rem; line-height: 1; }}
  .cx-chip.lead {{ border-left: 3px solid {t.ACCENT}; }}
  .cx-chip.ok    {{ border-color: {t.OK_MUTED};  color: #bfe3cb; }}
  .cx-chip.caut  {{ border-color: #8a6a1c;       color: #f0dcae; }}
  .cx-chip.alarm {{ border-color: #8f3428; color: #f6cdc6;
                    background: #2a1917; }}
  .cx-chip.info  {{ border-color: #35617f;       color: #cbe0f0; }}
  .cx-chip.mute  {{ color: {t.TXT_500}; }}
  .cx-chip.mono b {{ font-family: {t.MONO}; }}

  /* ======================= condition ribbon ============================= */
  .cx-ribbon {{ display: flex; align-items: stretch; gap: 0;
     border: 1px solid {t.INK_500}; border-radius: {t.RADIUS};
     background: {t.INK_700}; overflow: hidden; }}
  .cx-ribbon-cell {{ display: flex; align-items: center; gap: 8px;
     padding: 6px 12px; border-right: 1px solid {t.INK_500};
     min-width: 0; }}
  .cx-ribbon-cell:last-child {{ border-right: 0; margin-left: auto; }}
  .cx-ribbon-lamp {{ width: 10px; height: 10px; border-radius: 50%;
     flex: 0 0 10px; box-shadow: 0 0 0 2px rgba(0,0,0,.35) inset; }}
  .cx-ribbon-n {{ font-family: {t.MONO}; font-size: 1.0rem; font-weight: 700;
     color: {t.TXT_100}; line-height: 1; }}
  .cx-ribbon-l {{ font-size: .64rem; text-transform: uppercase;
     letter-spacing: .09em; color: {t.TXT_500}; line-height: 1.25; }}
  .cx-ribbon-cell.hot {{ background: #2a1a17; }}
  .cx-ribbon-cell.warm {{ background: #26210f; }}
  .cx-ribbon-msg {{ color: {t.TXT_300}; font-size: .74rem;
     overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}

  /* ============================ KPI strip =============================== */
  /* auto-fit with a MAX track: a scenario with few tiles must not stretch
     three cards across 1900 px. */
  .cx-kpis {{ display: grid; gap: 6px; justify-content: start;
     grid-template-columns: repeat(auto-fit, minmax(140px, 200px)); }}
  .cx-kpi {{ background: {t.INK_700}; border: 1px solid {t.INK_500};
     border-top: 2px solid {t.INK_400};
     border-radius: {t.RADIUS}; padding: 6px 10px 7px; min-width: 0; }}
  .cx-kpi.ok {{ border-top-color: {t.OK}; }}
  .cx-kpi.caut {{ border-top-color: {t.CAUTION}; }}
  .cx-kpi.alarm {{ border-top-color: {t.ALARM}; }}
  .cx-kpi.info {{ border-top-color: {t.INFO}; }}
  .cx-kpi-l {{ color: {t.TXT_500}; font-size: .63rem; font-weight: 650;
     text-transform: uppercase; letter-spacing: .085em;
     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .cx-kpi-v {{ color: {t.TXT_100}; font-family: {t.MONO};
     font-size: 1.22rem; font-weight: 600; line-height: 1.15;
     margin-top: 1px; }}
  .cx-kpi-v small {{ font-size: .66rem; color: {t.TXT_500};
     font-family: {t.FONT}; margin-left: 3px; }}
  .cx-kpi-s {{ color: {t.TXT_500}; font-size: .66rem; margin-top: 1px;
     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .cx-bar {{ height: 3px; background: {t.INK_500}; border-radius: 2px;
     margin-top: 5px; overflow: hidden; }}
  .cx-bar span {{ display: block; height: 100%; background: {t.INFO}; }}
  .cx-bar.ok span {{ background: {t.OK}; }}
  .cx-bar.caut span {{ background: {t.CAUTION}; }}
  .cx-bar.alarm span {{ background: {t.ALARM}; }}

  /* ============================ panels ================================== */
  .cx-panel-h {{ display: flex; align-items: baseline; gap: 8px;
     background: {t.INK_600}; border: 1px solid {t.INK_500};
     border-bottom: 0;
     border-radius: {t.RADIUS} {t.RADIUS} 0 0; padding: 5px 9px 6px; }}
  .cx-panel-h b {{ color: {t.TXT_100}; font-size: .7rem; font-weight: 700;
     text-transform: uppercase; letter-spacing: .1em; line-height: 1.4; }}
  /* line-height 1.4 + the extra bottom padding keep descenders (p, g, y) off
     the panel border — they were being clipped at 4px/1.0. */
  .cx-panel-h span {{ margin-left: auto; color: {t.TXT_500};
     font-size: .66rem; font-family: {t.MONO}; line-height: 1.4;
     white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}

  /* key/value inspector table */
  .cx-kv {{ width: 100%; border-collapse: collapse;
     background: {t.INK_700}; border: 1px solid {t.INK_500};
     border-radius: 0 0 {t.RADIUS} {t.RADIUS}; }}
  .cx-kv tr + tr {{ border-top: 1px solid {t.INK_600}; }}
  .cx-kv td {{ padding: 3px 9px; font-size: .73rem; vertical-align: top; }}
  .cx-kv td.k {{ color: {t.TXT_500}; width: 42%; white-space: nowrap;
     font-size: .68rem; text-transform: uppercase; letter-spacing: .05em; }}
  .cx-kv td.v {{ color: {t.TXT_100}; font-family: {t.MONO};
     overflow-wrap: anywhere; }}
  .cx-kv td.v.txt {{ font-family: {t.FONT}; }}
  .cx-kv tr.sev-alarm td.v {{ color: #f6cdc6; }}
  .cx-kv tr.sev-caut td.v {{ color: #f0dcae; }}
  .cx-kv tr.sev-ok td.v {{ color: #bfe3cb; }}

  /* alarm / condition list */
  .cx-list {{ border: 1px solid {t.INK_500}; border-top: 0;
     border-radius: 0 0 {t.RADIUS} {t.RADIUS}; overflow: hidden; }}
  .cx-row {{ display: flex; align-items: baseline; gap: 8px;
     padding: 5px 9px; background: {t.INK_700}; font-size: .73rem;
     color: {t.TXT_300}; border-left: 3px solid {t.NEUTRAL}; }}
  .cx-row + .cx-row {{ border-top: 1px solid {t.INK_600}; }}
  .cx-row.ALARM {{ border-left-color: {t.ALARM}; background: #241715; }}
  .cx-row.CAUTION {{ border-left-color: {t.CAUTION}; background: #211d10; }}
  .cx-row.ADVISORY {{ border-left-color: {t.INFO}; }}
  .cx-row-tag {{ font-family: {t.MONO}; color: {t.TXT_100};
     font-size: .71rem; flex: 0 0 auto; }}
  .cx-row-txt {{ flex: 1 1 auto; min-width: 0; }}
  .cx-row-sev {{ flex: 0 0 auto; font-size: .62rem; letter-spacing: .08em;
     text-transform: uppercase; color: {t.TXT_500}; }}
  .cx-empty {{ padding: 10px; background: {t.INK_700}; color: {t.TXT_500};
     font-size: .74rem; text-align: center; }}

  /* ============================ graphic frame =========================== */
  .cx-frame-h {{ display: flex; align-items: center; gap: 10px;
     background: {t.INK_600}; border: 1px solid {t.INK_500};
     border-bottom: 0; border-radius: {t.RADIUS} {t.RADIUS} 0 0;
     padding: 4px 10px; flex-wrap: wrap; }}
  .cx-frame-h b {{ color: {t.TXT_100}; font-size: .72rem; font-weight: 700;
     text-transform: uppercase; letter-spacing: .1em; line-height: 1.5; }}
  .cx-frame-h .sp {{ margin-left: auto; }}
  .st-key-cx_graphic {{ border: 1px solid {t.INK_500} !important;
     border-radius: 0 0 {t.RADIUS} {t.RADIUS} !important;
     background: {t.CANVAS_PAPER} !important; padding: 0 !important; }}
  .st-key-cx_graphic_dark {{ border: 1px solid {t.INK_500} !important;
     border-radius: 0 0 {t.RADIUS} {t.RADIUS} !important;
     background: {t.CANVAS_DARK_PAPER} !important; padding: 0 !important; }}

  /* ============================ footer ================================== */
  .cx-foot {{ display: flex; gap: 8px 18px; flex-wrap: wrap;
     border: 1px solid {t.INK_500}; border-radius: {t.RADIUS};
     background: {t.INK_800}; padding: 6px 11px;
     color: {t.TXT_500}; font-size: .68rem; line-height: 1.45; }}
  .cx-foot b {{ color: {t.TXT_300}; font-weight: 650; }}
  .cx-foot code {{ font-family: {t.MONO}; color: {t.TXT_300};
     background: {t.INK_600}; padding: 0 4px; border-radius: 2px; }}
  .cx-legend {{ display: flex; flex-wrap: wrap; gap: 5px 8px;
     align-items: center; }}
  .cx-note {{ color: {t.TXT_500}; font-size: .69rem; line-height: 1.5;
     margin: 3px 0 6px; }}
  .cx-note b {{ color: {t.TXT_300}; }}
  .cx-note .cx-legend {{ margin-bottom: 2px; }}

  /* ======================= streamlit widget skin ======================== */
  /* micro-labels: uppercase, tight, so 8 controls fit one operator rail */
  .stApp label p, .stApp label span {{ font-size: .68rem !important; }}
  div[data-testid="stWidgetLabel"] p {{
     color: {t.TXT_500} !important; font-size: .64rem !important;
     font-weight: 650 !important; text-transform: uppercase;
     letter-spacing: .085em; margin-bottom: 2px !important; }}
  div[data-testid="stCaptionContainer"] p {{
     color: {t.TXT_500} !important; font-size: .68rem !important; }}

  /* segmented controls / pills -> industrial button banks */
  div[data-testid="stButtonGroup"] button {{
     background: {t.INK_600} !important; color: {t.TXT_300} !important;
     border: 1px solid {t.INK_400} !important; border-radius: 2px !important;
     font-size: .7rem !important; padding: 2px 9px !important;
     min-height: 26px !important; font-weight: 600 !important; }}
  div[data-testid="stButtonGroup"] button[aria-checked="true"],
  div[data-testid="stButtonGroup"] button[aria-pressed="true"] {{
     background: {t.NAVY} !important; color: #fff !important;
     border-color: #4a86bd !important; }}

  /* buttons */
  .stButton button, .stDownloadButton button {{
     background: {t.INK_600}; color: {t.TXT_100};
     border: 1px solid {t.INK_400}; border-radius: 2px;
     font-size: .71rem; font-weight: 600; min-height: 28px;
     padding: 2px 10px; }}
  .stButton button:hover, .stDownloadButton button:hover {{
     border-color: {t.NAVY}; color: #fff; }}
  .stButton button[kind="primary"] {{
     background: {t.ACCENT}; border-color: #b96f18; color: #22150a; }}
  .stButton button[kind="primary"]:hover {{ background: #f09a3c;
     color: #22150a; }}

  /* inputs */
  div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {{
     background: {t.INK_600} !important; border-color: {t.INK_400} !important;
     border-radius: 2px !important; min-height: 28px !important;
     font-size: .72rem !important; }}
  div[data-baseweb="select"] span, div[data-baseweb="tag"] {{
     font-size: .7rem !important; }}
  div[data-baseweb="tag"] {{ background: {t.NAVY} !important;
     border-radius: 2px !important; }}

  /* sliders */
  div[data-testid="stSlider"] {{ padding-top: 2px; }}
  div[data-testid="stSlider"] div[data-baseweb="slider"] div[role="slider"] {{
     background: {t.ACCENT} !important; }}
  div[data-testid="stSliderTickBarMin"],
  div[data-testid="stSliderTickBarMax"],
  div[data-testid="stThumbValue"] {{
     font-size: .62rem !important; color: {t.TXT_500} !important; }}

  /* expanders as flush console drawers */
  details[data-testid="stExpander"] {{
     border: 1px solid {t.INK_500} !important; border-radius: {t.RADIUS};
     background: {t.INK_700}; }}
  details[data-testid="stExpander"] summary {{
     background: {t.INK_600}; padding: 4px 10px; }}
  details[data-testid="stExpander"] summary p {{
     font-size: .69rem !important; font-weight: 650;
     text-transform: uppercase; letter-spacing: .08em;
     color: {t.TXT_300} !important; }}

  /* tabs */
  button[data-baseweb="tab"] {{ padding: 3px 10px !important; }}
  button[data-baseweb="tab"] p {{ font-size: .69rem !important;
     font-weight: 650; text-transform: uppercase; letter-spacing: .06em; }}
  div[data-baseweb="tab-highlight"] {{ background: {t.ACCENT} !important; }}
  div[data-baseweb="tab-border"] {{ background: {t.INK_500} !important; }}

  /* alerts: honest severity, no pastel */
  div[data-testid="stAlert"] {{ border-radius: 2px; font-size: .74rem;
     padding: 7px 11px; }}

  /* dataframes */
  div[data-testid="stDataFrame"] {{ border: 1px solid {t.INK_500};
     border-radius: 2px; }}

  /* keep Plotly modebar unobtrusive over the light canvas */
  .modebar-container {{ opacity: .35; }}
  .modebar-container:hover {{ opacity: 1; }}

  /* status widget ("running…") must not cover the title bar */
  [data-testid="stStatusWidget"] {{ bottom: 8px; top: auto !important;
     right: 8px; }}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
