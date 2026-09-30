"""Build the two current project reports and optionally print both PDFs.

The scope report remains sourced from the maintained scope document.  The
formulation report is sourced from the verified model_knowledge package so an
obsolete narrative cannot silently override the implemented optimizer.
"""
from __future__ import annotations

import argparse
import html
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


SOURCE_DIR = Path(__file__).resolve().parent
ROOT = SOURCE_DIR.parent
OUTPUT = ROOT / "generated_outputs" / "reports" / "current"
MODEL_ROOT = ROOT / "Claude-Integrated-App" / "simulator"
FORMULATION = MODEL_ROOT / "model_knowledge" / "MODEL_FORMULATION.md"
SCOPE_MD = SOURCE_DIR / "MPH-OSBL-SMS-Job-Planning-Automation-Scope.md"
SCOPE_HTML = SOURCE_DIR / "_scope.html"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

SCOPE_STEM = "MPH-SMS-Job-Allocation-Scope-Objectives-and-Functionalities"
FORMULATION_STEM = "MPH-SMS-Job-Allocation-Model-Formulation-and-Optimization"
EDITION_DATE = "17 September 2026"


def _inline(text: str) -> str:
    code: list[str] = []

    def hold(match: re.Match[str]) -> str:
        code.append("<code>" + html.escape(match.group(1)) + "</code>")
        return f"\x00CODE{len(code) - 1}\x00"

    value = re.sub(r"`([^`]+)`", hold, text)
    value = html.escape(value, quote=False)
    value = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)
    for index, fragment in enumerate(code):
        value = value.replace(f"\x00CODE{index}\x00", fragment)
    return value


def _list_block(lines: list[str], index: int, ordered: bool) -> tuple[str, int]:
    """Render one list while retaining indented Markdown continuation lines."""
    marker = r"^\s*\d+\.\s+" if ordered else r"^\s*[-*]\s+"
    items: list[str] = []
    while index < len(lines) and re.match(marker, lines[index]):
        item = re.sub(marker, "", lines[index]).strip()
        index += 1
        continuation: list[str] = []
        while index < len(lines) and lines[index].strip():
            candidate = lines[index]
            if re.match(marker, candidate):
                break
            if not re.match(r"^\s{2,}\S", candidate):
                break
            continuation.append(candidate.strip())
            index += 1
        if continuation:
            item += " " + " ".join(continuation)
        items.append(item)
    tag = "ol" if ordered else "ul"
    return (
        f"<{tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items)
        + f"</{tag}>",
        index,
    )


