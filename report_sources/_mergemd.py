from pathlib import Path
import re
import sys

SOURCE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "generated_outputs" / "reports" / "current"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SOURCE_DIR))
from _secref import protect_external_sections, restore_sections  # noqa: E402
from _revision import REV, REV_DATE, as_markdown as revhist_md  # noqa: E402

scope = (SOURCE_DIR / 'MPH-OSBL-SMS-Job-Planning-Automation-Scope.md').read_text(encoding='utf-8')
fd = (SOURCE_DIR / 'MPH-OSBL-SMS-Job-Allocation-Functional-Design.md').read_text(encoding='utf-8')

revhist = revhist_md()

# scope: replace title block, split at Abbreviations.
# Structural, NOT literal: the source header carries whatever revision the
# scope note was last edited at, so matching an exact "Rev.: X / date" string
# silently no-ops and ships a Markdown report with a stale revision (the
# Rev-F/Rev-H split this pass fixes). Match the shape instead.
_HEADER = re.compile(
    r'^\#\ Project\ Scope\ [^\n]*\n'      # H1 title line
    r'(\#\#\#\ [^\n]*)\n'                 # H3 subtitle (kept verbatim)
    r'\n'
    r'\*\*Status:\*\*[^\n]*\n',           # status/rev/date line
    re.M | re.X)
scope, _hdr_n = _HEADER.subn(
    lambda m:
    '# MPH — Automated Job Allocation Layer for the SMS — Project Report\n'
    + m.group(1) + '\n\n'
    + '**Team:** Moncef Driouich  **Status:** Draft — Scope & Functional '
    + f'Design  **Rev.:** {REV}  **Date:** {REV_DATE}\n'
    + revhist + '\n# PART I — PROJECT SCOPE\n',
    scope, count=1)
if _hdr_n != 1:
    raise SystemExit("_mergemd.py: scope title block not recognised — the "
                     "Markdown report would keep a stale revision header")
i = scope.index('## Abbreviations')
smain, stail = scope[:i], scope[i:]

# fd: drop everything up to first "## 1." , renumber, drop footer
fbody = fd[fd.index('## 1. Domain'):]
fbody = re.sub(r'\n---\n\*Companion to the Job‑Allocation Scope note.*$', '', fbody, flags=re.S)
fbody = protect_external_sections(fbody)
for a,b in {'## 1. Domain':'## 14. Domain','## 2. Allocation':'## 15. Allocation',
            '## 3. Evidence':'## 16. Evidence','## 4. Simulation':'## 17. Simulation','## 5. Acceptance':'## 18. Acceptance',
            '### 1.1':'### 14.1','### 1.2':'### 14.2','### 1.3':'### 14.3','### 1.4':'### 14.4',
            '### 2.1':'### 15.1','### 2.2':'### 15.2','### 2.3':'### 15.3','### 2.4':'### 15.4',
            '### 2.5':'### 15.5','### 2.6':'### 15.6','### 2.7':'### 15.7','### 2.8':'### 15.8','### 2.9':'### 15.9',
            '§1.2':'§14.2','§2.3':'§15.3','§2.4':'§15.4','§2.6':'§15.6','§2.7':'§15.7'}.items():
  fbody = fbody.replace(a,b)
fbody = restore_sections(fbody)

out = smain + '# PART II — FUNCTIONAL DESIGN\n\n' + fbody + '\n' + stail
output_path = OUTPUT_DIR / 'MPH-SMS-Job-Allocation-Project-Report.md'
output_path.write_text(out, encoding='utf-8')
print('report md lines:', out.count(chr(10)), '->', output_path)
