"""Regenerate sample_data/ from the built-in demonstration scenarios.

Each scenario is exported as a complete JSON snapshot (deterministic-replay
format) plus per-table CSVs. Run:  python make_sample_data.py
"""
from pathlib import Path

from mph_sim import datasets
from mph_sim.persistence import save_scenario, scenario_tables

ROOT = Path(__file__).parent / "sample_data"

for name, fn in datasets.BUILTIN_SCENARIOS.items():
    sc = fn()
    slug = name.split(" ")[0].lower()          # s1 / s2 / s3
    folder = ROOT / slug
    folder.mkdir(parents=True, exist_ok=True)
    save_scenario(sc, folder / "scenario.json")
    for tname, df in scenario_tables(sc).items():
        df.to_csv(folder / f"{tname}.csv", index=False)
    print(f"{slug}: scenario.json + {len(scenario_tables(sc))} CSV tables -> {folder}")
print("NOTE: values with evidence_status ASSUMED/TBC are placeholders, "
      "not engineering facts (see ASSUMPTIONS.md).")
