"""Read EnergyPlus SQLite output (Output:SQLite, SimpleAndTabular).

All numbers come from EnergyPlus's own tables wherever possible, so the checks report
what EnergyPlus computed rather than a re-implementation of it.
"""

from __future__ import annotations

import os
import re
import sqlite3
from array import array
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_NUM = re.compile(r"^\s*-?\d+(\.\d+)?([eE][-+]?\d+)?\s*$")


def to_float(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if _NUM.match(s):
        return float(s)
    return None


# Tabular reports can be in SI or IP (OutputControl:Table:Style). Factors to SI (offset for °F).
_TO_SI = {
    "m2": ("m2", 1.0), "ft2": ("m2", 0.09290304),
    "gj": ("GJ", 1.0), "mj": ("GJ", 1e-3), "kj": ("GJ", 1e-6), "j": ("GJ", 1e-9), "kwh": ("GJ", 0.0036),
    "mwh": ("GJ", 3.6), "kbtu": ("GJ", 0.001055056), "mbtu": ("GJ", 1.055056), "btu": ("GJ", 1.055056e-6),
    "therm": ("GJ", 0.1055056), "mmbtu": ("GJ", 1.055056),
    "w": ("W", 1.0), "kw": ("W", 1000.0), "btu/h": ("W", 0.29307107), "kbtu/h": ("W", 293.07107),
    "m3/s": ("m3/s", 1.0), "l/s": ("m3/s", 0.001), "ft3/min": ("m3/s", 0.000471947443),
    "c": ("C", 1.0), "f": ("C", None),
}


def to_si(value: Optional[float], units: str) -> Tuple[Optional[float], Optional[str]]:
    """Convert a tabular value to SI. Returns (value, SI unit) or (None, None) for unknown units."""
    if value is None:
        return None, None
    u = (units or "").strip().lower().replace("°", "").replace(" ", "")
    if u in ("", "hr", "%"):
        return value, units
    conv = _TO_SI.get(u)
    if conv is None:
        return None, None
    si, factor = conv
    if factor is None:  # °F
        return (value - 32.0) / 1.8, si
    return value * factor, si


@dataclass
class Zone:
    index: int
    name: str
    floor_area: float  # m2, one zone instance (multipliers separate)
    volume: float
    multiplier: float
    part_of_total: bool
    ext_gross_wall: float
    ext_window: float

    @property
    def total_area(self) -> float:
        return self.floor_area * self.multiplier


class SqlResults:
    """Thin, cached accessor over an eplusout.sql file."""

    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.path = path
        # read-only URI; as_uri() also handles Windows drive letters, spaces, '#' and '?' in paths
        self.con = sqlite3.connect(Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True)
        self._cache: Dict[str, object] = {}
        self._tables = {
            r[0].lower()
            for r in self.con.execute("select name from sqlite_master where type in ('table','view')")
        }

    def close(self) -> None:
        self.con.close()

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def q(self, sql: str, args: tuple = ()) -> List[tuple]:
        return self.con.execute(sql, args).fetchall()

    # ----- simulation meta ------------------------------------------------------
    def version(self) -> Optional[str]:
        rows = self.q("select EnergyPlusVersion from Simulations order by SimulationIndex limit 1")
        if not rows:
            return None
        m = re.search(r"Version\s+([\d.]+)", rows[0][0] or "")
        return m.group(1) if m else rows[0][0]

    def completed_successfully(self) -> Optional[bool]:
        rows = self.q("select Completed, CompletedSuccessfully from Simulations")
        if not rows:
            return None
        return all(str(r[1]).strip().upper() in ("1", "TRUE", "YES") for r in rows)

    def timesteps_per_hour(self) -> Optional[int]:
        rows = self.q("select NumTimestepsPerHour from Simulations limit 1")
        return int(rows[0][0]) if rows and rows[0][0] is not None else None

    # ----- tabular ----------------------------------------------------------------
    def tabular(self, report: str, table: str, for_string: Optional[str] = "Entire Facility",
                ) -> Dict[str, Dict[str, Tuple[str, str]]]:
        """{row: {column: (value, units)}} for one tabular report table."""
        key = f"tab|{report}|{table}|{for_string}"
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]
        sql = ("select RowName, ColumnName, Value, Units, ReportForString from TabularDataWithStrings "
               "where ReportName=? and TableName=?")
        args: tuple = (report, table)
        rows = self.q(sql, args)
        if for_string is not None:
            filt = [r for r in rows if (r[4] or "").strip().upper() == for_string.upper()]
            rows = filt or rows
        out: Dict[str, Dict[str, Tuple[str, str]]] = defaultdict(dict)
        for row, col, val, units, _ in rows:
            out[row][col] = ((val or "").strip(), units or "")
        result = dict(out)
        self._cache[key] = result
        return result

    def tab_value(self, report: str, table: str, row: str, col: str) -> Optional[str]:
        t = self.tabular(report, table)
        r = t.get(row)
        if r is None:
            for k in t:
                if k.strip().upper() == row.strip().upper():
                    r = t[k]
                    break
        if r is None:
            return None
        if col in r:
            return r[col][0]
        for k, v in r.items():
            if k.strip().upper() == col.strip().upper():
                return v[0]
        return None

    def tab_float(self, report: str, table: str, row: str, col: str) -> Optional[float]:
        return to_float(self.tab_value(report, table, row, col))

    def tab_units(self, report: str, table: str, row: str, col: str) -> str:
        t = self.tabular(report, table)
        for rk, cols in t.items():
            if rk.strip().upper() == row.strip().upper():
                for ck, (_, units) in cols.items():
                    if ck.strip().upper() == col.strip().upper():
                        return units or ""
        return ""

    def tab_si(self, report: str, table: str, row: str, col: str, expect: str) -> Optional[float]:
        """A tabular number converted to SI; None when missing or in an unexpected unit."""
        v, si = to_si(self.tab_float(report, table, row, col), self.tab_units(report, table, row, col))
        if v is None or (si not in (expect, "") and si is not None and si.lower() != expect.lower()):
            return None
        return v

    def general(self) -> Dict[str, str]:
        t = self.tabular("InputVerificationandResultsSummary", "General")
        return {k: v.get("Value", ("", ""))[0] for k, v in t.items()}

    # ----- inputs echoed into SQL ------------------------------------------------
    def zones(self) -> List[Zone]:
        if "zones" in self._cache:
            return self._cache["zones"]  # type: ignore[return-value]
        rows = self.q(
            "select ZoneIndex, ZoneName, FloorArea, Volume, Multiplier, ListMultiplier, IsPartOfTotalArea, "
            "ExtGrossWallArea, ExtWindowArea from Zones order by ZoneIndex"
        )
        zones = [
            Zone(
                index=r[0], name=r[1], floor_area=r[2] or 0.0, volume=r[3] or 0.0,
                multiplier=float(r[4] or 1) * float(r[5] or 1),
                part_of_total=bool(r[6]) if r[6] is not None else True,
                ext_gross_wall=r[7] or 0.0, ext_window=r[8] or 0.0,
            )
            for r in rows
        ]
        self._cache["zones"] = zones
        return zones

    def zone_by_index(self) -> Dict[int, Zone]:
        return {z.index: z for z in self.zones()}

    def schedules(self) -> Dict[int, Tuple[str, str, float, float]]:
        rows = self.q("select ScheduleIndex, ScheduleName, ScheduleType, ScheduleMinimum, ScheduleMaximum from Schedules")
        return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}

    def nominal(self, table: str) -> List[dict]:
        """Rows of a Nominal* table with zone and schedule names resolved."""
        if not self.has_table(table):
            return []
        cols = [r[1] for r in self.q(f"pragma table_info('{table}')")]
        scheds = self.schedules()
        zones = self.zone_by_index()
        out = []
        for row in self.q(f"select * from {table}"):
            d = dict(zip(cols, row))
            zi = d.get("ZoneIndex")
            d["ZoneName"] = zones[zi].name if zi in zones else None
            for c in list(d):
                if c.lower().endswith("scheduleindex") or c == "SchedNo":
                    si = d[c]
                    d[c.replace("Index", "Name") if "Index" in c else "ScheduleName"] = (
                        scheds[si][0] if si in scheds else None)
            out.append(d)
        return out

    def component_sizes(self) -> List[dict]:
        rows = self.q("select CompType, CompName, Description, Value, Units from ComponentSizes")
        return [dict(type=r[0], name=r[1], desc=r[2], value=r[3], units=r[4]) for r in rows]

    def zone_sizes(self) -> List[dict]:
        rows = self.q(
            "select ZoneName, LoadType, CalcDesLoad, UserDesLoad, CalcDesFlow, UserDesFlow, DesDayName, "
            "PeakHrMin, PeakTemp, CalcOutsideAirFlow from ZoneSizes"
        )
        return [
            dict(zone=r[0], load_type=r[1], calc_load=r[2], user_load=r[3], calc_flow=r[4],
                 user_flow=r[5], design_day=r[6], peak_time=r[7], peak_temp=r[8], oa_flow=r[9])
            for r in rows
        ]

    def system_sizes(self) -> List[dict]:
        rows = self.q(
            "select SystemName, LoadType, PeakLoadType, UserDesCap, CalcDesVolFlow, UserDesVolFlow, DesDayName "
            "from SystemSizes"
        )
        return [dict(system=r[0], load_type=r[1], peak_type=r[2], user_cap=r[3], calc_flow=r[4],
                     user_flow=r[5], design_day=r[6]) for r in rows]

    def errors(self) -> List[Tuple[int, str, int]]:
        if not self.has_table("Errors"):
            return []
        return [(r[0], r[1], r[2]) for r in self.q("select ErrorType, ErrorMessage, Count from Errors")]

    # ----- time series --------------------------------------------------------------
    def _run_period_time_index(self, interval_type: int = 1) -> List[int]:
        """TimeIndex values in weather-file run periods (no warmup) for one reporting frequency.

        interval_type 1 = hourly; 0 = sub-hourly (zone timestep). Recent EnergyPlus versions store
        zone-timestep rows with IntervalType -1, older ones with 0, so both count as sub-hourly.
        """
        ck = f"runtime|{interval_type}"
        if ck in self._cache:
            return self._cache[ck]  # type: ignore[return-value]
        env_types = {r[0]: r[1] for r in self.q("select EnvironmentPeriodIndex, EnvironmentType from EnvironmentPeriods")}
        rows = self.q(
            "select TimeIndex, EnvironmentPeriodIndex, WarmupFlag, IntervalType from Time order by TimeIndex"
        )
        wanted = {1} if interval_type == 1 else {-1, 0}
        idx = [r[0] for r in rows if env_types.get(r[1]) == 3 and not r[2] and r[3] in wanted]
        self._cache[ck] = idx
        return idx

    def time_stamps(self) -> List[Tuple[int, int, int, str]]:
        """(month, day, hour, day type) for each run-period hour, in order."""
        if "stamps" in self._cache:
            return self._cache["stamps"]  # type: ignore[return-value]
        idx = set(self._run_period_time_index(1))
        rows = self.q("select TimeIndex, Month, Day, Hour, DayType from Time order by TimeIndex")
        out = [(r[1], r[2], r[3], r[4]) for r in rows if r[0] in idx]
        self._cache["stamps"] = out
        return out

    def available_variables(self, frequency: str = "Hourly") -> Dict[str, List[str]]:
        rows = self.q(
            "select Name, KeyValue from ReportDataDictionary where IsMeter=0 and ReportingFrequency=?",
            (frequency,))
        out: Dict[str, List[str]] = defaultdict(list)
        for name, key in rows:
            out[name].append(key)
        return dict(out)

    def series(self, name: str, key: Optional[str] = None, frequency: str = "Hourly"
               ) -> Dict[str, List[float]]:
        """{key: values for each run-period interval} for a report variable (one query per variable)."""
        ck = f"series|{name}|{key}|{frequency}"
        if ck in self._cache:
            return self._cache[ck]  # type: ignore[return-value]
        sql = ("select ReportDataDictionaryIndex, KeyValue from ReportDataDictionary "
               "where IsMeter=0 and ReportingFrequency=? and Name=?")
        args: tuple = (frequency, name)
        if key is not None:
            sql += " and upper(KeyValue)=upper(?)"
            args = (frequency, name, key)
        dicts = self.q(sql, args)
        if not dicts:
            self._cache[ck] = {}
            return {}
        order = self._run_period_time_index(0 if frequency.lower() in ("zone timestep", "timestep") else 1)
        pos = {t: i for i, t in enumerate(order)}
        names = {di: k for di, k in dicts}
        # array('d') holds 8 bytes per value instead of ~32 for a list of floats
        out: Dict[str, List[float]] = {k: array("d", bytes(8 * len(order))) for k in names.values()}  # type: ignore
        marks = ",".join("?" * len(names))
        for di, t, v in self.q(
                f"select ReportDataDictionaryIndex, TimeIndex, Value from ReportData "
                f"where ReportDataDictionaryIndex in ({marks})", tuple(names)):
            p = pos.get(t)
            if p is not None:
                out[names[di]][p] = v
        self._cache[ck] = out
        return out

    def forget(self, name: str) -> None:
        """Drop cached series of one variable (rules call this when done with it, to bound memory)."""
        for k in [k for k in self._cache if k.startswith(f"series|{name}|")]:
            del self._cache[k]

    def hourly(self, name: str, key: Optional[str] = None) -> Dict[str, List[float]]:
        """{key: values for each run-period hour} for an hourly report variable."""
        return self.series(name, key, "Hourly")

    def has_hourly(self, name: str) -> bool:
        return self.has_series(name, "Hourly")

    def has_series(self, name: str, frequency: str = "Hourly") -> bool:
        return bool(self.q(
            "select 1 from ReportDataDictionary where IsMeter=0 and ReportingFrequency=? and Name=? limit 1",
            (frequency, name)))
