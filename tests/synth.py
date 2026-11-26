"""Build tiny EnergyPlus-like SQLite files for unit tests (only the tables prereview reads)."""

from __future__ import annotations

import os
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

SCHEMA = """
create table Simulations (SimulationIndex integer primary key, EnergyPlusVersion text, TimeStamp text,
  NumTimestepsPerHour integer, Completed bool, CompletedSuccessfully bool);
create table EnvironmentPeriods (EnvironmentPeriodIndex integer primary key, SimulationIndex integer,
  EnvironmentName text, EnvironmentType integer);
create table Time (TimeIndex integer primary key, Year integer, Month integer, Day integer, Hour integer,
  Minute integer, Dst integer, Interval integer, IntervalType integer, SimulationDays integer, DayType text,
  EnvironmentPeriodIndex integer, WarmupFlag integer);
create table ReportDataDictionary (ReportDataDictionaryIndex integer primary key, IsMeter integer, Type text,
  IndexGroup text, TimestepType text, KeyValue text, Name text, ReportingFrequency text, ScheduleName text,
  Units text);
create table ReportData (ReportDataIndex integer primary key, TimeIndex integer, ReportDataDictionaryIndex integer,
  Value real);
create table TabularDataWithStrings (TabularDataIndex integer primary key, Value text, ReportName text,
  ReportForString text, TableName text, RowName text, ColumnName text, Units text);
create table Zones (ZoneIndex integer primary key, ZoneName text, RelNorth real, OriginX real, OriginY real,
  OriginZ real, CentroidX real, CentroidY real, CentroidZ real, OfType integer, Multiplier real,
  ListMultiplier real, MinimumX real, MaximumX real, MinimumY real, MaximumY real, MinimumZ real, MaximumZ real,
  CeilingHeight real, Volume real, InsideConvectionAlgo integer, OutsideConvectionAlgo integer, FloorArea real,
  ExtGrossWallArea real, ExtNetWallArea real, ExtWindowArea real, IsPartOfTotalArea integer);
create table Schedules (ScheduleIndex integer primary key, ScheduleName text, ScheduleType text,
  ScheduleMinimum real, ScheduleMaximum real);
create table ComponentSizes (ComponentSizesIndex integer primary key, CompType text, CompName text,
  Description text, Value real, Units text);
create table ZoneSizes (ZoneSizesIndex integer primary key, ZoneName text, LoadType text, CalcDesLoad real,
  UserDesLoad real, CalcDesFlow real, UserDesFlow real, DesDayName text, PeakHrMin text, PeakTemp real,
  PeakHumRat real, CalcOutsideAirFlow real, DOASHeatAddRate real);
create table SystemSizes (SystemSizesIndex integer primary key, SystemName text, LoadType text,
  PeakLoadType text, UserDesCap real, CalcDesVolFlow real, UserDesVolFlow real, DesDayName text, PeakHrMin text);
"""


class SynthSql:
    """Write a minimal results database: one annual run period, hourly + 4-per-hour timestep rows."""

    def __init__(self, path: str, hours: int = 48, timesteps: int = 4, version: str = "26.2.0"):
        if os.path.exists(path):
            os.remove(path)
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)
        self.hours = hours
        self.ts = timesteps
        self.con.execute("insert into Simulations values (1, ?, '', ?, 1, 1)",
                         (f"EnergyPlus, Version {version}-abc, YMD=2026.01.01 00:00", timesteps))
        self.con.execute("insert into EnvironmentPeriods values (1, 1, 'ANNUAL', 3)")
        self.hour_index: List[int] = []
        self.ts_index: List[int] = []
        t = 1
        for h in range(hours):
            day, hour = divmod(h, 24)
            for k in range(timesteps):
                self.con.execute("insert into Time values (?,2026,1,?,?,?,0,?,-1,?, ?,1,0)",
                                 (t, day + 1, hour + (1 if k == timesteps - 1 else 0),
                                  (k + 1) * 60 // timesteps % 60, 60 // timesteps, day + 1, "Monday"))
                self.ts_index.append(t)
                t += 1
            self.con.execute("insert into Time values (?,2026,1,?,?,0,0,60,1,?,?,1,0)",
                             (t, day + 1, hour + 1, day + 1, "Monday"))
            self.hour_index.append(t)
            t += 1
        self._rdd = 0

    def variable(self, name: str, key: str, values: Sequence[float], frequency: str = "Hourly",
                 units: str = "") -> None:
        self._rdd += 1
        self.con.execute("insert into ReportDataDictionary values (?,0,'Avg','Zone','Zone',?,?,?,NULL,?)",
                         (self._rdd, key, name, frequency, units))
        idx = self.hour_index if frequency == "Hourly" else self.ts_index
        assert len(values) == len(idx), (name, len(values), len(idx))
        self.con.executemany("insert into ReportData (TimeIndex, ReportDataDictionaryIndex, Value) values (?,?,?)",
                             [(t, self._rdd, float(v)) for t, v in zip(idx, values)])

    def tab(self, report: str, table: str, row: str, col: str, value, units: str = "",
            for_string: str = "Entire Facility") -> None:
        self.con.execute("insert into TabularDataWithStrings (Value, ReportName, ReportForString, TableName, RowName,"
                         " ColumnName, Units) values (?,?,?,?,?,?,?)",
                         (str(value), report, for_string, table, row, col, units))

    def zone(self, index: int, name: str, area: float, multiplier: float = 1.0) -> None:
        self.con.execute("insert into Zones (ZoneIndex, ZoneName, Multiplier, ListMultiplier, FloorArea, Volume, "
                         "ExtGrossWallArea, ExtWindowArea, IsPartOfTotalArea) values (?,?,?,1,?,?,0,0,1)",
                         (index, name, multiplier, area, area * 3))

    def general(self, weather: str = "Taipei TMYx", hours: float = 8760, north: float = 0.0) -> None:
        for row, val in (("Weather File", weather), ("Hours Simulated", f"{hours:.2f}"),
                         ("North Axis Angle", f"{north:.2f}"), ("Rotation for Appendix G", "0.00")):
            self.tab("InputVerificationandResultsSummary", "General", row, "Value", val)

    def close(self) -> str:
        self.con.commit()
        self.con.close()
        return self.path


def err_text(lines: List[str], completed: bool = True, warnings: int = 0, severe: int = 0) -> str:
    head = "Program Version,EnergyPlus, Version 26.2.0-abc, YMD=2026.01.01 00:00,\n"
    tail = (f"   ************* EnergyPlus Completed Successfully-- {warnings} Warning; {severe} Severe Errors; "
            f"Elapsed Time=00hr 00min  1.00sec\n" if completed else
            f"   ************* EnergyPlus Terminated--Fatal Error Detected. {warnings} Warning; {severe} Severe "
            f"Errors; Elapsed Time=00hr 00min  0.10sec\n")
    return head + "\n".join(lines) + "\n" + tail


def write(path: str, text: str) -> str:
    with open(path, "w") as fh:
        fh.write(text)
    return path


def facility_series(h_steps: List[int], c_steps: List[int], n_steps: int, dt: float = 0.25
                    ) -> Tuple[List[float], List[float]]:
    h = [0.0] * n_steps
    c = [0.0] * n_steps
    for i in h_steps:
        h[i] = dt
    for i in c_steps:
        c[i] = dt
    return h, c
