from pathlib import Path
import re
import sys

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "generated_outputs" / "reports" / "current"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SOURCE_DIR))
from _secref import protect_external_sections, restore_sections  # noqa: E402
from _revision import REV, REV_DATE, as_html as revhist_html  # noqa: E402

scope = (SOURCE_DIR / '_scope.html').read_text(encoding='utf-8')
fd = (SOURCE_DIR / '_fd.html').read_text(encoding='utf-8')

# --- head: take scope head, set report <title>, append FD-specific CSS before </style> ---
head = scope[:scope.index('</head>')]
head = head.replace(
  '<title>Project Scope — MPH OSBL Automated Job Allocation Layer</title>',
  '<title>MPH — Automated Job Allocation Layer for the SMS — Project Report</title>')
css_add = """
  h4 { color:#2e6da4; font-size:9.8pt; margin:10px 0 3px; }
  code { font-family:"Consolas",monospace; font-size:8.2pt; background:#f1f4f7; padding:0 3px; border-radius:3px; color:#14365c; }
  .obj { background:#eef3f8; border:1px solid #cdd6df; border-radius:5px; padding:7px 10px; font-size:9pt; margin:5px 0; }
  .fdnote { font-size:8.6pt; color:#555; }
  tr.hl td { background:#fdf6ea; font-weight:600; }
  .erd { break-inside:avoid; border:1px solid #cdd6df; border-radius:6px; padding:10px; margin:6px 0; background:#fcfdfe; }
  .erow { display:flex; align-items:center; gap:5px; flex-wrap:wrap; margin:4px 0; }
  .ebox { border:1.5px solid #5b7fa6; background:#eef3f8; border-radius:5px; padding:3px 9px; font-size:8.3pt; font-weight:700; color:#14365c; }
  .ebox.key { border-color:#d98b2b; background:#fdf6ea; color:#b5651d; }
  .ebox.job { border-color:#2e6d4f; background:#eef6f1; color:#235a40; }
  .ebox small { font-weight:400; color:#777; }
  .earr { font-size:7.6pt; color:#7a8a99; font-weight:700; white-space:nowrap; }
  .edown { font-size:7.9pt; color:#7a8a99; font-weight:700; }
  .partbanner { background:#1f4e79; color:#fff; padding:6px 10px; border:none; border-radius:4px; margin:16px 0 6px; font-size:12pt; font-weight:700; }
  .eq { break-inside:avoid; font-family:"Consolas",monospace; font-size:8.3pt; background:#f6f8fa; border:1px solid #dbe3ea; border-radius:5px; padding:6px 10px; margin:4px 0; white-space:pre; overflow-x:auto; color:#14365c; line-height:1.45; }
  .eqcap { font-size:8.4pt; color:#555; margin:1px 0 7px; }
  table.reg { font-size:7.3pt; }
  table.reg td { padding:2px 4px; }
  table.reg th { padding:2px 4px; font-size:7.3pt; }
  thead { display:table-header-group; }
"""
head = head.replace('</style>', css_add + '</style>')

# --- scope body ---
sbody = scope[scope.index('<body>')+6 : scope.index('</body>')]
# new title / sub / meta (+ document metadata & revision history)
sbody = sbody.replace(
  '<h1>Project Scope — Automated Job Allocation Layer for the Stockpile Management System (SMS)</h1>',
  '<h1>MPH — Automated Job Allocation Layer for the SMS — Project Report</h1>')
sbody = sbody.replace(
  '<h2 class="sub">MPH OSBL Solid Handling (Phosphate &amp; Solid Sulphur) — SP2M Program</h2>',
  '<h2 class="sub">Scope &amp; Functional Design · Phosphate value chain (Louta · Benguerir · MPH) + Sulphur · SP2M Program</h2>')
revhist = revhist_html()
sbody, _meta_n = re.subn(r'<div class="meta">.*?</div>',
  '<div class="meta"><strong>Team:</strong> Moncef Driouich &nbsp;|&nbsp; <strong>Status:</strong> Draft — Scope &amp; Functional Design &nbsp;|&nbsp; '
  f'<strong>Rev.:</strong> {REV} &nbsp;|&nbsp; <strong>Date:</strong> {REV_DATE}</div>'
  + revhist + '\n<div class="partbanner">PART I — PROJECT SCOPE</div>',
  sbody, count=1, flags=re.S)
if _meta_n != 1:
    raise SystemExit("_merge.py: scope <div class=\"meta\"> header block not "
                     "found — the report would keep a stale revision")
# split at Abbreviations
idx = sbody.index('<h3>Abbreviations</h3>')
scope_main, scope_tail = sbody[:idx], sbody[idx:]

# --- FD body: drop title block + footref, renumber ---
fbody = fd[fd.index('<body>')+6 : fd.index('</body>')]
fbody = re.sub(r'^.*?<div class="meta">.*?</div>', '', fbody, count=1, flags=re.S)
fbody = re.sub(r'<div class="footref">.*?</div>', '', fbody, flags=re.S)
ren = {'<h3>1. Domain':'<h3>14. Domain','<h3>2. Allocation':'<h3>15. Allocation',
       '<h3>3. Evidence':'<h3>16. Evidence','<h3>4. Simulation':'<h3>17. Simulation','<h3>5. Acceptance':'<h3>18. Acceptance',
       '<h4>1.1':'<h4>14.1','<h4>1.2':'<h4>14.2','<h4>1.3':'<h4>14.3','<h4>1.4':'<h4>14.4',
       '<h4>2.1':'<h4>15.1','<h4>2.2':'<h4>15.2','<h4>2.3':'<h4>15.3','<h4>2.4':'<h4>15.4',
       '<h4>2.5':'<h4>15.5','<h4>2.6':'<h4>15.6','<h4>2.7':'<h4>15.7','<h4>2.8':'<h4>15.8','<h4>2.9':'<h4>15.9',
       '§1.2':'§14.2','§2.3':'§15.3','§2.4':'§15.4','§2.6':'§15.6','§2.7':'§15.7'}
# protect EXTERNAL document-citation section refs from FD-internal renumbering
fbody = protect_external_sections(fbody)
for a,b in ren.items(): fbody = fbody.replace(a,b)
fbody = restore_sections(fbody)
fbody = fbody.replace('class="note"','class="fdnote"')
part2 = '<div class="partbanner">PART II — FUNCTIONAL DESIGN &nbsp;<span style="font-weight:400;font-size:9pt;">(Domain Model · Allocation Decision Model · Engineering Basis)</span></div>\n'

out = head + '</head>\n<body>\n' + scope_main + part2 + fbody + scope_tail + '\n</body>\n</html>'
output_path = OUTPUT_DIR / '_report.html'
output_path.write_text(out, encoding='utf-8')
print("report html chars:", len(out), "->", output_path)
