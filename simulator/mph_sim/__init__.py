"""MPH automated stockyard job-allocation & planning simulator (engineering MVP).

Layers:
  domain       - validated dataclass models (planning digital twin)
  validation   - input validation -> issue list
  routes       - directed route-graph model (real network, not ASP free-text routes)
  optimizer    - CP-SAT planning engine (bucketised MILP)
  simulator    - discrete-time closed-loop simulation + event injection
  kpis         - KPI calculation from plan + simulation history
  persistence  - scenario JSON snapshots, CSV/XLSX import-export, SQLite run history
  inventory_snapshot - validated, atomic starting-inventory ingestion
  rabbitmq_inventory - optional read-only SpaceValue connector (uncommissioned)
  adapters     - SMS/ASP adapter interfaces; simulated ASP validation (8 rules)
  datasets     - built-in demonstration scenarios (confirmed values + labelled assumptions)

This MVP runs offline by default on configurable datasets, validated inventory
snapshots and simulated feedback. Optional read-only SMS inventory retrieval is
implemented but uncommissioned; live plan submission/control is not implemented.
"""

__version__ = "0.1.0"