def markdown_to_html(markdown: str) -> str:
    """Render the maintained report subset of CommonMark without dependencies."""
    lines = markdown.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            language = line[3:].strip()
            index += 1
            block = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            index += int(index < len(lines))
            klass = f' class="language-{html.escape(language)}"' if language else ""
            out.append(f"<pre><code{klass}>{html.escape(chr(10).join(block))}</code></pre>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if ("|" in line and index + 1 < len(lines)
                and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])):
            headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
            index += 2
            rows = []
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append([cell.strip() for cell in lines[index].strip().strip("|").split("|")])
                index += 1
            out.append("<table><thead><tr>" + "".join(
                f"<th>{_inline(cell)}</th>" for cell in headers) + "</tr></thead><tbody>")
            for row in rows:
                padded = row + [""] * (len(headers) - len(row))
                out.append("<tr>" + "".join(
                    f"<td>{_inline(cell)}</td>" for cell in padded[:len(headers)]) + "</tr>")
            out.append("</tbody></table>")
            continue
        if re.match(r"^\s*[-*]\s+", line):
            block, index = _list_block(lines, index, ordered=False)
            out.append(block)
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            block, index = _list_block(lines, index, ordered=True)
            out.append(block)
            continue
        if line.startswith(">"):
            quote = []
            while index < len(lines) and lines[index].startswith(">"):
                quote.append(lines[index].lstrip("> "))
                index += 1
            out.append(f"<blockquote>{_inline(' '.join(quote))}</blockquote>")
            continue
        if not line.strip() or line.strip() == "---":
            index += 1
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index]
            if (candidate.startswith(("#", "```", ">"))
                    or re.match(r"^\s*([-*]|\d+\.)\s+", candidate)
                    or ("|" in candidate and index + 1 < len(lines)
                        and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1]))):
                break
            paragraph.append(candidate.strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


def _head(title: str) -> str:
    source = SCOPE_HTML.read_text(encoding="utf-8")
    head = source[:source.index("</head>")]
    head = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", head)
    extra = """
  pre { white-space:pre-wrap; break-inside:avoid; font-family:Consolas,monospace;
        font-size:7.6pt; line-height:1.35; background:#f6f8fa;
        border:1px solid #dbe3ea; border-radius:5px; padding:7px 9px; }
  h2 { color:#1f4e79; font-size:11.5pt; margin:12px 0 4px;
       border-bottom:1px solid #d6e0ea; padding-bottom:2px; break-after:avoid; }
  h5, h6 { color:#2e6da4; margin:7px 0 2px; break-after:avoid; }
  ol { margin:3px 0 5px; padding-left:19px; }
  a { color:#1f4e79; text-decoration:none; }
  .edition { background:#eef3f8; border-left:4px solid #1f4e79;
             padding:6px 9px; margin:7px 0 10px; font-size:8.3pt; }
"""
    return head.replace("</style>", extra + "</style>") + "</head>"


def build_scope() -> tuple[Path, Path]:
    raw_md = SCOPE_MD.read_text(encoding="utf-8")
    raw_md = re.sub(
        r"^# Project Scope[^\n]*\n### ([^\n]+)\n\n\*\*Status:\*\*[^\n]+\n",
        "# MPH — Job Allocation Scope, Objectives & Functionalities\n"
        r"### \1\n\n"
        f"**Edition:** Split report · **Date:** {EDITION_DATE} · "
        "**Content baseline:** maintained project scope\n",
        raw_md, count=1, flags=re.MULTILINE)
    md_path = OUTPUT / f"{SCOPE_STEM}.md"
    md_path.write_text(raw_md, encoding="utf-8")

    source = SCOPE_HTML.read_text(encoding="utf-8")
    body = source[source.index("<body>") + 6:source.index("</body>")]
    body = re.sub(r"<h1>.*?</h1>",
                  "<h1>MPH — Job Allocation Scope, Objectives &amp; Functionalities</h1>",
                  body, count=1, flags=re.DOTALL)
    body = re.sub(
        r'<div class="meta">.*?</div>',
        '<div class="meta"><strong>Edition:</strong> Split report &nbsp;|&nbsp; '
        f'<strong>Date:</strong> {EDITION_DATE} &nbsp;|&nbsp; '
        '<strong>Source:</strong> Maintained project scope</div>',
        body, count=1, flags=re.DOTALL)
    html_path = OUTPUT / f"_{SCOPE_STEM}.html"
    html_path.write_text(
        "<!DOCTYPE html><html lang=\"en\">" + _head(
            "MPH — Scope, Objectives & Functionalities")
        + "<body>" + body + "</body></html>", encoding="utf-8")
    return md_path, html_path


def build_formulation() -> tuple[Path, Path]:
    source = FORMULATION.read_text(encoding="utf-8")
    title = "MPH — Model Formulation & Optimization Algorithms"
    preface = (
        f"# {title}\n"
        "### Phosphate Value-Chain Job-Allocation Simulator\n\n"
        f"**Edition:** Code-verified split report · **Date:** {EDITION_DATE} · "
        "**Canonical source:** `model_knowledge/MODEL_FORMULATION.md`\n\n"
        "> This report is generated from the versioned model-knowledge package. "
        "Run-specific configuration and the model-context snapshot remain "
        "authoritative for interpreting an individual solve.\n\n"
    )
    source = re.sub(r"^# MPH stockyard planning model formulation\s*", "", source, count=1)
    report_md = preface + source
    md_path = OUTPUT / f"{FORMULATION_STEM}.md"
    md_path.write_text(report_md, encoding="utf-8")
    body = markdown_to_html(report_md)
    html_path = OUTPUT / f"_{FORMULATION_STEM}.html"
    html_path.write_text(
        "<!DOCTYPE html><html lang=\"en\">" + _head(title)
        + "<body>" + body + "</body></html>", encoding="utf-8")
    return md_path, html_path


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    if not CHROME.is_file():
        raise SystemExit(f"Chrome not found at {CHROME}")
    with tempfile.TemporaryDirectory(prefix="mph-report-") as folder:
        temporary = Path(folder) / pdf_path.name
        command = [
            str(CHROME), "--headless=new", "--disable-gpu",
            "--no-pdf-header-footer", f"--print-to-pdf={temporary}",
            html_path.resolve().as_uri(),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0 or not temporary.is_file():
            raise SystemExit(
                f"Chrome PDF render failed ({completed.returncode}): "
                f"{completed.stderr or completed.stdout}")
        shutil.copy2(temporary, pdf_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", action="store_true", help="also print both PDFs with Chrome")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    scope_md, scope_html = build_scope()
    formulation_md, formulation_html = build_formulation()
    if args.pdf:
        print_pdf(scope_html, OUTPUT / f"{SCOPE_STEM}.pdf")
        print_pdf(formulation_html, OUTPUT / f"{FORMULATION_STEM}.pdf")
    for path in (scope_md, scope_html, formulation_md, formulation_html):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
