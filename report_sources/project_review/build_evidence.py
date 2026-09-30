"""Inventory implementation changes and bundle the final review evidence."""
from pathlib import Path
from datetime import datetime, timezone
from importlib.metadata import distributions
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "generated_outputs/project_review"
APP = ROOT / "Claude-Integrated-App"
recovery = json.loads((OUT / "recovery_manifest.json").read_text(encoding="utf-8"))
changes = []
for path in sorted(APP.rglob("*")):
    if not path.is_file() or any(x in path.parts for x in ("__pycache__", ".pytest_cache", ".venv")):
        continue
    if path.suffix not in (".py", ".md", ".json", ".csv", ".txt", ".ps1", ".cmd"):
        continue
    relative = str(path.relative_to(APP))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    original = recovery["sha256"].get(relative)
    if original != digest:
        changes.append({"path": "Claude-Integrated-App/" + path.relative_to(APP).as_posix(),
                        "status": "modified" if original else "added", "sha256": digest,
                        "recovered_sha256": original})
catalog = APP / "simulator/config/grade_catalog.json"
catalog_hash = hashlib.sha256(catalog.read_bytes()).hexdigest()
manifest = {"created_utc": datetime.now(timezone.utc).isoformat(),
            "current_application": str(APP / "simulator"),
            "saved_grade_catalog_preserved": catalog_hash == recovery["sha256"][str(catalog.relative_to(APP))],
            "changes": changes,
            "python_packages": sorted([{ "name": d.metadata["Name"], "version": d.version} for d in distributions()], key=lambda x:x["name"].lower())}
(OUT / "implementation_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
paths = [p for p in OUT.glob("*") if p.is_file() and p.suffix in (".pdf", ".html", ".md", ".json", ".log", ".svg", ".png")]
paths += list((OUT / "experiments").rglob("*.json"))
paths += list((ROOT / "report_sources/project_review").glob("*"))
paths += [APP / "simulator/BUSINESS_CASE_METHOD.md", APP / "simulator/run_business_case.py"]
paths += [APP / "simulator/app.py", APP / "simulator/requirements.txt", catalog]
for folder in ("mph_sim", "ui", "tests"):
    paths += list((APP / "simulator" / folder).glob("*.py"))
paths += [p for p in (APP / "simulator/sample_data").rglob("*") if p.is_file()]
paths += [p for p in (ROOT / "report_sources").glob("*") if p.is_file()]
paths += [p for p in (ROOT / "generated_outputs/reports/current").glob("*") if p.is_file()]
paths += [ROOT / name for name in ("README.md", "Run-Latest-Simulator.ps1", "Stop-Simulator.ps1", "RUN-LATEST-SIMULATOR.cmd", "STOP-SIMULATOR.cmd")]
with zipfile.ZipFile(OUT / "MPH-Review-Evidence.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(set(paths)):
        if p.is_file():
            z.write(p, p.relative_to(ROOT).as_posix())
print(f"Recorded {len(changes)} implementation changes; saved grade catalogue preserved: {manifest['saved_grade_catalog_preserved']}")
print(OUT / "MPH-Review-Evidence.zip")
