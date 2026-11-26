"""Unmet load hours: the check reviewers apply, and the diagnosis modelers need.

Definition used by 90.1: an unmet load hour is an hour in which one or more zones is
outside the thermostat setpoint range. EnergyPlus reports occupied heating and occupied
cooling hours separately (and the LEED summary adds them). prereview reports both the
numbers teams usually submit and the exact count of hours, when hourly output is present.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model import ModelRun
from ..sqlout import to_float
from ..topology import air_loop_components, plant_loops, plants_serving, zone_services
from .base import FAIL, INFO, PASS, REVIEW, SKIPPED, Context, Finding

VAR_H = "Zone Heating Setpoint Not Met While Occupied Time"
VAR_C = "Zone Cooling Setpoint Not Met While Occupied Time"
VAR_QH = "Zone Air System Sensible Heating Rate"
VAR_QC = "Zone Air System Sensible Cooling Rate"
VAR_T = "Zone Mean Air Temperature"
VAR_SPH = "Zone Thermostat Heating Setpoint Temperature"
VAR_SPC = "Zone Thermostat Cooling Setpoint Temperature"
VAR_OCC = "Zone People Occupant Count"
VAR_ODB = "Site Outdoor Air Drybulb Temperature"
VAR_OWB = "Site Outdoor Air Wetbulb Temperature"
VAR_DAMPER = "Zone Air Terminal VAV Damper Position"
VAR_NODE_T = "System Node Temperature"
VAR_NODE_SP = "System Node Setpoint Temperature"
VAR_FAC_H = "Facility Heating Setpoint Not Met While Occupied Time"
VAR_FAC_C = "Facility Cooling Setpoint Not Met While Occupied Time"

LIMIT = 300.0
DIFF_LIMIT = 50.0

SOURCE_TEXT = ("“Unmet load hours for the proposed design or baseline building designs shall not exceed "
               "300 (of the 8760 hours simulated), and unmet load hours for the proposed design shall not "
               "exceed the number of unmet load hours for the baseline building design by more than 50.” "
               "(text quoted in ASHRAE interpretation IC 90.1-2007-03)")
CLAUSES = {
    "2010": "ASHRAE 90.1-2010 Appendix G — equipment capacities / unmet load hours (G3.1.2.2)",
    "2016": "ASHRAE 90.1-2016 Appendix G — unmet load hours (G3.1.2.3)",
}

# Root-cause categories, in the order they are preferred as the "primary" explanation.
NO_DELIVERY = "never_delivers"
SYSTEM_OFF = "system_off"
RECOVERY = "morning_recovery"
HARD_SIZED = "hard_sized_below_design"
COIL_LIMITED = "supply_air_not_at_setpoint"
PLANT_LIMITED = "plant_not_at_setpoint"
AIRFLOW_LIMITED = "terminal_at_max_airflow"
DESIGN_BASIS = "beyond_design_day"
AT_CAPACITY = "capacity_limited"
PARTIAL = "partial_output"
MARGINAL = "marginal_misses"

CAUSE_TEXT = {
    NO_DELIVERY: "The system never delivered {mode} to this zone all year.",
    SYSTEM_OFF: "Unmet while the system delivered no {mode} (system off or unavailable while occupied).",
    RECOVERY: "Unmet hours cluster in the first two occupied hours of the day (setback recovery).",
    HARD_SIZED: "Capacity-limited, and a component serving the zone is hard-sized below its design size.",
    COIL_LIMITED: "The air system could not hold its supply-air temperature setpoint (coil capacity).",
    PLANT_LIMITED: "The {water} plant serving the zone could not hold its supply-water temperature setpoint.",
    AIRFLOW_LIMITED: "The zone's terminal was at maximum airflow (zone design airflow too low).",
    DESIGN_BASIS: "Capacity-limited when outdoor conditions exceed the {mode} design day used for sizing.",
    AT_CAPACITY: "Capacity-limited: the system was at or near its peak output while the zone missed setpoint.",
    PARTIAL: "The system ran below its peak while the zone missed setpoint (control or terminal limits).",
    MARGINAL: "Misses are small: the zone was within 0.5 K of setpoint in most unmet hours.",
}

def _cause_text(cause: str, mode: str) -> str:
    return CAUSE_TEXT[cause].format(mode=mode, water="chilled-water" if mode == "cooling" else "hot-water")


CAUSE_FIXES = {
    NO_DELIVERY: [
        "Check that the zone is connected to a system with {mode} equipment and that the equipment's "
        "availability schedule is not always off.",
        "Every conditioned zone needs both heating and cooling in the baseline (Table G3.1.1B systems).",
    ],
    SYSTEM_OFF: [
        "Align the HVAC availability / fan schedule with the occupancy schedule, or add night-cycle or "
        "optimum-start control. HVAC operation schedules must be identical in both models (Table G3.1 #4).",
    ],
    RECOVERY: [
        "Start the system earlier or use an optimum-start availability manager, and check the setback setpoints.",
        "Make the same change in both models so the comparison stays fair.",
    ],
    HARD_SIZED: [
        "[baseline] Autosize the component: baseline capacities must come from the sizing run (Appendix G "
        "equipment capacities, oversized 15% cooling / 25% heating). Hard-sized baseline equipment below the "
        "sizing result is a common review comment.",
        "[proposed] If this value is a leftover from an earlier option, autosize it or enter the capacity on the "
        "design drawings/schedules.",
        "[proposed] If it is the as-designed capacity, the design cannot hold this zone at setpoint: raise it "
        "with the design team, or document why the unmet hours are acceptable.",
    ],
    COIL_LIMITED: [
        "Check the {mode} coil capacity of the air system serving the zone against its peak load; "
        "check sizing factors and the system sizing method (coincident vs non-coincident).",
        "For DX cooling in hot-humid climates, check the coil's sensible heat ratio and rated capacity at the "
        "design wet-bulb; latent load can use up capacity the sensible load needs.",
        "[baseline] Baseline capacities must come from the sizing run, oversized 15% (cooling) / 25% (heating).",
    ],
    PLANT_LIMITED: [
        "Check the capacity of the plant equipment (chillers/boilers) against the coincident coil loads, the plant "
        "equipment operation scheme (load ranges that cap the equipment that can run) and Sizing:Plant.",
        "If the plant has a primary/secondary arrangement or heat exchanger, check its capacity and setpoints.",
        "[baseline] Baseline plant capacities come from the sizing run (Appendix G): check the plant is autosized "
        "and the number of chillers/boilers follows G3.1.3.",
    ],
    AIRFLOW_LIMITED: [
        "Raise the zone design airflow: check Sizing:Zone supply-air temperature (or temperature difference), "
        "the zone sizing factor and any hard-sized terminal maximum airflow.",
        "If the supply-air temperature is reset upward, the terminal may need more airflow than it was sized for.",
    ],
    DESIGN_BASIS: [
        "Check that the design days come from the project location and use the percentile the standard "
        "requires (90.1-2010 baseline sizing: 99.6% heating, 1% cooling dry-bulb and wet-bulb).",
        "In hot-humid climates DX coils lose sensible capacity at high wet-bulb; compare the coil's sensible "
        "capacity with the zone's peak sensible load.",
        "[baseline] Baseline capacities are oversized 15% for cooling and 25% for heating; confirm the sizing "
        "factors.",
    ],
    AT_CAPACITY: [
        "Check zone and system sizing factors, the zone design supply-air temperature difference and the "
        "coil capacities serving the zone.",
        "Look for loads that the design days miss (e.g. high internal gains on non-design days).",
    ],
    PARTIAL: [
        "Check VAV terminal minimum/maximum flows, reheat coil capacity and supply-air temperature reset or "
        "setpoint-manager limits for the system serving the zone.",
        "Check for thermostat schedules that step faster than the system can respond.",
    ],
    MARGINAL: [
        "Misses are small; check the thermostat throttling range and EnergyPlus reporting tolerance "
        "(OutputControl:ReportingTolerances) are consistent with the controls being modeled, and document it.",
    ],
}


@dataclass
class ZoneModeDiagnosis:
    zone: str
    mode: str  # heating | cooling
    hours: float           # occupied unmet hours for this zone and mode
    share: float           # share of the facility's unmet hours in which this zone-mode is unmet
    counts: Dict[str, int] = field(default_factory=dict)
    cause: str = ""
    evidence: List[str] = field(default_factory=list)
    hard_sizing: List[Dict[str, object]] = field(default_factory=list)
    mean_miss_k: Optional[float] = None
    served_by: List[str] = field(default_factory=list)


@dataclass
class UnmetStats:
    reported_heating: Optional[float]
    reported_cooling: Optional[float]
    reported_leed: Optional[float]
    union_hours: Optional[float]      # hours with any zone unmet (heating or cooling), occupied
    union_heating: Optional[float]
    union_cooling: Optional[float]
    zone_hours: List[Tuple[str, float, float]]  # (zone, occupied heating h, occupied cooling h)
    hourly: bool
    diagnosis: List[ZoneModeDiagnosis] = field(default_factory=list)
    union_matches_reported: Optional[bool] = None

    @property
    def reported_sum(self) -> Optional[float]:
        if self.reported_leed is not None:
            return self.reported_leed
        if self.reported_heating is None and self.reported_cooling is None:
            return None
        return (self.reported_heating or 0.0) + (self.reported_cooling or 0.0)

    @property
    def judged(self) -> Optional[float]:
        """The number the 300-hour rule is judged on: exact union if known, else the reported sum."""
        if self.union_hours is not None:
            return float(self.union_hours)
        return self.reported_sum


# ----------------------------------------------------------------------------------
def _facility_reported(model: ModelRun) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    s = model.sql
    rh = s.tab_float("AnnualBuildingUtilityPerformanceSummary", "Comfort and Setpoint Not Met Summary",
                     "Time Setpoint Not Met During Occupied Heating", "Facility")
    rc = s.tab_float("AnnualBuildingUtilityPerformanceSummary", "Comfort and Setpoint Not Met Summary",
                     "Time Setpoint Not Met During Occupied Cooling", "Facility")
    leed = s.tab_float("LEEDsummary", "EAp2-2. Advisory Messages", "Number of hours not met", "Data")
    return rh, rc, leed


def _zone_hours_tabular(model: ModelRun) -> List[Tuple[str, float, float]]:
    t = model.sql.tabular("SystemSummary", "Time Setpoint Not Met")
    rows = []
    for zone, cols in t.items():
        if zone.strip().upper() == "FACILITY":
            continue
        h = to_float(cols.get("During Occupied Heating", ("", ""))[0]) or 0.0
        c = to_float(cols.get("During Occupied Cooling", ("", ""))[0]) or 0.0
        rows.append((zone, h, c))
    rows.sort(key=lambda r: -(r[1] + r[2]))
    return rows


def _design_days(model: ModelRun) -> List[Dict[str, object]]:
    t = model.sql.tabular("ClimaticDataSummary", "SizingPeriod:DesignDay")
    out = []
    for name, cols in t.items():
        out.append({
            "name": name.strip(),
            "max_db": to_float(cols.get("Maximum Dry Bulb", ("", ""))[0]),
            "humidity": to_float(cols.get("Humidity Value", ("", ""))[0]),
            "humidity_type": cols.get("Humidity Type", ("", ""))[0],
        })
    return out


def _is_heating_dd(name: str) -> bool:
    n = name.upper()
    return "HTG" in n or "HEAT" in n or "99.6%" in n or "99%" in n


_CAPACITY_WORDS = ("capacity", "maximum air flow", "maximum flow", "supply air flow", "design air flow",
                   "rated air flow", "hot water flow", "reheat water", "design water flow", "maximum water flow",
                   "air flow rate", "water flow rate", "fluid flow rate", "design size")
_NOT_CAPACITY_WORDS = ("minimum", "fraction", "ratio", "outdoor", "no load", "sensible heat ratio",
                       "fan power", "pressure", "efficiency", "cop", "temperature", "per floor area",
                       "electric power", "pump", "speed 1", "low speed")


def _relevant_field(field_name: str, mode: str) -> bool:
    f = field_name.lower()
    if any(w in f for w in _NOT_CAPACITY_WORDS):
        return False
    if not any(w in f for w in _CAPACITY_WORDS):
        return False
    heat_words = ("heat", "reheat", "hot water", "furnace", "boiler")
    cool_words = ("cool", "chilled", "evaporat", "total cooling")
    if mode == "cooling" and any(w in f for w in heat_words):
        return False
    if mode == "heating" and any(w in f for w in cool_words):
        return False
    return True


def _relevant_component(ctype: str, mode: str) -> bool:
    t = ctype.upper()
    if mode == "cooling" and ("HEATING" in t or "REHEAT" in t and "COIL" in t or "BOILER" in t):
        return False
    if mode == "heating" and ("COOLING" in t or "CHILLER" in t or "TOWER" in t):
        return False
    return True


def _hard_sizing_for_zone(model: ModelRun, zone: str, mode: str, services, loops,
                          plant_equipment: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, object]]:
    """Components serving the zone whose user-specified capacity or flow is below the design size."""
    zone_u = zone.upper()
    n_zones: Dict[str, int] = defaultdict(int)
    for zsvc in services.values():
        for loop in zsvc.air_loops:
            n_zones[loop.upper()] += 1
    svc = services.get(zone_u)
    names = set()
    if svc is not None:
        names |= {n.upper() for n in svc.terminals | svc.zone_equipment}
        for loop in svc.air_loops:
            names.add(loop.upper())
            names |= {c["name"].upper() for c in loops.get(loop.upper(), [])}
    names |= {e["name"].upper() for e in plant_equipment or []}
    by_comp: Dict[Tuple[str, str], Dict[str, Tuple[float, str]]] = defaultdict(dict)
    for row in model.sql.component_sizes():
        cname = (row["name"] or "").upper()
        if cname in names or (zone_u and zone_u in cname):
            by_comp[(row["type"], row["name"])][row["desc"]] = (row["value"], row["units"])
    found = []
    single_zone = svc is not None and all(n_zones.get(loop.upper(), 0) <= 1 for loop in svc.air_loops)
    zone_load = 0.0
    for zs in model.sql.zone_sizes():
        if zs["zone"].upper() == zone_u and (zs["load_type"] or "").lower() == mode:
            zone_load = max(zone_load, float(zs["user_load"] or zs["calc_load"] or 0.0))
    for (ctype, cname), descs in by_comp.items():
        if not _relevant_component(ctype, mode):
            continue
        for desc, (uval, units) in descs.items():
            if not desc.startswith("User-Specified "):
                continue
            base = desc[len("User-Specified "):]
            if not _relevant_field(base, mode):
                continue
            dval = descs.get("Design Size " + base, (None, None))[0]
            if uval is None or uval <= 0:
                continue
            if dval:
                ratio = uval / dval
                if ratio < 0.97:
                    found.append({"component": cname, "type": ctype, "field": base, "user": uval,
                                  "design": dval, "units": units, "ratio": ratio})
            elif single_zone and zone_load > 0 and (units or "").upper() == "W" and "capacity" in base.lower():
                # EnergyPlus reports no design size for some hard-sized coils: compare with the
                # design load of the only zone the system serves
                ratio = uval / zone_load
                if ratio < 0.8:
                    found.append({"component": cname, "type": ctype, "field": base + " (vs zone design load)",
                                  "user": uval, "design": zone_load, "units": units, "ratio": ratio})
    # zone-level sizing: user design load/airflow below the calculated value (zone sizing factor < 1)
    for zs in model.sql.zone_sizes():
        if zs["zone"].upper() != zone_u or (zs["load_type"] or "").lower() != mode:
            continue
        if zs["calc_flow"] and zs["user_flow"] is not None:
            ratio = zs["user_flow"] / zs["calc_flow"]
            if ratio < 0.97:
                found.append({"component": zs["zone"], "type": "Sizing:Zone", "field":
                              f"{mode} design air flow", "user": zs["user_flow"],
                              "design": zs["calc_flow"], "units": "m3/s", "ratio": ratio})
    found.sort(key=lambda d: d["ratio"])
    return found


def _first_occupied_hours(occ: List[float], stamps: List[Tuple]) -> set:
    """Indices of the first two occupied hours of each day."""
    first = set()
    by_day: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, st in enumerate(stamps):
        by_day[(st[0], st[1])].append(i)
    for _, idxs in by_day.items():
        occ_idx = [i for i in idxs if occ[i] > 0]
        if occ_idx:
            start = occ_idx[0]
            first.add(start)
            if start + 1 in idxs:
                first.add(start + 1)
    return first


def facility_union(model: ModelRun) -> Optional[Tuple[float, float, float]]:
    """(any-zone heating h, any-zone cooling h, any-zone heating-or-cooling h), occupied, from the
    facility zone-timestep series. None when the series were not reported."""
    s = model.sql
    fh = s.series(VAR_FAC_H, frequency="Zone Timestep")
    fc = s.series(VAR_FAC_C, frequency="Zone Timestep")
    if not fh or not fc:
        return None
    h = next(iter(fh.values()))
    c = next(iter(fc.values()))
    return sum(h), sum(c), sum(max(a, b) for a, b in zip(h, c))


def compute(model: ModelRun, diagnose: bool = True, top_n: int = 6) -> UnmetStats:
    rh, rc, leed = _facility_reported(model)
    s = model.sql
    zone_tab = _zone_hours_tabular(model)
    fu = facility_union(model)
    hourly = s.has_hourly(VAR_H) and s.has_hourly(VAR_C)
    if fu is None:
        stats = UnmetStats(rh, rc, leed, None, None, None, zone_tab, hourly)
    else:
        uh, uc, uu = fu
        stats = UnmetStats(rh, rc, leed, round(uu, 2), round(uh, 2), round(uc, 2), zone_tab, hourly)
        if rh is not None and rc is not None:
            stats.union_matches_reported = abs(uh - rh) <= 0.1 and abs(uc - rc) <= 0.1
    if diagnose and hourly and (stats.judged or 0) > 0:
        stats.diagnosis = _diagnose(model, stats, top_n)
    return stats


def _zone_sizing_supply_temps(model: ModelRun) -> Dict[Tuple[str, str], float]:
    """(zone upper, mode) -> design supply air temperature from Sizing:Zone, when specified that way."""
    out: Dict[Tuple[str, str], float] = {}
    if model.idf is None:
        return out
    for o in model.idf.by_class("Sizing:Zone"):
        for mode, i_method, i_val in (("cooling", 1, 2), ("heating", 4, 5)):
            if o.get(i_method).strip().upper() == "SUPPLYAIRTEMPERATURE":
                try:
                    out[(o.name.upper(), mode)] = float(o.get(i_val))
                except ValueError:
                    pass
    return out


def _loop_outlet_nodes(model: ModelRun) -> Dict[str, str]:
    """Air loop name (upper) -> supply outlet node name, from the IDF when available."""
    out: Dict[str, str] = {}
    if model.idf is not None:
        for loop in model.idf.by_class("AirLoopHVAC"):
            node = loop.get(9).strip()
            if node:
                out[loop.name.upper()] = node
    return out


def _diagnose(model: ModelRun, stats: UnmetStats, top_n: int) -> List[ZoneModeDiagnosis]:
    s = model.sql
    stamps = s.time_stamps()
    total = max(1e-9, stats.judged or 0.0)
    h, c = s.hourly(VAR_H), s.hourly(VAR_C)
    qh, qc = s.hourly(VAR_QH), s.hourly(VAR_QC)
    temp = s.hourly(VAR_T)
    sph, spc = s.hourly(VAR_SPH), s.hourly(VAR_SPC)
    occ = s.hourly(VAR_OCC)
    damper = {k.upper(): v for k, v in s.hourly(VAR_DAMPER).items()}
    node_t = {k.upper(): v for k, v in s.hourly(VAR_NODE_T).items()}
    node_sp = {k.upper(): v for k, v in s.hourly(VAR_NODE_SP).items()}
    odb = next(iter(s.hourly(VAR_ODB).values()), None)
    owb = next(iter(s.hourly(VAR_OWB).values()), None)
    dds = _design_days(model)
    cool_dd = [d for d in dds if not _is_heating_dd(d["name"])]
    heat_dd = [d for d in dds if _is_heating_dd(d["name"])]
    services = zone_services(s)
    loops = air_loop_components(s)
    outlets = _loop_outlet_nodes(model)
    design_load = {}
    for zs in s.zone_sizes():
        k = (zs["zone"].upper(), (zs["load_type"] or "").lower())
        design_load[k] = max(design_load.get(k, 0.0), float(zs["user_load"] or zs["calc_load"] or 0.0))
    zone_sizing_sat = _zone_sizing_supply_temps(model)
    loop_sp_nodes = {}
    if model.idf is not None:
        for pl in model.idf.by_class("PlantLoop"):
            if pl.get(4):
                loop_sp_nodes[pl.name.upper()] = pl.get(4).strip().upper()
    all_plants = plant_loops(s)
    condenser = {lp for lp, d in all_plants.items() if any(c["type"].startswith("CHILLER") for c in d["demand"])}

    items: List[Tuple[str, str, List[float]]] = []
    for z, series in h.items():
        if sum(series) > 0:
            items.append((z, "heating", series))
    for z, series in c.items():
        if sum(series) > 0:
            items.append((z, "cooling", series))
    items.sort(key=lambda it: -sum(it[2]))

    out: List[ZoneModeDiagnosis] = []
    for z, mode, series in items[:top_n]:
        idx = [i for i, v in enumerate(series) if v > 1e-9]
        n_idx = max(1, len(idx))
        q = (qh if mode == "heating" else qc).get(z)
        sp = (sph if mode == "heating" else spc).get(z)
        t = temp.get(z)
        d = ZoneModeDiagnosis(zone=z, mode=mode, hours=sum(series), share=min(1.0, sum(series) / total))
        svc = services.get(z.upper())
        counts: Counter = Counter()
        qmax = max(q) if q else 0.0
        dload = design_load.get((z.upper(), mode), 0.0)
        never = qmax <= 1.0 or (dload > 0 and qmax < 0.1 * dload)
        for i in idx:
            if q is None:
                counts["unknown"] += 1
            elif never:
                counts[NO_DELIVERY] += 1
            elif q[i] <= max(1.0, 0.01 * qmax):
                counts[SYSTEM_OFF] += 1
            elif q[i] >= 0.9 * qmax:
                counts[AT_CAPACITY] += 1
            else:
                counts[PARTIAL] += 1
        if t and sp:
            misses = [abs(t[i] - sp[i]) for i in idx]
            d.mean_miss_k = sum(misses) / len(misses) if misses else None
        # system-side evidence: terminal damper at max, supply air not at setpoint
        damper_max = 0
        terms = sorted(svc.terminals) if svc else []
        for term in terms:
            dp = damper.get(term.upper())
            if dp:
                damper_max = max(damper_max, sum(1 for i in idx if dp[i] >= 0.98))
        sat_miss = 0
        sat_desc = ""
        sys_sp_unmet: List[float] = []
        for loop in sorted(svc.air_loops) if svc else []:
            node = outlets.get(loop.upper())
            cand = [node.upper()] if node else [k for k in node_t if loop.upper() in k]
            for nk in cand:
                tt, ss = node_t.get(nk), node_sp.get(nk)
                if not tt or not ss:
                    continue
                sys_sp_unmet = [ss[i] for i in idx if ss[i] > -50]
                if mode == "cooling":
                    n = sum(1 for i in idx if ss[i] > -50 and tt[i] > ss[i] + 0.5)
                else:
                    n = sum(1 for i in idx if ss[i] > -50 and tt[i] < ss[i] - 0.5)
                if n > sat_miss:
                    sat_miss = n
                    gaps = [abs(tt[i] - ss[i]) for i in idx if ss[i] > -50]
                    sat_desc = f"air loop {loop} (supply outlet {nk.title() if False else nk})"
                    d.served_by = [loop]
        if svc and not d.served_by:
            d.served_by = sorted(svc.air_loops) + sorted(svc.zone_equipment)
        # plant side: water coils serving the zone -> plant loops -> supply temperature vs setpoint
        coil_names = set()
        if svc:
            coil_names |= {n.upper() for n in svc.terminals | svc.zone_equipment}
            for loop in svc.air_loops:
                coil_names |= {c["name"].upper() for c in loops.get(loop.upper(), [])}
        plant_seen, plant_equipment = plants_serving(s, coil_names) if coil_names else ([], [])
        wanted_equip = ("CHILLER", "DISTRICTCOOLING") if mode == "cooling" else ("BOILER", "DISTRICTHEATING")
        relevant = [lp for lp in plant_seen if lp not in condenser and any(
            e["type"].startswith(wanted_equip) or e["type"].startswith("HEATEXCHANGER")
            for e in all_plants.get(lp, {}).get("supply", []))]
        plant_miss, plant_desc = 0, ""
        for lp in relevant:
            nk = loop_sp_nodes.get(lp)
            tt, ss = (node_t.get(nk), node_sp.get(nk)) if nk else (None, None)
            if not tt or not ss:
                continue
            if mode == "cooling":
                n = sum(1 for i in idx if ss[i] > -50 and tt[i] > ss[i] + 0.5)
            else:
                n = sum(1 for i in idx if ss[i] > -50 and tt[i] < ss[i] - 0.5)
            if n > plant_miss:
                plant_miss, plant_desc = n, f"plant loop {lp}"
        plant_equipment = [e for e in plant_equipment if e["type"].startswith(wanted_equip)]
        zone_occ = occ.get(z)
        recovery_n = 0
        if zone_occ:
            first = _first_occupied_hours(zone_occ, stamps)
            recovery_n = sum(1 for i in idx if i in first)
        beyond_dd = 0
        dd_desc = ""
        if odb is not None:
            if mode == "cooling" and cool_dd:
                dd = max(cool_dd, key=lambda x: x["max_db"] or -99)
                lim_db = dd["max_db"]
                lim_wb = dd["humidity"] if "wetbulb" in str(dd["humidity_type"]).lower() else None
                for i in idx:
                    hot = lim_db is not None and odb[i] > lim_db + 0.5
                    humid = lim_wb is not None and owb is not None and owb[i] > lim_wb + 0.5
                    if hot or humid:
                        beyond_dd += 1
                dd_desc = (f"{dd['name']} (DB {lim_db:.1f} °C" + (f", WB {lim_wb:.1f} °C" if lim_wb is not None else "")
                           + ")")
            elif mode == "heating" and heat_dd:
                dd = min(heat_dd, key=lambda x: x["max_db"] if x["max_db"] is not None else 99)
                lim_db = dd["max_db"]
                for i in idx:
                    if lim_db is not None and odb[i] < lim_db - 0.5:
                        beyond_dd += 1
                dd_desc = f"{dd['name']} (DB {lim_db:.1f} °C)" if lim_db is not None else dd["name"]
        hard = _hard_sizing_for_zone(model, z, mode, services, loops, plant_equipment)
        d.hard_sizing = hard
        d.counts = dict(counts)
        for key, val in ((RECOVERY, recovery_n), (DESIGN_BASIS, beyond_dd), (COIL_LIMITED, sat_miss),
                         (AIRFLOW_LIMITED, damper_max), (PLANT_LIMITED, plant_miss)):
            if val:
                d.counts[key] = val

        frac = lambda k: d.counts.get(k, 0) / n_idx  # noqa: E731
        limited = (frac(AT_CAPACITY) >= 0.5 or frac(COIL_LIMITED) >= 0.5 or frac(AIRFLOW_LIMITED) >= 0.5
                   or frac(PLANT_LIMITED) >= 0.5)
        if frac(NO_DELIVERY) >= 0.5:
            d.cause = NO_DELIVERY
        elif frac(SYSTEM_OFF) >= 0.5:
            d.cause = SYSTEM_OFF
        elif hard and (limited or frac(PARTIAL) >= 0.5):
            d.cause = HARD_SIZED
        elif limited and frac(DESIGN_BASIS) >= 0.75:
            # most unmet hours are colder/hotter than the sizing design day: the design day is the root
            # cause even if the misses show up during morning recovery
            d.cause = DESIGN_BASIS
        elif frac(RECOVERY) >= 0.6:
            d.cause = RECOVERY
        elif limited and frac(DESIGN_BASIS) >= 0.5:
            d.cause = DESIGN_BASIS
        elif frac(PLANT_LIMITED) >= 0.5:
            d.cause = PLANT_LIMITED
        elif frac(COIL_LIMITED) >= 0.5:
            d.cause = COIL_LIMITED
        elif frac(AIRFLOW_LIMITED) >= 0.5:
            d.cause = AIRFLOW_LIMITED
        elif frac(AT_CAPACITY) >= 0.5:
            d.cause = AT_CAPACITY
        elif d.mean_miss_k is not None and d.mean_miss_k < 0.5:
            d.cause = MARGINAL
        else:
            d.cause = PARTIAL

        ev = [f"{d.hours:.1f} occupied {mode} unmet hours ({d.share:.0%} of the building's {total:.0f})."]
        for key, label in ((AT_CAPACITY, f"with the zone receiving ≥90% of its annual peak {mode}"),
                           (SYSTEM_OFF, f"with no {mode} delivered to the zone"),
                           (NO_DELIVERY, f"with no {mode} delivered all year"),
                           (COIL_LIMITED, f"with {sat_desc} missing its supply-air setpoint by >0.5 K"
                            if sat_desc else "with the supply air missing its setpoint"),
                           (AIRFLOW_LIMITED, "with the zone's VAV damper fully open"),
                           (PLANT_LIMITED, f"with {plant_desc} missing its supply-water setpoint by >0.5 K"),
                           (RECOVERY, "in the first two occupied hours of the day"),
                           (DESIGN_BASIS, f"when outdoor conditions exceed {dd_desc}")):
            if d.counts.get(key):
                ev.append(f"{d.counts[key] / n_idx:.0%} of those hours {label}.")
        if d.mean_miss_k is not None:
            ev.append(f"Average miss: {d.mean_miss_k:.2f} K from the {mode} setpoint.")
        zsat = zone_sizing_sat.get((z.upper(), mode))
        if zsat is not None and sys_sp_unmet and mode == "cooling":
            sys_sat = sum(sys_sp_unmet) / len(sys_sp_unmet)
            if zsat < sys_sat - 1.0:
                ev.append(f"Zone airflow was sized for {zsat:.1f} °C supply air (Sizing:Zone), but the system "
                          f"supplies {sys_sat:.1f} °C — the zone's design airflow is too small for the air it gets.")
                if d.cause in (AT_CAPACITY, PARTIAL, MARGINAL) or frac(AIRFLOW_LIMITED) >= 0.3:
                    d.cause = AIRFLOW_LIMITED
        for hz in hard[:3]:
            ev.append(f"{hz['type']} “{hz['component']}”: {hz['field']} user-specified "
                      f"{hz['user']:.4g} {hz['units']} vs design size {hz['design']:.4g} ({hz['ratio']:.0%}).")
        d.evidence = ev
        out.append(d)
    return out


# ----------------------------------------------------------------------------------
def _fmt(v: Optional[float]) -> str:
    return "—" if v is None else (f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.2f}")


def _model_finding(model: ModelRun, stats: UnmetStats, edition: str) -> Finding:
    clause = CLAUSES.get(edition, CLAUSES["2010"])
    rs = stats.reported_sum
    evidence = [
        {"Metric": "Occupied heating not met (EnergyPlus, facility)", "Hours": _fmt(stats.reported_heating)},
        {"Metric": "Occupied cooling not met (EnergyPlus, facility)", "Hours": _fmt(stats.reported_cooling)},
        {"Metric": "LEED summary ‘Number of hours not met’ (heating + cooling)", "Hours": _fmt(stats.reported_leed)},
        {"Metric": "Hours with any zone unmet (90.1 definition)",
         "Hours": _fmt(stats.union_hours) if stats.union_hours is not None else "needs hourly output"},
    ]
    j = stats.judged
    if j is None:
        return Finding("UMLH-300", "Unmet load hours ≤ 300", SKIPPED,
                       "No unmet-hours table found in the SQL output (run with AllSummary reports).",
                       model=model.label, clause=clause)
    fixes: List[str] = []
    objects: List[str] = []
    if stats.union_hours is not None:
        if stats.union_hours > LIMIT:
            status = FAIL
            msg = (f"{stats.union_hours} hours have at least one zone outside setpoint while occupied "
                   f"(limit 300).")
        elif rs is not None and rs > LIMIT:
            status = REVIEW
            msg = (f"The exact count is {stats.union_hours} h (within 300), but EnergyPlus's heating + cooling "
                   f"total is {_fmt(rs)} h. Reviewers often read the summed figure; report the exact count "
                   f"with an explanation, or reduce unmet hours below 300 on both measures.")
        else:
            status = PASS
            msg = f"{stats.union_hours} unmet load hours (exact count); heating + cooling total {_fmt(rs)} h."
    else:
        hmax = max(stats.reported_heating or 0, stats.reported_cooling or 0)
        if hmax > LIMIT:
            status = FAIL
            msg = f"Occupied {'heating' if (stats.reported_heating or 0) >= (stats.reported_cooling or 0) else 'cooling'} " \
                  f"unmet hours alone are {_fmt(hmax)} h (limit 300)."
        elif rs is not None and rs > LIMIT:
            status = REVIEW
            msg = (f"Heating + cooling unmet hours total {_fmt(rs)} h (> 300). The exact count of hours could be "
                   f"lower; rerun with `prereview run` to get hourly output and the exact figure.")
        else:
            status = PASS
            msg = f"Heating + cooling unmet hours total {_fmt(rs)} h (≤ 300)."
    if stats.diagnosis and status != PASS:
        top = stats.diagnosis[0]
        msg += f" Main contributor: {top.zone} ({top.mode}, {top.hours:.0f} h) — " \
               f"{_cause_text(top.cause, top.mode)}"
        if top.cause == HARD_SIZED and top.hard_sizing:
            hz = top.hard_sizing[0]
            msg += (f" {hz['type']} “{hz['component']}” {hz['field']} is {hz['user']:,.4g} {hz['units']}, "
                    f"{hz['ratio']:.0%} of its design size ({hz['design']:,.4g}).")
        others = [d for d in stats.diagnosis[1:] if d.share >= 0.1]
        if others:
            msg += " Also: " + "; ".join(f"{d.zone} ({d.mode}, {d.hours:.0f} h, "
                                         f"{_cause_text(d.cause, d.mode).rstrip('.').lower()})"
                                         for d in others[:3]) + "."
        seen = set()
        for dg in [top] + others:
            objects.append(dg.zone)
            if dg.cause not in seen:
                seen.add(dg.cause)
                for fx in CAUSE_FIXES[dg.cause]:
                    fx = fx.format(mode=dg.mode)
                    if fx.startswith("[") and "] " in fx:
                        role, fx = fx[1:].split("] ", 1)
                        if role != model.role:
                            continue
                    if fx not in fixes:
                        fixes.append(fx)
        for dg in stats.diagnosis:
            if dg.zone not in objects:
                objects.append(dg.zone)
        for hz in top.hard_sizing[:3]:
            if hz["component"] not in objects:
                objects.append(hz["component"])
    elif status != PASS and not stats.hourly:
        fixes.append("Rerun the model with `prereview run` so hourly output is available for the diagnosis.")
        objects = [z for z, _, _ in stats.zone_hours[:5]]
    data = {
        "reported_heating": stats.reported_heating, "reported_cooling": stats.reported_cooling,
        "reported_leed": stats.reported_leed, "union_hours": stats.union_hours,
        "union_heating": stats.union_heating, "union_cooling": stats.union_cooling,
        "zone_hours": stats.zone_hours[:20],
        "diagnosis": [dg.__dict__ for dg in stats.diagnosis],
        "source": SOURCE_TEXT,
    }
    return Finding("UMLH-300", "Unmet load hours ≤ 300", status, msg, model=model.label, clause=clause,
                   evidence=evidence, objects=objects, fixes=fixes, data=data)


def _design_day_findings(model: ModelRun, edition: str) -> List[Finding]:
    out: List[Finding] = []
    dds = _design_days(model)
    weather = model.weather()
    if not dds:
        out.append(Finding("SIZE-DD", "Design days present", REVIEW,
                           "No SizingPeriod:DesignDay objects found; check how equipment was sized.",
                           model=model.label, clause="Appendix G equipment capacities (sizing runs)"))
        return out
    generic = {"TMY", "TMY2", "TMY3", "TMYX", "IWEC", "IWEC2", "CWEC", "SRC", "WMO", "INTL", "AIRPORT", "AP",
               "ANN", "HTG", "CLG", "CONDNS", "USA", "CAN", "NOR", "COUNTY", "CITY", "INT", "FIELD", "AFB"}
    tokens = [t for t in re.findall(r"[A-Za-z]{4,}", weather.upper()) if t not in generic]
    names = " ".join(d["name"].upper() for d in dds)
    if tokens and not any(t in names for t in tokens):
        out.append(Finding(
            "SIZE-DD", "Design days match the weather file location", FAIL,
            f"The design days ({', '.join(d['name'] for d in dds[:3])}) do not appear to be for the weather file "
            f"location ({weather}). Equipment sized for another climate is a common cause of unmet hours and "
            f"an automatic review comment.",
            model=model.label, clause="Appendix G equipment capacities — sizing runs use the project's weather/design days",
            evidence=[{"Design day": d["name"], "Max dry-bulb (°C)": _fmt(d["max_db"])} for d in dds],
            fixes=["Import the design days from the .ddy file that comes with the project weather file."]))
    elif model.role == "baseline" and edition == "2010":
        heat = [d for d in dds if _is_heating_dd(d["name"])]
        cool = [d for d in dds if not _is_heating_dd(d["name"])]
        problems = []
        if heat and not any("99.6" in d["name"] for d in heat):
            problems.append("heating design day is not the 99.6% condition")
        if cool and not any(re.search(r"(^|[^.\d])1(\.0)?%", d["name"]) for d in cool):
            problems.append("cooling design day is not the 1% condition")
        if problems:
            out.append(Finding(
                "SIZE-DD", "Baseline sizing design days", REVIEW,
                "Baseline sizing basis differs from 90.1-2010 G3.1.2.2.1: " + "; ".join(problems) + ".",
                model=model.label, clause="ASHRAE 90.1-2010 G3.1.2.2.1 (99.6% heating, 1% cooling design temperatures)",
                evidence=[{"Design day": d["name"]} for d in dds],
                fixes=["Use the 99.6% heating and 1% cooling (DB and WB) design days for baseline sizing runs, "
                       "or document why another basis is used."]))
    return out


def _tolerances(model: ModelRun) -> Optional[Tuple[float, float]]:
    """(heating, cooling) not-met reporting tolerance in K from the run's IDF; EnergyPlus default 0.2."""
    if model.idf is None:
        return None
    o = model.idf.first("OutputControl:ReportingTolerances")
    vals = []
    for i in (0, 1):
        try:
            vals.append(float(o.get(i)) if o is not None and o.get(i).strip() else 0.2)
        except ValueError:
            vals.append(0.2)
    return vals[0], vals[1]


