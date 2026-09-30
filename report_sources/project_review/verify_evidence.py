"""Check exported paired evidence against its inputs and current source files."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "generated_outputs/project_review"
source = ROOT / "Claude-Integrated-App/simulator/mph_sim"
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob("*.py")}
studies = json.loads((OUT / "experiments/summary.json").read_text(encoding="utf-8"))
expected = {"coordination_outage", "coordination_control", "coordination_capacity_150",
            "project_baseline", "project_low_local_stock", "project_quality_stress"}
assert {s["study"] for s in studies} == expected, "Incomplete saved study set"
rows = []
for study in studies:
    folder = OUT / "experiments" / study["study"]
    sc = json.loads((folder / "scenario.json").read_text(encoding="utf-8"))
    fingerprint = hashlib.sha256(json.dumps(sc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:20]
    comparison = json.loads((folder / "comparison.json").read_text(encoding="utf-8"))
    assert fingerprint == comparison["input_fingerprint"] == study["input_fingerprint"]
    assert comparison["provenance"]["source_sha256"] == hashes == study["provenance"]["source_sha256"]
    for key, arm in study["arms"].items():
        assert arm["plan_status"] in ("FEASIBLE", "OPTIMAL") and not arm["physical_errors"], (study["study"], key)
        assert arm["kpis"] == comparison["arms"][key]["kpis"]
    rows.append({"study": study["study"], "input_fingerprint": fingerprint,
                 "eligible_policy_runs": len(study["arms"]),
                 "comparison_sha256": hashlib.sha256((folder / "comparison.json").read_bytes()).hexdigest()})
result = {"passed": True, "studies": rows, "policy_runs": sum(r["eligible_policy_runs"] for r in rows),
          "qualification": "Input/source integrity and recorded comparison audits; not plant validation"}
(OUT / "evidence_validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(f"PASS: {len(rows)} studies / {result['policy_runs']} eligible policy runs; every input fingerprint and current model source hash matches")
