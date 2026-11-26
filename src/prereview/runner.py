"""Run EnergyPlus with the outputs prereview needs, without changing the physics.

Only output-request objects are added or replaced. Output requests do not change the
simulated heat balance, so results stay identical to the user's own run (this is
checked by the test suite on DOE prototype models).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from .idf import Idf, format_object

# Hourly variables used by the unmet-hours diagnosis and the consistency checks.
HOURLY_VARIABLES = [
    "Zone Heating Setpoint Not Met While Occupied Time",
    "Zone Cooling Setpoint Not Met While Occupied Time",
    "Zone Heating Setpoint Not Met Time",
    "Zone Cooling Setpoint Not Met Time",
    "Zone Air System Sensible Heating Rate",
    "Zone Air System Sensible Cooling Rate",
    "Zone Thermostat Heating Setpoint Temperature",
    "Zone Thermostat Cooling Setpoint Temperature",
    "Zone Mean Air Temperature",
    "Zone People Occupant Count",
    "Zone Lights Electricity Rate",
    "Zone Electric Equipment Electricity Rate",
    "Zone Gas Equipment NaturalGas Rate",
    "Zone Other Equipment Total Heating Rate",
    "Zone Air Terminal VAV Damper Position",
    "Site Outdoor Air Drybulb Temperature",
    "Site Outdoor Air Wetbulb Temperature",
]

# Facility-level unmet time at the zone timestep: lets prereview count the hours in which any
# zone is unmet (heating or cooling) exactly as EnergyPlus counts each of them.
TIMESTEP_VARIABLES = [
    "Facility Heating Setpoint Not Met While Occupied Time",
    "Facility Cooling Setpoint Not Met While Occupied Time",
]

# Schedules whose hourly values the consistency checks compare. Any field of these objects
# that names a schedule is requested (no field-position tables, so it works across versions).
SCHEDULE_SOURCE_CLASSES = (
    "PEOPLE", "LIGHTS", "ELECTRICEQUIPMENT", "GASEQUIPMENT", "HOTWATEREQUIPMENT", "STEAMEQUIPMENT",
    "OTHEREQUIPMENT", "ZONEINFILTRATION:DESIGNFLOWRATE", "ZONEINFILTRATION:EFFECTIVELEAKAGEAREA",
    "ZONEINFILTRATION:FLOWCOEFFICIENT", "THERMOSTATSETPOINT:DUALSETPOINT",
    "THERMOSTATSETPOINT:SINGLEHEATING", "THERMOSTATSETPOINT:SINGLECOOLING",
    "THERMOSTATSETPOINT:SINGLEHEATINGORCOOLING", "AVAILABILITYMANAGER:SCHEDULED",
    "AVAILABILITYMANAGER:NIGHTCYCLE", "DESIGNSPECIFICATION:OUTDOORAIR",
)
SCHEDULE_CLASSES = ("SCHEDULE:COMPACT", "SCHEDULE:YEAR", "SCHEDULE:CONSTANT", "SCHEDULE:FILE",
                    "SCHEDULE:DAY:INTERVAL", "SCHEDULE:DAY:HOURLY", "SCHEDULE:DAY:LIST")


# Output classes we take over. OutputControl:Files can switch SQLite output off, so we
# replace it with one that keeps everything on.
_REPLACED_CLASSES = ["Output:SQLite", "OutputControl:Files"]


class RunError(RuntimeError):
    pass


@dataclass
class RunResult:
    out_dir: str
    idf_path: str
    returncode: int
    elapsed_s: float
    sql_path: Optional[str]
    err_path: Optional[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.sql_path is not None


def find_energyplus(explicit: Optional[str] = None) -> str:
    """Locate the energyplus executable."""
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("PREREVIEW_ENERGYPLUS") or os.environ.get("ENERGYPLUS")
    if env:
        candidates.append(env)
    which = shutil.which("energyplus") or shutil.which("EnergyPlus")
    if which:
        candidates.append(which)
    patterns = [
        "/usr/local/EnergyPlus-*/energyplus",
        "/Applications/EnergyPlus-*/energyplus",
        "C:/EnergyPlusV*/energyplus.exe",
        os.path.expanduser("~/opt/EnergyPlus-*/energyplus"),
        "/home/claude/opt/EnergyPlus-*/energyplus",
    ]
    for p in patterns:
        candidates.extend(sorted(glob.glob(p), reverse=True))
    for c in candidates:
        if c and os.path.isdir(c):
            exe = os.path.join(c, "energyplus.exe" if os.name == "nt" else "energyplus")
            if os.path.isfile(exe):
                return exe
        if c and os.path.isfile(c):
            return c
    raise RunError(
        "EnergyPlus not found. Pass --energyplus PATH or set PREREVIEW_ENERGYPLUS."
    )


def energyplus_version(exe: str) -> str:
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
    text = (out.stdout or out.stderr).strip()
    # "EnergyPlus, Version 26.2.0-4bd7a1f26f"
    if "Version" in text:
        return text.split("Version", 1)[1].strip().split("-")[0]
    return text


def _major_minor(v: str) -> str:
    parts = v.strip().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else v.strip()


def _referenced_schedules(idf: Idf) -> List[str]:
    names = {}
    for cls in SCHEDULE_CLASSES:
        for o in idf.by_class(cls):
            if o.name:
                names[o.name.upper()] = o.name
    found = {}
    for o in idf.objects:
        if o.key in SCHEDULE_SOURCE_CLASSES or o.key.startswith(("ZONEHVAC:", "AVAILABILITYMANAGER:")):
            for f in o.fields[1:]:
                k = f.strip().upper()
                if k in names:
                    found[k] = names[k]
    return sorted(found.values(), key=str.upper)


def _supply_outlet_nodes(idf: Idf) -> List[str]:
    nodes = []
    for loop in idf.by_class("AirLoopHVAC"):
        node = loop.get(9).strip()
        if not node:
            continue
        nl = idf.find("NodeList", node)
        nodes.extend([f for f in nl.fields[1:] if f] if nl is not None else [node])
    return nodes


def inject_outputs(idf: Idf, variables: Optional[List[str]] = None) -> Idf:
    """Return a copy of the model with the output requests prereview needs."""
    variables = HOURLY_VARIABLES if variables is None else variables
    model = idf.remove_classes(_REPLACED_CLASSES)

    # Summary reports: keep the user's list, make sure AllSummary is included.
    reports = model.by_class("Output:Table:SummaryReports")
    wanted = []
    for r in reports:
        wanted.extend(f for f in r.fields if f)
    if not any(w.upper() in ("ALLSUMMARY", "ALLSUMMARYANDMONTHLY", "ALLSUMMARYANDSIZINGPERIOD",
                             "ALLSUMMARYMONTHLYANDSIZINGPERIOD") for w in wanted):
        wanted.insert(0, "AllSummary")
    model = model.remove_classes(["Output:Table:SummaryReports"])

    existing = {
        (o.get(0).upper(), o.get(1).upper(), o.get(2).upper())
        for o in model.by_class("Output:Variable")
    }

    def var(key: str, name: str, freq: str) -> List[str]:
        if (key.upper(), name.upper(), freq.upper()) in existing:
            return []
        return [format_object("Output:Variable", [key, name, freq])]

    additions = [
        format_object("Output:SQLite", ["SimpleAndTabular"]),
        format_object("Output:Table:SummaryReports", wanted),
    ]
    for name in variables:
        additions += var("*", name, "Hourly")
    for name in TIMESTEP_VARIABLES:
        additions += var("*", name, "Timestep")
    schedules = _referenced_schedules(model)
    if schedules:
        for sched in schedules:
            additions += var(sched, "Schedule Value", "Hourly")
    else:
        additions += var("*", "Schedule Value", "Hourly")
    for node in _supply_outlet_nodes(model):
        additions += var(node, "System Node Temperature", "Hourly")
        additions += var(node, "System Node Setpoint Temperature", "Hourly")
    header = "!- ===== Output requests added by eplus-prereview (no effect on results) ====="
    return model.append(header, *additions)


def needs_expand_objects(idf: Idf) -> bool:
    return idf.has_class_prefix("HVACTemplate:") or idf.has_class_prefix("GroundHeatTransfer:")


def run_model(
    idf_path: str,
    epw_path: str,
    out_dir: str,
    energyplus: Optional[str] = None,
    inject: bool = True,
    timeout_s: int = 3600,
    check_version: bool = True,
) -> RunResult:
    """Run one model into out_dir and return paths to the results."""
    exe = find_energyplus(energyplus)
    idf = Idf.from_file(idf_path)
    if check_version:
        model_v = idf.version()
        exe_v = energyplus_version(exe)
        if model_v and _major_minor(model_v) != _major_minor(exe_v):
            raise RunError(
                f"{os.path.basename(idf_path)} is EnergyPlus {model_v} but the executable is "
                f"{exe_v}. Run with a matching version (--energyplus) or transition the file "
                "with IDFVersionUpdater first."
            )
    os.makedirs(out_dir, exist_ok=True)
    model = inject_outputs(idf) if inject else idf
    run_idf = os.path.join(out_dir, "in.idf")
    model.write(run_idf)
    args = [exe, "-w", epw_path, "-d", out_dir]
    if needs_expand_objects(model):
        args.append("-x")
    args.append(run_idf)
    t0 = time.time()
    with open(os.path.join(out_dir, "prereview_run.log"), "w") as log:
        proc = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=timeout_s)
    elapsed = time.time() - t0
    sql = os.path.join(out_dir, "eplusout.sql")
    err = os.path.join(out_dir, "eplusout.err")
    return RunResult(
        out_dir=out_dir,
        idf_path=run_idf,
        returncode=proc.returncode,
        elapsed_s=elapsed,
        sql_path=sql if os.path.isfile(sql) else None,
        err_path=err if os.path.isfile(err) else None,
    )