def _tolerance_finding(ctx: Context) -> Optional[Finding]:
    rows, tols = [], {}
    for m in ctx.simulated:
        t = _tolerances(m)
        if t is None:
            continue
        tols[m.label] = t
        rows.append({"Model": m.label, "Heating tolerance (K)": f"{t[0]:.2f}", "Cooling tolerance (K)": f"{t[1]:.2f}"})
    if not tols:
        return None
    clause = "Unmet load hour definition — outside setpoint ± half the throttling range (OutputControl:ReportingTolerances)"
    distinct = {v for v in tols.values()}
    if len(distinct) > 1:
        return Finding("UMLH-TOL", "Same unmet-hour reporting tolerance in every model", FAIL,
                       "The models count unmet hours with different tolerances, so their unmet hours are not "
                       "comparable.", clause=clause, evidence=rows,
                       fixes=["Use the same OutputControl:ReportingTolerances in the proposed and every baseline run."])
    h, c = next(iter(distinct))
    if max(h, c) > 1.12:
        return Finding("UMLH-TOL", "Unmet-hour reporting tolerance", REVIEW,
                       f"Unmet hours are counted with a {max(h, c):.2f} K tolerance (EnergyPlus default 0.2 K). "
                       f"Tolerances above 1.11 K (2 °F) hide unmet hours and are likely to be questioned.",
                       clause=clause, evidence=rows,
                       fixes=["Use a tolerance that matches the thermostat throttling range (half of it), and "
                              "state it in the submittal."])
    return Finding("UMLH-TOL", "Unmet-hour reporting tolerance", PASS,
                   f"Same tolerance in every model ({h:.2f} K heating, {c:.2f} K cooling).", clause=clause)


