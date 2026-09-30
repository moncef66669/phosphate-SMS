"""Persistence & scenario management — layer 7.

- Scenario <-> JSON (complete snapshot incl. seed -> deterministic replay)
- entity tables <-> pandas DataFrames (CSV / XLSX / JSON import-export)
- SQLite run history (runs.db next to the app)
"""
from __future__ import annotations

import dataclasses
import copy
from contextlib import contextmanager
import io
import json
import math
import sqlite3
import datetime as dtm
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from . import domain as dm
from .domain import Scenario
from .grade_ids import canonicalize_scenario_grade_ids

_LIST_FIELDS = {
    "quality_params": dm.QualityParam, "sites": dm.Site, "grades": dm.Grade,
    "material_profiles": dm.MaterialProfile,
    "spaces": dm.StockSpace, "segments": dm.Segment,
    "pile_zones": dm.PileZone, "machines": dm.Machine,
    "nodes": dm.Node, "links": dm.Link, "destinations": dm.Destination,
    "demand": dm.DemandEntry, "calendars": dm.CalendarEntry,
    "exogenous": dm.ExogenousFlow,
    "process_transfers": dm.ProcessTransfer,
    "belt_profile": dm.BeltProfileEntry,
    "fixed_jobs": dm.FixedJob, "assumptions": dm.Assumption,
}
_NESTED = {"evidence": dm.Evidence, "spec": dm.SpecRange}


