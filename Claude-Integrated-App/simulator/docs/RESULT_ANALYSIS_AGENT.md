# Result-analysis tools and future agent integration

The simulator now persists a normalized, immutable evidence bundle for every
new plan run. The bundle supports exact data questions as well as causal
result interpretation. Old rows in `runs.db` retain their summary history but
must be rerun before detailed analysis is available.

## Layers

1. `result_analysis/` is project-neutral. It catalogs and queries scalar or
   indexed measurements, computes variability, compares runs, and assembles
   decision evidence packets.
2. `mph_sim/analysis_tools.py` is the MPH adapter. It maps scenario inputs,
   optimizer decisions, state/output series, constraints, objectives and KPIs
   into the neutral contract. It also loads the verified `model_knowledge/`
   package and implements copied-scenario counterfactuals.
3. `RunStore` saves bundles in `run_artifacts` as compressed JSON while keeping
   the existing `runs` table compatible.
4. `MphAnalysisTools.tool_definitions()` exposes strongly typed function-tool
   schemas. `execute_tool(name, arguments)` is the local dispatcher an agent
   loop or UI can call. No LLM provider is embedded in the simulator.

## Available tools

- `get_model_context`
- `get_variable_definition`
- `get_objective_term_definition`
- `get_constraint_definition`
- `get_kpi_definition`
- `get_code_mapping`
- `list_runs`
- `load_run`
- `catalog_run_data`
- `query_run_data`
- `analyze_variability`
- `get_objective_breakdown`
- `get_candidate_decisions`
- `explain_decision`
- `compare_runs`
- `run_counterfactual`

Every model project should carry a canonical package containing
`model_knowledge/MODEL_FORMULATION.md`, `model_manifest.json`, and
`CODE_MAP.md`. New MPH run bundles embed the complete package used at run time,
including its implementation fingerprint. Definition tools accept an optional
`run_id`; use it so a historical result is interpreted with historical model
semantics rather than whichever formulation happens to be current today.

Maintain the MPH manifest hashes with:

```powershell
python scripts/sync_model_manifest.py
python scripts/sync_model_manifest.py --check
```

Always call `catalog_run_data` before querying an unfamiliar project. Use the
returned exact measurement ID, unit, basis and source path. A measurement can
belong to the `input`, `decision`, `state`, `output`, `kpi`, or `diagnostic`
namespace and may be scalar or time-indexed.

Example local use:

```python
from mph_sim.analysis_tools import MphAnalysisTools

tools = MphAnalysisTools("runs.db")
catalog = tools.catalog_run_data(12, name_contains="inventory")
result = tools.analyze_variability(
    12, measurement_id="state:inventory:space:HP-01")
```

Counterfactual changes use JSON-pointer-like scenario paths and run on a
reconstructed copy. They do not mutate the baseline. Examples:

```text
/weights/w1_unmet
/config/WASHING_TREATMENTS/modes/simple/available
/machines/RC01/rate_max_tph
/links/C1/capacity_tph
/demand/0/rate_tph
```

Persistence is off by default for counterfactuals. A multi-change run is
labelled non-isolated and must not be used to attribute a result to one cause.

## Adapting another project, including APC

Keep `result_analysis/` unchanged and write a domain adapter that emits the
same bundle. For APC, typical measurements are:

| Namespace | Examples |
|---|---|
| `input` | controller tuning, limits, sample time, operating mode |
| `decision` | optimizer move or control action |
| `state` | controlled variables, manipulated variables, disturbances |
| `output` | predictions, constraint violations, actuator commands |
| `kpi` | IAE/ISE, variance, settling time, overshoot, saturation time |
| `diagnostic` | estimator residuals, solver status, model mismatch |

The adapter must retain engineering units, sample time, timestamps, data
quality, operating-mode intervals and missing values. It should implement
counterfactual execution separately because an APC replay/simulation has
different safety boundaries from a stockyard planning solve.

Before enabling interpretation in another project, create its model-knowledge
package with the global `model-formulation` skill. APC packages should include
state-space or step-response equations, CV/MV/DV definitions, estimator logic,
move suppression, constraint priorities, operating modes, prediction and
control horizons, scaling, and KPI evaluation windows.

## Evidence limits

Generated candidates prove eligibility after candidate-generation filters;
they do not prove that forcing an unused alternative is hard-feasible. The
bundle therefore leaves `hard_feasible` null until a forced-alternative test is
run. Constraint rows are independently recomputed reportable residuals, not a
complete export of every internal CP-SAT constraint.