def check(ctx: Context) -> List[Finding]:
    findings: List[Finding] = []
    stats_by_label: Dict[str, UnmetStats] = {}
    tol = _tolerance_finding(ctx)
    if tol is not None:
        findings.append(tol)
    for m in ctx.simulated:
        st = compute(m)
        stats_by_label[m.label] = st
        findings.append(_model_finding(m, st, ctx.edition))
        findings.extend(_design_day_findings(m, ctx.edition))

    if ctx.pair_ready:
        p = stats_by_label[ctx.proposed.label].judged
        bvals = [(b.label, stats_by_label[b.label].judged) for b in ctx.baselines if b.label in stats_by_label]
        bvals = [(lbl, v) for lbl, v in bvals if v is not None]
        clause = CLAUSES.get(ctx.edition, CLAUSES["2010"])
        if p is None or not bvals:
            findings.append(Finding("UMLH-50", "Proposed unmet hours within 50 of baseline", SKIPPED,
                                    "Unmet hours missing for the proposed or baseline model.", clause=clause))
        else:
            mean_b = sum(v for _, v in bvals) / len(bvals)
            diff = p - mean_b
            worst_lbl, worst_b = min(bvals, key=lambda x: x[1])
            ev = [{"Model": ctx.proposed.label, "Unmet hours": _fmt(p)}] + \
                 [{"Model": lbl, "Unmet hours": _fmt(v)} for lbl, v in bvals] + \
                 [{"Model": "Baseline average", "Unmet hours": _fmt(mean_b)}]
            if diff > DIFF_LIMIT:
                status = FAIL if ctx.edition == "2010" else REVIEW
                msg = (f"Proposed has {_fmt(p)} unmet hours vs a baseline average of {_fmt(mean_b)} "
                       f"(+{_fmt(diff)} h; limit +50).")
                fixes = ["Reduce the proposed unmet hours (see the diagnosis), or decrease baseline capacities "
                         "incrementally and re-simulate until the difference is within 50 hours, as the "
                         "standard prescribes."]
            elif p - worst_b > DIFF_LIMIT:
                status = REVIEW
                msg = (f"Within 50 h of the baseline average ({_fmt(mean_b)} h), but {_fmt(p - worst_b)} h above "
                       f"{worst_lbl}. Some reviewers compare against each rotation.")
                fixes = ["State in the submittal that the comparison uses the average of the four rotations."]
            else:
                status = PASS
                msg = f"Proposed {_fmt(p)} h vs baseline average {_fmt(mean_b)} h (difference {_fmt(diff)} h)."
                fixes = []
            findings.append(Finding("UMLH-50", "Proposed unmet hours within 50 of baseline", status, msg,
                                    clause=clause, evidence=ev, fixes=fixes,
                                    data={"proposed": p, "baseline_mean": mean_b, "source": SOURCE_TEXT}))
    return findings
