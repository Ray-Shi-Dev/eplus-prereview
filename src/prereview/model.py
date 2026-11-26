"""A simulated model as prereview sees it: SQL results, err file and (optionally) the IDF."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errfile import ErrSummary, read_err
from .idf import Idf
from .sqlout import SqlResults


@dataclass
class ModelRun:
    label: str
    role: str  # "proposed" | "baseline"
    path: str
    sql: Optional[SqlResults]  # None when EnergyPlus stopped before writing results
    err: Optional[ErrSummary] = None
    idf: Optional[Idf] = None
    idf_path: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str, role: str, label: Optional[str] = None) -> "ModelRun":
        """path: a run folder or one run's .sql file.

        Files are paired by name: eplusout.sql with eplusout.err and in.idf / eplusout.expidf (EnergyPlus
        defaults), or X.sql with X.err and X.idf / X.expidf. A folder holding several runs' .sql files is
        refused, so results from different runs are never mixed."""
        def exists(name: str) -> Optional[str]:
            p = os.path.join(folder, name)
            return p if os.path.isfile(p) else None

        if os.path.isdir(path):
            folder = path
            sql: Optional[str] = exists("eplusout.sql")
            if sql is None:
                cands = sorted(glob.glob(os.path.join(folder, "*.sql")))
                if len(cands) > 1:
                    raise ValueError(
                        f"{folder} holds several .sql files ({', '.join(os.path.basename(c) for c in cands[:4])}"
                        f"{' …' if len(cands) > 4 else ''}); pass the .sql file of one run instead of the folder.")
                sql = cands[0] if cands else None
        else:
            sql = path
            folder = os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(sql))[0] if sql else "eplusout"
        err_path = exists(stem + ".err")
        if err_path is None and sql is None:
            errs = sorted(glob.glob(os.path.join(folder, "*.err")))
            err_path = errs[0] if len(errs) == 1 else None
        idf_path = None
        names = ([stem + ".expidf", "in.idf", stem + ".idf"] if stem == "eplusout" else
                 [stem + ".expidf", stem + ".idf"])
        for name in names:
            idf_path = exists(name)
            if idf_path:
                break
        sql_res = None
        if sql:
            try:
                sql_res = SqlResults(sql)
                if not sql_res.has_table("Simulations"):
                    sql_res = None
            except Exception:  # noqa: BLE001 - unreadable/partial file: treat as no results
                sql_res = None
        run = cls(
            label=label or os.path.basename(os.path.normpath(folder)),
            role=role,
            path=folder,
            sql=sql_res,
            err=read_err(err_path) if err_path else None,
            idf=Idf.from_file(idf_path) if idf_path else None,
            idf_path=idf_path,
        )
        if sql_res is None and err_path is None:
            run.notes.append(f"No EnergyPlus results (.sql or .err) found in {folder}.")
        elif err_path is None:
            run.notes.append("No .err file found next to the SQL output; err triage skipped.")
        elif sql_res is None:
            run.notes.append("No usable SQL output (the run stopped early or Output:SQLite was missing).")
        if idf_path is None and sql_res is not None:
            run.notes.append("No IDF found next to the results; checks that read inputs (HVAC schedules, reheat "
                             "types, reporting tolerance) are limited.")
        return run

    @property
    def has_results(self) -> bool:
        return self.sql is not None

    @property
    def completed(self) -> bool:
        """Did EnergyPlus finish? The .err summary line is authoritative (some versions leave the SQL
        Simulations.CompletedSuccessfully flag FALSE even after a clean run)."""
        if self.err is not None and self.err.warnings_total is not None:
            return self.err.completed and not self.err.fatal
        if self.sql is not None:
            return bool(self.sql.completed_successfully()) or bool(self.sql.general())
        return False

    # Convenience accessors used by several rules ---------------------------------
    def general(self) -> Dict[str, str]:
        return self.sql.general() if self.sql is not None else {}

    def north_axis(self) -> Optional[float]:
        g = self.general()
        for key in ("North Axis Angle",):
            if key in g:
                try:
                    return float(g[key])
                except ValueError:
                    return None
        return None

    def appendix_g_rotation(self) -> Optional[float]:
        g = self.general()
        try:
            return float(g.get("Rotation for Appendix G", ""))
        except ValueError:
            return None

    def hours_simulated(self) -> Optional[float]:
        try:
            return float(self.general().get("Hours Simulated", ""))
        except ValueError:
            return None

    def weather(self) -> str:
        return self.general().get("Weather File", "").strip()

    def conditioned_area(self) -> Optional[float]:
        if self.sql is None:
            return None
        return self.sql.tab_si("AnnualBuildingUtilityPerformanceSummary", "Building Area",
                               "Net Conditioned Building Area", "Area", "m2")

    def end_uses_gj(self) -> Dict[str, Dict[str, float]]:
        """ABUPS End Uses by end use (upper case) and fuel column, converted to GJ. Water is left out."""
        from .sqlout import to_si, to_float
        out: Dict[str, Dict[str, float]] = {}
        if self.sql is None:
            return out
        t = self.sql.tabular("AnnualBuildingUtilityPerformanceSummary", "End Uses")
        for row, cols in t.items():
            name = row.strip().upper()
            if not name:
                continue
            for col, (val, units) in cols.items():
                if "water" in col.strip().lower() and "district" not in col.strip().lower():
                    continue
                v, si = to_si(to_float(val), units or "GJ")
                if v is not None and si == "GJ":
                    out.setdefault(name, {})[col.strip()] = v
        return out

    def total_area(self) -> Optional[float]:
        if self.sql is None:
            return None
        return self.sql.tab_si("AnnualBuildingUtilityPerformanceSummary", "Building Area",
                               "Total Building Area", "Area", "m2")