def _json_compatible(value):
    """Normalize non-finite and container values without inventing numbers."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_compatible(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _build(cls, data: dict):
    kwargs = {}
    fields = {f.name: f for f in dataclasses.fields(cls)}
    for k, v in data.items():
        if k not in fields:
            continue
        if k == "evidence" and isinstance(v, dict):
            kwargs[k] = dm.Evidence(**{kk: vv for kk, vv in v.items()
                                       if kk in {f.name for f in dataclasses.fields(dm.Evidence)}})
        elif k in ("spec", "receiving_spec") and isinstance(v, list):
            kwargs[k] = [dm.SpecRange(**s) for s in v]
        else:
            kwargs[k] = v
    return cls(**kwargs)


def scenario_to_json(sc: Scenario) -> str:
    canonical = copy.deepcopy(sc)
    canonicalize_scenario_grade_ids(canonical)
    return json.dumps(dataclasses.asdict(canonical), indent=1)


def scenario_from_json(text: str) -> Scenario:
    d = json.loads(text)
    kwargs: Dict[str, object] = {"name": d.get("name", "unnamed"),
                                 "description": d.get("description", ""),
                                 "notes": d.get("notes", "")}
    for fname, cls in _LIST_FIELDS.items():
        kwargs[fname] = [_build(cls, item) for item in d.get(fname, [])]
    kwargs["weights"] = _build(dm.ObjectiveWeights, d.get("weights", {}))
    kwargs["sim"] = _build(dm.SimParams, d.get("sim", {}))
    # configuration switches must survive the round-trip (they gate
    # behaviour such as the experimental multi-grade zone expansion)
    cfg = d.get("config", {})
    kwargs["config"] = dict(cfg) if isinstance(cfg, dict) else {}
    scenario = Scenario(**kwargs)
    canonicalize_scenario_grade_ids(scenario)
    return scenario


def save_scenario(sc: Scenario, path: str | Path):
    Path(path).write_text(scenario_to_json(sc), encoding="utf-8")


def load_scenario(path: str | Path) -> Scenario:
    return scenario_from_json(Path(path).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ tables
def entities_to_df(items: list) -> pd.DataFrame:
    rows = []
    for it in items:
        d = dataclasses.asdict(it)
        ev = d.pop("evidence", None)
        for k, v in list(d.items()):
            if isinstance(v, (list, dict)):
                d[k] = json.dumps(v)
        if ev:
            d["source"] = ev.get("source", "")
            d["evidence_status"] = ev.get("status", "")
            d["confidence"] = ev.get("confidence", "")
        rows.append(d)
    return pd.DataFrame(rows)


def df_to_entities(df: pd.DataFrame, cls) -> list:
    out = []
    fieldnames = {f.name: f for f in dataclasses.fields(cls)}
    has_ev = "evidence" in fieldnames
    for _, row in df.iterrows():
        data = {}
        for k in fieldnames:
            if k == "evidence":
                continue
            if k in row.index and pd.notna(row[k]):
                v = row[k]
                if isinstance(v, str) and v[:1] in "[{":
                    try:
                        v = json.loads(v)
                    except json.JSONDecodeError:
                        pass
                data[k] = v
        if has_ev:
            data["evidence"] = dm.Evidence(
                source=str(row.get("source", "") or ""),
                status=str(row.get("evidence_status", "ASSUMED") or "ASSUMED"),
                confidence=str(row.get("confidence", "L") or "L"))
        out.append(_build(cls, data))
    return out


TABLE_REGISTRY = {  # table name -> (scenario attribute, dataclass)
    "sites": ("sites", dm.Site),
    "grades": ("grades", dm.Grade),
    "material_profiles": ("material_profiles", dm.MaterialProfile),
    "spaces": ("spaces", dm.StockSpace),
    "segments": ("segments", dm.Segment),
    "pile_zones": ("pile_zones", dm.PileZone),
    "machines": ("machines", dm.Machine),
    "nodes": ("nodes", dm.Node),
    "links": ("links", dm.Link),
    "destinations": ("destinations", dm.Destination),
    "demand": ("demand", dm.DemandEntry),
    "calendars": ("calendars", dm.CalendarEntry),
    "exogenous": ("exogenous", dm.ExogenousFlow),
    "process_transfers": ("process_transfers", dm.ProcessTransfer),
    "belt_profile": ("belt_profile", dm.BeltProfileEntry),
    "fixed_jobs": ("fixed_jobs", dm.FixedJob),
    "assumptions": ("assumptions", dm.Assumption),
    "quality_params": ("quality_params", dm.QualityParam),
}


def scenario_tables(sc: Scenario) -> Dict[str, pd.DataFrame]:
    canonical = copy.deepcopy(sc)
    canonicalize_scenario_grade_ids(canonical)
    return {name: entities_to_df(getattr(canonical, attr))
            for name, (attr, _) in TABLE_REGISTRY.items()}


def export_excel(sc: Scenario) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in scenario_tables(sc).items():
            (df if not df.empty else pd.DataFrame({"empty": []})).to_excel(
                xw, sheet_name=name[:31], index=False)
    return buf.getvalue()


def import_table(sc: Scenario, table: str, df: pd.DataFrame) -> Scenario:
    attr, cls = TABLE_REGISTRY[table]
    setattr(sc, attr, df_to_entities(df, cls))
    canonicalize_scenario_grade_ids(sc)
    return sc


# ------------------------------------------------------------------ run history
class RunStore:
    def __init__(self, path: str | Path = "runs.db"):
        self.path = str(path)
        with self._con() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, scenario TEXT, seed INTEGER, solver TEXT,
                status TEXT, solve_s REAL, replans INTEGER,
                kpis TEXT, note TEXT)""")
            con.execute("""CREATE TABLE IF NOT EXISTS run_artifacts (
                run_id INTEGER PRIMARY KEY,
                schema_version TEXT NOT NULL,
                project TEXT NOT NULL,
                encoding TEXT NOT NULL,
                artifact BLOB NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runs(id))""")

    @contextmanager
    def _con(self):
        """Yield a connection and always close it (important on Windows)."""
        con = sqlite3.connect(self.path)
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def record(self, scenario_name: str, seed: int, solver: str, status: str,
               solve_s: float, replans: int, kpis: dict, note: str = "",
               *, artifact: Optional[Dict[str, Any]] = None,
               schema_version: str = "1.0",
               project: str = "mph-stockyard") -> int:
        safe = {k: v for k, v in kpis.items()
                if isinstance(v, (int, float, str, bool))}
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO runs (ts, scenario, seed, solver, status, solve_s,"
                " replans, kpis, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (dtm.datetime.now().isoformat(timespec="seconds"), scenario_name,
                 seed, solver, status, solve_s, replans, json.dumps(safe), note))
            run_id = int(cur.lastrowid)
            if artifact is not None:
                self._save_artifact(
                    con, run_id, artifact, schema_version=schema_version,
                    project=project)
            return run_id

    @staticmethod
    def _save_artifact(con, run_id: int, artifact: Dict[str, Any], *,
                       schema_version: str, project: str) -> None:
        payload = json.dumps(
            _json_compatible(artifact), separators=(",", ":"), ensure_ascii=False,
            allow_nan=False).encode("utf-8")
        encoded = zlib.compress(payload, level=6)
        con.execute(
            "INSERT INTO run_artifacts "
            "(run_id, schema_version, project, encoding, artifact) "
            "VALUES (?,?,?,?,?)",
            (int(run_id), str(schema_version), str(project),
             "json+zlib", sqlite3.Binary(encoded)))

    def save_run_bundle(self, run_id: int, artifact: Dict[str, Any], *,
                        schema_version: str = "1.0",
                        project: str = "mph-stockyard") -> None:
        """Attach one analysis bundle; an existing run artifact is immutable."""
        with self._con() as con:
            exists = con.execute(
                "SELECT 1 FROM runs WHERE id = ?", (int(run_id),)).fetchone()
            if not exists:
                raise KeyError(f"run {run_id} does not exist")
            attached = con.execute(
                "SELECT 1 FROM run_artifacts WHERE run_id = ?",
                (int(run_id),)).fetchone()
            if attached:
                raise FileExistsError(
                    f"run {run_id} already has an immutable evidence bundle")
            self._save_artifact(
                con, int(run_id), artifact, schema_version=schema_version,
                project=project)

    @staticmethod
    def _decode_artifact(encoding: str, payload: bytes) -> Dict[str, Any]:
        raw = bytes(payload)
        if encoding == "json+zlib":
            raw = zlib.decompress(raw)
        elif encoding != "json":
            raise ValueError(f"unsupported run-artifact encoding {encoding!r}")
        decoded = json.loads(raw.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("run artifact must decode to an object")
        return decoded

    def load_run_bundle(self, run_id: int) -> Dict[str, Any]:
        """Return the full normalized bundle, or fail clearly for legacy rows."""
        with self._con() as con:
            row = con.execute(
                "SELECT r.id, r.ts, r.scenario, r.seed, r.solver, r.status, "
                "r.solve_s, r.replans, r.kpis, r.note, a.schema_version, "
                "a.project, a.encoding, a.artifact "
                "FROM runs r LEFT JOIN run_artifacts a ON a.run_id = r.id "
                "WHERE r.id = ?", (int(run_id),)).fetchone()
        if row is None:
            raise KeyError(f"run {run_id} does not exist")
        if row[13] is None:
            raise KeyError(
                f"run {run_id} predates evidence-bundle persistence; rerun "
                "the scenario to enable detailed analysis")
        artifact = self._decode_artifact(str(row[12]), row[13])
        run = artifact.setdefault("run", {})
        run["id"] = int(row[0])
        if not run.get("started_at"):
            run["started_at"] = row[1]
        artifact["storage"] = {
            "schema_version": row[10], "project": row[11],
            "recorded_at": row[1],
        }
        return artifact

    def list_run_bundles(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Compact catalog including whether detailed evidence is available."""
        with self._con() as con:
            rows = con.execute(
                "SELECT r.id, r.ts, r.scenario, r.seed, r.solver, r.status, "
                "r.solve_s, r.replans, r.kpis, r.note, a.schema_version, "
                "a.project, length(a.artifact) "
                "FROM runs r LEFT JOIN run_artifacts a ON a.run_id = r.id "
                "ORDER BY r.id DESC LIMIT ?", (max(int(limit), 1),)).fetchall()
        return [{
            "id": int(row[0]), "recorded_at": row[1], "scenario": row[2],
            "seed": row[3], "solver": row[4], "status": row[5],
            "solve_s": row[6], "replans": row[7],
            "summary_kpis": json.loads(row[8] or "{}"), "note": row[9],
            "bundle_available": row[10] is not None,
            "schema_version": row[10], "project": row[11],
            "compressed_bytes": int(row[12] or 0),
        } for row in rows]

    def history(self, limit: int = 200) -> pd.DataFrame:
        with self._con() as con:
            return pd.read_sql_query(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", con, params=(limit,))
