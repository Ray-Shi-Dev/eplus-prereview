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
        """path: a run folder (with eplusout.sql) or an .sql file."""
        if os.path.isdir(path):
            folder = path
            sql: Optional[str] = os.path.join(folder, "eplusout.sql")
            if not os.path.isfile(sql):
                cands = sorted(glob.glob(os.path.join(folder, "*.sql")))
                sql = cands[0] if cands else None
        else:
            sql = path
            folder = os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(sql))[0] if sql else "eplusout"
        err_path = None
        for cand in (os.path.join(folder, stem + ".err"),
                     os.path.join(folder, "eplusout.err")):
            if os.path.isfile(cand):
                err_path = cand
                break
        if err_path is None:
            errs = sorted(glob.glob(os.path.join(folder, "*.err")))
            err_path = errs[0] if errs else None
        idf_path = None
        for cand in [os.path.join(folder, "in.idf")] + sorted(glob.glob(os.path.join(folder, "*.idf"))):
            if os.path.isfile(cand):
                idf_path = cand
                break
        sql_res = None
        if sql:
            try:
                sql_res = SqlResults(sql)
                if not sql_res.has_table("Simulations"):
                    sql_res = None
            except Exception:  # noqa: BLE001 - unreadable/partial file: treat as no results
                sql_res = None
        if sql_res is None and err_path is None:
            raise FileNotFoundError(f"No EnergyPlus results (.sql or .err) found in {folder}")
        run = cls(
            label=label or os.path.basename(os.path.normpath(folder)),
            role=role,
            path=folder,
            sql=sql_res,
            err=read_err(err_path) if err_path else None,
            idf=Idf.from_file(idf_path) if idf_path else None,
            idf_path=idf_path,
        )
        if err_path is None:
            run.notes.append("No .err file found next to the SQL output; err triage skipped.")
        if sql_res is None:
            run.notes.append("No usable SQL output (the run stopped early or Output:SQLite was missing).")
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
        return self.sql.tab_float("AnnualBuildingUtilityPerformanceSummary", "Building Area",
                                  "Net Conditioned Building Area", "Area")

    def total_area(self) -> Optional[float]:
        if self.sql is None:
            return None
        return self.sql.tab_float("AnnualBuildingUtilityPerformanceSummary", "Building Area",
                                  "Total Building Area", "Area")
