"""Seeded-fault lab: build clean proposed/baseline sets from DOE prototypes, seed one fault per
case, simulate, and score whether prereview finds each fault with the right rule, zone and cause.

    python -I tools/faultlab/lab.py build  LAB_DIR   # write IDFs
    python -I tools/faultlab/lab.py run    LAB_DIR   # simulate everything not yet simulated
    python -I tools/faultlab/lab.py score  LAB_DIR   # evaluate and write scorecard.{json,md}

Environment: PREREVIEW_ENERGYPLUS (executable), LAB_EPW (weather), LAB_DDY (design days),
LAB_PROTOTYPES (folder with ASHRAE901_*_STD2019_Denver.idf).
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # tools/ on the path for `faultlab`

from faultlab import mutate as M  # noqa: E402
from faultlab.prepare import prepare  # noqa: E402
from prereview.idf import Idf, format_object  # noqa: E402

EPW = os.environ.get("LAB_EPW", "/home/claude/data/weather_taipei/TWN_NOR_Taipei.589680_TMYx.epw")
DDY = os.environ.get("LAB_DDY", "/home/claude/data/weather_taipei/TWN_NOR_Taipei.589680_TMYx.ddy")
PROTO = os.environ.get("LAB_PROTOTYPES",
                       "/home/claude/opt/EnergyPlus-26.2.0-4bd7a1f26f-Linux-Ubuntu24.04-x86_64/ExampleFiles")
ALT_EPW = os.environ.get("LAB_ALT_EPW", os.path.join(os.path.dirname(PROTO), "WeatherData",
                                                     "USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw"))

# Prototype -> (heating sizing factor, cooling sizing factor) that keeps the clean model under
# 300 unmet hours in Taipei (found with prereview's own diagnosis: coil-limited, see docs).
BUILDINGS = {
    "OS": ("OfficeSmall", 1.25, 1.4),
    "RS": ("RetailStandalone", 1.25, 1.15),
    "OM": ("OfficeMedium", 1.25, 1.8),
    # chiller plant cases only (no clean baseline set): the prototype's chillers are hard-sized for
    # Denver; autosized here, then faults put the plant back out of reach
    "OL": ("OfficeLarge", 1.25, 1.3),
}
PROPOSED_ONLY = {"OL"}
LAB_SET = os.environ.get("LAB_SET", "humid")  # humid (Taipei, default) | cold (heating-side faults)
if LAB_SET == "cold":
    BUILDINGS = {k: v for k, v in BUILDINGS.items() if k in ("OS", "RS")}
ROTATIONS = (0, 90, 180, 270)
BASELINE_LPD_FACTOR = 1.1  # an allowed difference: baseline lighting power differs from proposed


@dataclass
class Expect:
    rule: str
    status: str
    model: Optional[str] = None   # "proposed", "B000", ... (None = any / pair-level)
    zone: Optional[str] = None    # top unmet-hours contributor
    cause: Optional[str] = None   # its primary cause


@dataclass
class Fault:
    id: str
    building: str
    target: str                    # proposed | B000 | none (reuses clean runs)
    title: str
    mutate: Optional[Callable[[Idf, Dict], Idf]]
    expect: List[Expect]
    weather: Optional[str] = None  # alternative weather for the target run
    baselines: Optional[List[str]] = None   # which clean baselines to pair with (labels)
    project: Dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- helpers
def _always_off(idf: Idf) -> Idf:
    if idf.find("Schedule:Constant", "LAB_ALWAYS_OFF") is None:
        idf = idf.append(format_object("Schedule:Constant", ["LAB_ALWAYS_OFF", "", "0"]))
    return idf


def _replace_schedule(idf: Idf, name: str, type_limits: str, lines: List[str]) -> Idf:
    """Replace a schedule (any type) by a Schedule:Compact with the same name."""
    for cls in ("Schedule:Compact", "Schedule:Year", "Schedule:Constant", "Schedule:File"):
        o = idf.find(cls, name)
        if o is not None:
            idf = idf.remove([o])
    return idf.append(M.compact_schedule(name, type_limits, lines))


def _copy_schedule_as(idf: Idf, name: str, new_name: str, type_limits: str, lines: List[str]) -> Idf:
    return idf.append(M.compact_schedule(new_name, type_limits, lines))


def _design_size(meta: Dict, comp: str, desc: str) -> float:
    return meta["sizes"][(comp.upper(), desc)]


def _set_cooling_capacity(idf: Idf, coil: str, value: float) -> Idf:
    return M.set_field(idf, "Coil:Cooling:DX:SingleSpeed", coil, 2, f"{value:.1f}")


OCC_START_LATE = ["Through: 12/31", "For: Weekdays SummerDesignDay WinterDesignDay", "Until: 11:00", "0",
                  "Until: 22:00", "1", "Until: 24:00", "0", "For: Saturday", "Until: 11:00", "0",
                  "Until: 18:00", "1", "Until: 24:00", "0", "For: AllOtherDays", "Until: 24:00", "0"]
HVAC_24_7 = ["Through: 12/31", "For: AllDays", "Until: 24:00", "1"]
CLG_25 = ["Through: 12/31", "For: Weekdays SummerDesignDay", "Until: 06:00", "29.44", "Until: 22:00", "25.0",
          "Until: 24:00", "29.44", "For: Saturday", "Until: 06:00", "29.44", "Until: 18:00", "25.0",
          "Until: 24:00", "29.44", "For: WinterDesignDay", "Until: 24:00", "29.44", "For: AllOtherDays",
          "Until: 24:00", "29.44"]
CLG_DEEP_SETUP = ["Through: 12/31", "For: Weekdays", "Until: 06:00", "40.0", "Until: 18:00", "23.89",
                  "Until: 24:00", "40.0", "For: SummerDesignDay", "Until: 24:00", "23.89",
                  "For: AllOtherDays", "Until: 24:00", "40.0"]
HVAC_8_18 = ["Through: 12/31", "For: Weekdays SummerDesignDay WinterDesignDay", "Until: 08:00", "0",
             "Until: 18:00", "1", "Until: 24:00", "0", "For: AllOtherDays", "Until: 24:00", "0"]
HTG_DEEP_SETBACK = ["Through: 12/31", "For: Weekdays", "Until: 06:00", "12.0", "Until: 22:00", "21.0",
                    "Until: 24:00", "12.0", "For: SummerDesignDay", "Until: 24:00", "12.0",
                    "For: Saturday", "Until: 06:00", "12.0", "Until: 18:00", "21.0", "Until: 24:00", "12.0",
                    "For: WinterDesignDay", "Until: 24:00", "21.0", "For: AllOtherDays", "Until: 24:00", "12.0"]
FLAT_HALF = ["Through: 12/31", "For: Weekdays SummerDesignDay WinterDesignDay", "Until: 08:00", "0.1",
             "Until: 18:00", "0.5", "Until: 24:00", "0.1", "For: AllOtherDays", "Until: 24:00", "0.1"]
LIGHT_ALT = ["Through: 12/31", "For: Weekdays SummerDesignDay WinterDesignDay", "Until: 07:00", "0.05",
             "Until: 19:00", "0.8", "Until: 24:00", "0.05", "For: AllOtherDays", "Until: 24:00", "0.05"]


def _people_sched(idf: Idf, zone_people: str, new: str) -> Idf:
    idf = idf.append(M.compact_schedule(new, "Fraction", FLAT_HALF))
    return M.set_field(idf, "People", zone_people, 2, new)


def _run_period(idf: Idf, end_month: int, end_day: int) -> Idf:
    idf = idf.remove_classes(["RunPeriod"])
    return idf.append(format_object("RunPeriod", ["Lab", "1", "1", "", str(end_month), str(end_day), "",
                                                  "Sunday", "No", "No", "No", "Yes", "Yes", "No"]))


def _denver_design_days(idf: Idf, proto_path: str) -> Idf:
    src = Idf.from_file(proto_path)
    days = [o for o in src.by_class("SizingPeriod:DesignDay")]
    loc = src.first("Site:Location")
    idf = idf.remove_classes(["SizingPeriod:DesignDay", "Site:Location"])
    return idf.append(*([format_object(loc.cls, loc.fields)] + [format_object(d.cls, d.fields) for d in days]))


def _remove_one_window(idf: Idf) -> Idf:
    wins = [o for o in idf.by_class("FenestrationSurface:Detailed") if o.get(1).upper() == "WINDOW"]
    return idf.remove([wins[0]])


HTG_SETBACK_10 = ["Through: 12/31", "For: Weekdays", "Until: 06:00", "10.0", "Until: 19:00", "21.0",
                  "Until: 24:00", "10.0", "For: WinterDesignDay", "Until: 24:00", "21.0",
                  "For: AllOtherDays", "Until: 24:00", "10.0"]


def _mild_heating_design_day(idf: Idf) -> Idf:
    for o in idf.by_class("SizingPeriod:DesignDay"):
        if "HTG" in o.name.upper():
            idf = idf.set_field(o, 4, "2.0")
    return idf


def cold_faults() -> List[Fault]:
    P = "proposed"
    RS_COIL1 = "PSZ-AC:1_Unitary_Package_HeatCoil"
    nom = "Design Size Nominal Capacity"
    return [
        Fault("C01", "RS", P, "Furnace hard-sized at 40% of design (Back_Space)",
              lambda m, x: M.set_field(m, "Coil:Heating:Fuel", RS_COIL1, 4,
                                       f"{0.4 * _design_size(x, RS_COIL1, nom):.1f}"),
              # retail has high internal gains: few occupied heating hours even at 40% -- the diagnosis is the test
              [Expect("UMLH-300", "*", P, zone="BACK_SPACE", cause="hard_sized_below_design")]),
        Fault("C02", "RS", P, "Furnace never available (Front_Retail)",
              lambda m, x: M.set_field(_always_off(m), "Coil:Heating:Fuel", "PSZ-AC:4_Unitary_Package_HeatCoil", 1,
                                       "LAB_ALWAYS_OFF"),
              [Expect("UMLH-300", "FAIL", P, zone="FRONT_RETAIL", cause="never_delivers")]),
        Fault("C03", "OS", P, "Heating sized on a +2 °C design day instead of the 99.6% condition",
              lambda m, x: _mild_heating_design_day(m),
              [Expect("UMLH-300", "*", P, cause="beyond_design_day"), Expect("SIZE-DD", "FAIL", P)]),
        Fault("C04", "OS", P, "10 °C night setback with no night cycling (morning warm-up)",
              lambda m, x: M.set_field_all(_replace_schedule(m, "HTGSETP_SCH_NO_OPTIMUM", "Temperature",
                                                             HTG_SETBACK_10), "AvailabilityManager:NightCycle", 3,
                                           "StayOff"),
              [Expect("UMLH-300", "*", P, cause="morning_recovery"), Expect("CONS-TSTAT-HTG", "FAIL")]),
        Fault("C05", "OS", P, "Heat pump backup heater hard-sized at 20% (Perimeter_ZN_1)",
              lambda m, x: M.set_field(m, "Coil:Heating:Fuel", "PSZ-AC:2 Heat Pump DX Supp Heating Coil", 4,
                                       f"{0.2 * _design_size(x, 'PSZ-AC:2 Heat Pump DX Supp Heating Coil', nom):.1f}"),
              [Expect("UMLH-300", "*", P, zone="PERIMETER_ZN_1", cause="hard_sized_below_design")]),
    ]


# ----------------------------------------------------------------------------- the faults
def faults() -> List[Fault]:
    if LAB_SET == "cold":
        return cold_faults()
    P, B0 = "proposed", "B000"
    OS_COIL2 = "PSZ-AC:2 Heat Pump DX Cooling Coil"
    OS_COIL1 = "PSZ-AC:1 Heat Pump DX Cooling Coil"
    cap = "Design Size Gross Rated Total Cooling Capacity"
    return [
        # -- simulation health
        Fault("F01", "OS", P, "Partial-year run period (Jan–Jun)", lambda m, x: _run_period(m, 6, 30),
              [Expect("HEALTH-YEAR", "FAIL", P)]),
        Fault("F02", "OS", P, "Invalid keyword (Building terrain)", lambda m, x: M.set_field(
            m, "Building", m.first("Building").name, 2, "Suburbz"), [Expect("HEALTH-RUN", "FAIL", P)]),
        Fault("F03", "OS", P, "People schedule that does not exist", lambda m, x: M.set_field(
            m, "People", "Core_ZN", 2, "BLDG_OCC_SCH_TYPO"), [Expect("HEALTH-RUN", "FAIL", P)]),
        Fault("F04", "OS", P, "Window larger than its wall (multiplier 8)", lambda m, x: M.set_field(
            m, "FenestrationSurface:Detailed", "Perimeter_ZN_1_wall_south_Window_1", 7, "8"),
              [Expect("HEALTH-RUN", "FAIL", P)]),
        Fault("F05", "OS", P, "Warm-up limited to 1 day (no convergence)", lambda m, x: M.set_field(
            M.set_field(m, "Building", m.first("Building").name, 6, "1"), "Building", m.first("Building").name,
            7, "1"), [Expect("HEALTH-SEVERE", "FAIL", P)]),
        Fault("F06", "OS", P, "Design days from another climate (Denver) with Taipei weather",
              lambda m, x: _denver_design_days(m, x["proto"]),
              [Expect("SIZE-DD", "FAIL", P), Expect("UMLH-300", "FAIL", P, cause="beyond_design_day")]),
        # -- unmet load hours
        Fault("F07", "OS", P, "Cooling coil hard-sized at 50% of design (Perimeter_ZN_1)",
              lambda m, x: _set_cooling_capacity(m, OS_COIL2, 0.5 * _design_size(x, OS_COIL2, cap)),
              [Expect("UMLH-300", "FAIL", P, zone="PERIMETER_ZN_1", cause="hard_sized_below_design")]),
        Fault("F08", "OS", P, "Cooling coil never available (Perimeter_ZN_2)",
              lambda m, x: M.set_field(_always_off(m), "Coil:Cooling:DX:SingleSpeed",
                                       "PSZ-AC:3 Heat Pump DX Cooling Coil", 1, "LAB_ALWAYS_OFF"),
              [Expect("UMLH-300", "FAIL", P, zone="PERIMETER_ZN_2", cause="never_delivers")]),
        Fault("F09", "OS", P, "HVAC starts at 11:00, after occupancy (stay off at night)",
              lambda m, x: M.set_field_all(_replace_schedule(m, "HVACOperationSchd", "On/Off", OCC_START_LATE),
                                           "AvailabilityManager:NightCycle", 3, "StayOff"),
              [Expect("UMLH-300", "FAIL", P, cause="system_off"), Expect("CONS-HVAC-SCHED", "FAIL")]),
        Fault("F10", "OS", P, "No night cycling and 40 °C setup: HVAC starts at occupancy into a hot building",
              lambda m, x: M.set_field_all(_replace_schedule(
                  m, "CLGSETP_SCH_NO_OPTIMUM", "Temperature", CLG_DEEP_SETUP),
                  "AvailabilityManager:NightCycle", 3, "StayOff"),
              # mild on purpose: total stays under 300 h, but the diagnosis must still name the cause
              [Expect("UMLH-300", "PASS", P, cause="morning_recovery"), Expect("CONS-TSTAT-CLG", "FAIL")]),
        Fault("F32", "OS", P, "Cooling sized with no margin (sizing factor 1.0) in a humid climate",
              lambda m, x: m.set_field(m.by_class("Sizing:Parameters")[0], 1, "1.0"),
              [Expect("UMLH-300", "FAIL", P, cause="supply_air_not_at_setpoint")]),
        Fault("F33", "OL", P, "Chiller chilled-water flow hard-sized at 24% of design (left over from the "
              "Denver model)", lambda m, x: _chw_flow_hard(m, "0.0283687"),
              [Expect("UMLH-300", "*", P, cause="hard_sized_below_design")], baselines=[]),
        Fault("F11", "OM", P, "Zone airflow sized for 6 °C supply air while the system supplies 12.8 °C "
              "(Perimeter_bot_ZN_1)",
              lambda m, x: M.set_field(m, "Sizing:Zone", "Perimeter_bot_ZN_1", 2, "6.0"),
              [Expect("UMLH-300", "FAIL", P, zone="PERIMETER_BOT_ZN_1", cause="terminal_at_max_airflow")]),
        Fault("F12", "OS", P, "Compliant: proposed 166 h vs baselines ~70 h (no 50-hour rule since 90.1-2010)",
              lambda m, x: _set_cooling_capacity(m, OS_COIL1, 0.9 * _design_size(x, OS_COIL1, cap)),
              [Expect("UMLH-300", "PASS", P), Expect("UMLH-DIFF", "INFO")]),
        Fault("F13", "OS", B0, "Baseline coil hard-sized at 50% (Perimeter_ZN_1)",
              lambda m, x: _set_cooling_capacity(m, OS_COIL2, 0.5 * _design_size(x, OS_COIL2, cap)),
              [Expect("UMLH-300", "FAIL", B0, zone="PERIMETER_ZN_1", cause="hard_sized_below_design")]),
        # -- baseline vs proposed must-match
        Fault("F14", "OS", B0, "Baseline occupancy +25% in Perimeter_ZN_1",
              lambda m, x: M.scale_level(m, "People", 1.25, "Perimeter_ZN_1"),
              [Expect("CONS-PEOPLE", "FAIL", zone="PERIMETER_ZN_1")]),
        Fault("F15", "OS", B0, "Baseline occupancy schedule changed (Core_ZN)",
              lambda m, x: _people_sched(m, "Core_ZN", "LAB_OCC_ALT"),
              [Expect("CONS-PEOPLE", "FAIL", zone="CORE_ZN")]),
        Fault("F16", "OS", B0, "Baseline plug loads −20%", lambda m, x: M.scale_level(m, "ElectricEquipment", 0.8),
              [Expect("CONS-PLUG", "FAIL"), Expect("CONS-EQUIP-TOTAL", "FAIL")]),
        Fault("F17", "OS", B0, "Baseline plug-load schedule changed",
              lambda m, x: _replace_schedule(m, "BLDG_EQUIP_SCH", "Fraction", FLAT_HALF),
              [Expect("CONS-PLUG", "FAIL")]),
        Fault("F18", "OS", B0, "Baseline cooling setpoint 25 °C instead of 24 °C",
              lambda m, x: _replace_schedule(m, "CLGSETP_SCH_NO_OPTIMUM", "Temperature", CLG_25),
              [Expect("CONS-TSTAT-CLG", "FAIL")]),
        Fault("F19", "OS", B0, "Baseline heating setback deeper",
              lambda m, x: _replace_schedule(m, "HTGSETP_SCH_NO_OPTIMUM", "Temperature", HTG_DEEP_SETBACK),
              [Expect("CONS-TSTAT-HTG", "FAIL")]),
        Fault("F20", "OS", B0, "Baseline infiltration +50%",
              lambda m, x: M.scale_level(m, "ZoneInfiltration:DesignFlowRate", 1.5),
              [Expect("CONS-INFIL", "REVIEW")]),
        Fault("F21", "OS", B0, "Baseline lighting schedule changed",
              lambda m, x: _replace_schedule(m, "BLDG_LIGHT_SCH", "Fraction", LIGHT_ALT),
              [Expect("CONS-LSCHED", "REVIEW")]),
        Fault("F22", "OS", B0, "Baseline outdoor air +30%",
              lambda m, x: M.set_field_all(m, "DesignSpecification:OutdoorAir", 3, "0.000561305"),
              [Expect("CONS-OA", "REVIEW")]),
        Fault("F23", "OS", B0, "Baseline simulated with another weather file", None,
              [Expect("CONS-SITE", "FAIL")], weather=ALT_EPW),
        Fault("F24", "OS", B0, "Baseline zone multiplier 2 (Perimeter_ZN_4)",
              lambda m, x: M.set_field(m, "Zone", "Perimeter_ZN_4", 6, "2"), [Expect("CONS-ZONES", "FAIL")]),
        Fault("F25", "OS", B0, "Gas equipment only in the baseline (Core_ZN)",
              lambda m, x: M.add(m, "GasEquipment", ["LAB Core gas", "Core_ZN", "BLDG_EQUIP_SCH", "Watts/Area", "",
                                                     "2.0", "", "0", "0.3", "0"]),
              [Expect("CONS-GAS-EQUIP", "FAIL", zone="CORE_ZN")]),
        Fault("F26", "OS", B0, "Baseline HVAC runs 24/7",
              lambda m, x: _replace_schedule(m, "HVACOperationSchd", "On/Off", HVAC_24_7),
              [Expect("CONS-HVAC-SCHED", "FAIL")]),
        # -- 90.1 baseline rules (Tier 2)
        Fault("F27", "OS", "none", "Only one baseline orientation submitted", None,
              [Expect("BASE-ROTATION", "FAIL")], baselines=["B000"]),
        Fault("F28", "OS", "none", "Baseline orientations 0/90/90/270 (180 missing)", None,
              [Expect("BASE-ROTATION", "FAIL")], baselines=["B000", "B090", "B090", "B270"]),
        Fault("F29", "OS", B0, "Baseline window area differs from proposed (one window removed)",
              lambda m, x: _remove_one_window(m), [Expect("BASE-WWR", "FAIL")]),
        Fault("F30", "OS", "none", "Heat pump baseline for a fossil-fuel heated building", None,
              [Expect("BASE-SYSTEM", "FAIL")], project={"heating_source": "fossil"}),
        Fault("F31", "RS", "none", "Furnace baseline for an electrically heated building", None,
              [Expect("BASE-SYSTEM", "FAIL")], project={"heating_source": "electric"}),
        # -- cases taken from the standard's text (independent review): 90.1-2016 differences
        Fault("F35", "RS", "none", "90.1-2016, climate zone 2A: furnace baseline where Table G3.1.1-3 needs a heat "
              "pump (System 4)", None, [Expect("BASE-SYSTEM", "FAIL")],
              project={"edition": "2016", "climate_zone": "2A", "building_type": "retail"}),
        Fault("F36", "OS", "none", "90.1-2016: baseline at the proposed WWR — Table G3.1.1-1 may require another "
              "value (offices 5,000–50,000 ft²: 31%)", None, [Expect("BASE-WWR-2016", "REVIEW")],
              project={"edition": "2016", "climate_zone": "2A"}),
    ]


# Findings on the clean sets that are TRUE (the DOE prototypes are not Appendix G baselines); they are
# reported in the scorecard but not counted as false alarms.
KNOWN_TRUE = {
    "OM": {"BASE-SYSTEM": "The prototype's packaged VAV uses electric reheat; System 5 requires hot-water reheat "
                          "from a fossil boiler, so the clean baseline is genuinely not System 5."},
}

CLEAN_PROJECT = {
    "OS": {"heating_source": "electric", "building_type": "nonresidential", "floors_above_grade": 1},
    "RS": {"heating_source": "fossil", "building_type": "nonresidential", "floors_above_grade": 1},
    "OM": {"heating_source": "fossil", "building_type": "nonresidential", "floors_above_grade": 3},
}


# ----------------------------------------------------------------------------- build / run
def _autosize_chillers(m: Idf) -> Idf:
    for name in [o.name for o in m.by_class("Chiller:Electric:ReformulatedEIR")]:
        m = m.set_field(m.find("Chiller:Electric:ReformulatedEIR", name), 1, "Autosize")
        m = m.set_field(m.find("Chiller:Electric:ReformulatedEIR", name), 5, "Autosize")
    return m


def _chw_flow_hard(m: Idf, value: str) -> Idf:
    for name in [o.name for o in m.by_class("Chiller:Electric:ReformulatedEIR")]:
        m = m.set_field(m.find("Chiller:Electric:ReformulatedEIR", name), 5, value)
    return m


def _clean_paths(lab: str, b: str) -> Dict[str, str]:
    out = {"proposed": os.path.join(lab, "idf", b, "proposed.idf")}
    if b in PROPOSED_ONLY:
        return out
    for r in ROTATIONS:
        out[f"B{r:03d}"] = os.path.join(lab, "idf", b, f"B{r:03d}.idf")
    return out


def _meta(lab: str, b: str) -> Dict:
    """Design sizes from the clean proposed run (needed to hard-size components relative to design)."""
    from prereview.sqlout import SqlResults
    sql = os.path.join(lab, "runs", b, "proposed", "eplusout.sql")
    sizes = {}
    if os.path.isfile(sql):
        for r in SqlResults(sql).component_sizes():
            sizes[(r["name"].upper(), r["desc"])] = r["value"]
    return {"sizes": sizes, "proto": os.path.join(PROTO, f"ASHRAE901_{BUILDINGS[b][0]}_STD2019_Denver.idf")}


def build(lab: str, stage: str = "clean") -> None:
    for b, (proto, fh, fc) in BUILDINGS.items():
        paths = _clean_paths(lab, b)
        os.makedirs(os.path.dirname(paths["proposed"]), exist_ok=True)
        if stage == "clean":
            prep = paths["proposed"] + ".prep"
            prepare(os.path.join(PROTO, f"ASHRAE901_{proto}_STD2019_Denver.idf"), DDY, prep)
            m = Idf.from_file(prep)
            # the prototypes stop warm-up at 15-25 days; in some climates that leaves severe
            # warm-up convergence errors (prereview flags them) -- apply the fix it recommends
            bld = m.first("Building")
            m = m.set_field(bld, 6, "50")
            sp = m.by_class("Sizing:Parameters")[0]
            m = m.set_field(sp, 0, str(fh))
            m = m.set_field(m.by_class("Sizing:Parameters")[0], 1, str(fc))
            if b == "OL":
                m = _autosize_chillers(m)
            m.write(paths["proposed"])
            os.remove(prep)
            if b in PROPOSED_ONLY:
                continue
            for r in ROTATIONS:
                bl = M.rotate(M.scale_level(m, "Lights", BASELINE_LPD_FACTOR), r)
                bl.write(paths[f"B{r:03d}"])
    if stage == "faults":
        for f in faults():
            if f.target == "none":
                continue
            src = _clean_paths(lab, f.building)[f.target]
            m = Idf.from_file(src)
            if f.mutate is not None:
                m = f.mutate(m, _meta(lab, f.building))
            m.write(os.path.join(lab, "idf", "faults", f"{f.id}.idf"))


def _job(args):
    from prereview.runner import run_model
    idf, out, epw = args
    try:
        r = run_model(idf, epw, out)
        return out, r.returncode, round(r.elapsed_s, 1)
    except Exception as exc:  # noqa: BLE001
        return out, -1, str(exc)


def run(lab: str, stage: str = "clean") -> None:
    jobs = []
    if stage == "clean":
        for b in BUILDINGS:
            for label, idf in _clean_paths(lab, b).items():
                out = os.path.join(lab, "runs", b, label)
                if not os.path.isfile(os.path.join(out, "eplusout.err")):
                    jobs.append((idf, out, EPW))
    else:
        for f in faults():
            if f.target == "none":
                continue
            out = os.path.join(lab, "runs", "faults", f.id)
            if not os.path.isfile(os.path.join(out, "eplusout.err")):
                jobs.append((os.path.join(lab, "idf", "faults", f"{f.id}.idf"), out, f.weather or EPW))
    with ProcessPoolExecutor(int(os.environ.get("LAB_JOBS", "2"))) as ex:
        for res in ex.map(_job, jobs):
            print(*res, flush=True)


if __name__ == "__main__":
    cmd, lab = sys.argv[1], sys.argv[2]
    stage = sys.argv[3] if len(sys.argv) > 3 else "clean"
    if cmd == "build":
        build(lab, stage)
    elif cmd == "run":
        run(lab, stage)
    elif cmd == "score":
        from faultlab.score import score
        score(lab)
    else:
        raise SystemExit(__doc__)
