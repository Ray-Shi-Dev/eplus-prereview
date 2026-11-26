"""Baseline vs proposed: the inputs Appendix G requires to be the same in both models.

Compared from EnergyPlus's own hourly results (not from IDF text), so it does not matter how
a schedule or load was written — only what the simulation actually used.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..model import ModelRun
from ..topology import zone_services
from .base import FAIL, PASS, REVIEW, SKIPPED, Context, Finding

CL_SCHED = "Table G3.1 #4 Schedules — same in proposed and baseline"
CL_PEOPLE = "Table G3.1 #1/#4 — occupancy and its schedule identical in both models"
CL_PLUG = "Table G3.1 #12 Receptacle and other loads — identical in both models"
CL_TSTAT = "Table G3.1 #4 Schedules — thermostat setpoints identical in both models"
CL_ZONES = "Table G3.1 #1 Design model / #7 thermal blocks — same building, zones and conditioned area"
CL_SITE = "Appendix G G2 — same simulation program, weather file and simulation period"
CL_INFIL = {"2010": "G3.1.1 / Table G3.1 #5 — infiltration modeled the same way in both models",
            "2016": "Table G3.1 #5(b) — baseline infiltration fixed; proposed may differ only with justification"}
CL_OA = "G3.1.2.6 (2010) / G3.1.2.5 (2016) — minimum outdoor air the same in both models (DCV exceptions)"
CL_HVAC = "Table G3.1 #4 Schedules — HVAC system operation schedule the same in both models"
CL_LIGHT = "Table G3.1 #4/#6 — lighting schedules the same (control credits via Table G3.2, not schedules)"

V_PEOPLE = "Zone People Occupant Count"
V_ELEC = "Zone Electric Equipment Electricity Rate"
V_GAS = "Zone Gas Equipment NaturalGas Rate"
V_OTHER = "Zone Other Equipment Total Heating Rate"
V_SPH = "Zone Thermostat Heating Setpoint Temperature"
V_SPC = "Zone Thermostat Cooling Setpoint Temperature"


# ----------------------------------------------------------------------------- helpers
def _zone_map(p: ModelRun, b: ModelRun) -> Tuple[Dict[str, str], List[str], List[str]]:
    """Map proposed zone name (upper) -> baseline zone name (upper). Name first, then floor area."""
    pz = {z.name.upper(): z for z in p.sql.zones()}
    bz = {z.name.upper(): z for z in b.sql.zones()}
    mapping = {k: k for k in pz if k in bz}
    left_p = [k for k in pz if k not in mapping]
    left_b = [k for k in bz if k not in mapping.values()]
    for k in list(left_p):
        a = pz[k].total_area
        cands = [x for x in left_b if a > 0 and abs(bz[x].total_area - a) / a < 0.005]
        if len(cands) == 1:
            mapping[k] = cands[0]
            left_p.remove(k)
            left_b.remove(cands[0])
    return mapping, left_p, left_b


def _upper(d: Dict[str, List[float]]) -> Dict[str, List[float]]:
    return {k.upper(): v for k, v in d.items()}


def _stamp(model: ModelRun, i: int) -> str:
    st = model.sql.time_stamps()
    if 0 <= i < len(st):
        m, d, h, dt = st[i]
        return f"{m:02d}/{d:02d} {h:02d}:00 ({dt})"
    return f"hour {i}"


def _compare(a: List[float], b: List[float], rel: float, abs_tol: float) -> Tuple[int, float, int]:
    """(hours that differ, max abs difference, first differing index)."""
    n = 0
    mx = 0.0
    first = -1
    peak = max([abs(x) for x in a] + [abs(x) for x in b] + [0.0])
    tol = max(abs_tol, rel * peak)
    for i, (x, y) in enumerate(zip(a, b)):
        dlt = abs(x - y)
        if dlt > tol:
            n += 1
            if first < 0:
                first = i
        mx = max(mx, dlt)
    if len(a) != len(b):
        n += abs(len(a) - len(b))
    return n, mx, first


def _fmt(v: Optional[float], nd: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{nd}f}"


def _comparable(ctx: Context) -> Optional[str]:
    """Reason hourly comparisons are meaningless (different run lengths), else None."""
    n = len(ctx.proposed.sql.time_stamps())
    for b in ctx.baselines:
        if len(b.sql.time_stamps()) != n:
            return (f"not compared hour by hour: {b.label} simulated {len(b.sql.time_stamps())} hours and the "
                    f"proposed {n} (see CONS-SITE)")
    return None


def _series_check(ctx: Context, rule_id: str, title: str, var: str, clause: str, unit: str,
                  rel: float, abs_tol: float, status_on_diff: str, scale: float = 1.0, agg: str = "sum",
                  what: str = "", fixes: Optional[List[str]] = None, nominal: Optional[str] = None) -> Finding:
    p = ctx.proposed
    if nominal and all(m.sql.has_table(nominal) and not m.sql.q(f"select 1 from {nominal} limit 1")
                       for m in [p] + ctx.baselines):
        noun = {"NominalGasEquipment": "GasEquipment", "NominalOtherEquipment": "OtherEquipment"}.get(nominal, nominal)
        return Finding(rule_id, title, PASS, f"No {noun} objects in any model.", clause=clause)
    why = _comparable(ctx)
    if why:
        return Finding(rule_id, title, SKIPPED, f"{what or var}: {why}.", clause=clause)
    rows: List[Dict[str, str]] = []
    objs: List[str] = []
    compared = 0
    pdata = _upper(p.sql.hourly(var))
    for b in ctx.baselines:
        bdata = _upper(b.sql.hourly(var))
        mapping, _, _ = _zone_map(p, b)
        keys = sorted(set(pdata) | {pk for pk, bk in mapping.items() if bk in bdata})
        for pk in keys:
            bk = mapping.get(pk, pk)
            a = pdata.get(pk)
            c = bdata.get(bk)
            if a is None and c is None:
                continue
            compared += 1
            if a is None or c is None:
                tot = sum(a or c) * scale
                if abs(tot) < 1e-9:
                    continue
                rows.append({"Baseline": b.label, "Zone": pk, f"Proposed ({unit})": "absent" if a is None else
                             _fmt(sum(a) * scale), f"Baseline ({unit})": "absent" if c is None else _fmt(sum(c) * scale),
                             "Hours that differ": "all", "First difference": "—"})
                objs.append(pk)
                continue
            n, mx, first = _compare(a, c, rel, abs_tol)
            if n:
                if agg == "sum":
                    va, vb = sum(a) * scale, sum(c) * scale
                else:
                    va, vb = mx, None
                rows.append({"Baseline": b.label, "Zone": pk, f"Proposed ({unit})": _fmt(va),
                             f"Baseline ({unit})": _fmt(vb) if vb is not None else f"max Δ {_fmt(mx, 2)}",
                             "Hours that differ": str(n),
                             "First difference": _stamp(p, first) +
                             (f": {a[first]:.4g} vs {c[first]:.4g}" if first >= 0 else "")})
                if pk not in objs:
                    objs.append(pk)
    if compared == 0:
        return Finding(rule_id, title, SKIPPED, f"No hourly “{var}” output in the models (none in the "
                       f"building, or not requested — use `prereview run`).", clause=clause)
    if rows:
        nb = len({r["Baseline"] for r in rows})
        return Finding(rule_id, title, status_on_diff,
                       f"{what or var} differs between proposed and baseline in {len(objs)} zone"
                       f"{'s' if len(objs) != 1 else ''}" + (f" ({nb} baseline runs)" if nb > 1 else "") + ".",
                       clause=clause, evidence=rows[:30], objects=objs[:30], fixes=fixes or [])
    return Finding(rule_id, title, PASS, f"{what or var}: identical hourly values in every zone "
                   f"({compared} zone comparisons).", clause=clause)


# ----------------------------------------------------------------------------- checks
def _site(ctx: Context) -> Finding:
    p = ctx.proposed
    rows = []
    bad = False
    for b in ctx.baselines:
        for label, pa, ba in (("Weather file", p.weather(), b.weather()),
                              ("EnergyPlus version", p.sql.version() or "", b.sql.version() or ""),
                              ("Hours simulated", str(p.hours_simulated()), str(b.hours_simulated())),
                              ("Timesteps per hour", str(p.sql.timesteps_per_hour()), str(b.sql.timesteps_per_hour()))):
            same = pa.strip().upper() == ba.strip().upper()
            if not same:
                bad = True
            rows.append({"Baseline": b.label, "Item": label, "Proposed": pa, "Baseline value": ba,
                         "Same": "yes" if same else "NO"})
    if bad:
        return Finding("CONS-SITE", "Same weather, program version and run period", FAIL,
                       "Proposed and baseline were not simulated under the same conditions.",
                       clause=CL_SITE, evidence=[r for r in rows if r["Same"] == "NO"],
                       fixes=["Run both models with the same EnergyPlus version, weather file, run period and "
                              "timestep."])
    return Finding("CONS-SITE", "Same weather, program version and run period", PASS,
                   "Same weather file, EnergyPlus version, run period and timestep.", clause=CL_SITE)


def _zones(ctx: Context) -> Finding:
    p = ctx.proposed
    rows = []
    bad = False
    objs: List[str] = []
    for b in ctx.baselines:
        mapping, left_p, left_b = _zone_map(p, b)
        if left_p or left_b:
            bad = True
            for z in left_p:
                rows.append({"Baseline": b.label, "Zone": z, "Issue": "in proposed only"})
                objs.append(z)
            for z in left_b:
                rows.append({"Baseline": b.label, "Zone": z, "Issue": "in baseline only"})
                objs.append(z)
        for label, fa, fb in (("Net conditioned floor area", p.conditioned_area(), b.conditioned_area()),
                              ("Total floor area", p.total_area(), b.total_area())):
            if fa and fb and abs(fa - fb) / max(fa, fb) > 0.005:
                bad = True
                rows.append({"Baseline": b.label, "Zone": "(building)",
                             "Issue": f"{label}: proposed {fa:,.1f} m² vs baseline {fb:,.1f} m²"})
    if bad:
        return Finding("CONS-ZONES", "Same zones and floor areas", FAIL,
                       "The models do not describe the same building: zones or floor areas differ.",
                       clause=CL_ZONES, evidence=rows[:30], objects=objs[:30],
                       fixes=["Generate the baseline from the proposed model so the thermal zones and their "
                              "areas are identical; only envelope, lighting power and HVAC change.",
                              "Check zone multipliers and 'Part of Total Floor Area' are the same in both."])
    return Finding("CONS-ZONES", "Same zones and floor areas", PASS,
                   f"{len(p.sql.zones())} zones; same names and floor areas in every baseline.", clause=CL_ZONES)


def _nominal_by_zone(model: ModelRun, table: str, value_col: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    zones = model.sql.zone_by_index()
    for row in model.sql.nominal(table):
        zi = row.get("ZoneIndex")
        z = zones.get(zi)
        if z is None:
            continue
        v = row.get(value_col) or 0.0
        out[z.name.upper()] = out.get(z.name.upper(), 0.0) + float(v) * z.multiplier
    return out


def _nominal_schedules(model: ModelRun, table: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    zones = model.sql.zone_by_index()
    for row in model.sql.nominal(table):
        z = zones.get(row.get("ZoneIndex"))
        name = row.get("ScheduleName") or row.get("ScheduleNameName")
        if z is None or not name:
            continue
        out.setdefault(z.name.upper(), []).append(name.upper())
    return out


def _schedule_series(model: ModelRun) -> Dict[str, List[float]]:
    return _upper(model.sql.hourly("Schedule Value"))


def _infiltration(ctx: Context) -> Finding:
    p = ctx.proposed
    status_diff = FAIL if ctx.edition == "2010" else REVIEW
    clause = CL_INFIL.get(ctx.edition, CL_INFIL["2010"])
    pa = _nominal_by_zone(p, "NominalInfiltration", "DesignLevel")
    psched = _nominal_schedules(p, "NominalInfiltration")
    if not pa and not p.sql.has_table("NominalInfiltration"):
        return Finding("CONS-INFIL", "Infiltration the same", SKIPPED, "No infiltration objects found.",
                       clause=clause)
    rows, objs = [], []
    pser = _schedule_series(p)
    for b in ctx.baselines:
        mapping, _, _ = _zone_map(p, b)
        ba = _nominal_by_zone(b, "NominalInfiltration", "DesignLevel")
        bsched = _nominal_schedules(b, "NominalInfiltration")
        bser = _schedule_series(b)
        for pk in sorted(set(pa) | set(mapping)):
            bk = mapping.get(pk, pk)
            x, y = pa.get(pk, 0.0), ba.get(bk, 0.0)
            issue = ""
            if max(x, y) > 0 and abs(x - y) / max(x, y) > 0.01:
                issue = f"design flow {x:.4g} vs {y:.4g} m³/s"
            elif _comparable(ctx):
                pass
            else:
                for sp, sb in zip(sorted(psched.get(pk, [])), sorted(bsched.get(bk, []))):
                    a, c = pser.get(sp), bser.get(sb)
                    if a is not None and c is not None:
                        n, _, first = _compare(a, c, 0.01, 1e-6)
                        if n:
                            issue = f"schedule {sp} vs {sb}: {n} hours differ (first {_stamp(p, first)})"
                            break
            if issue:
                rows.append({"Baseline": b.label, "Zone": pk, "Difference": issue})
                if pk not in objs:
                    objs.append(pk)
    if rows:
        msg = f"Infiltration differs in {len(objs)} zone{'s' if len(objs) != 1 else ''}."
        if ctx.edition == "2016":
            msg += (" Under 90.1-2016 the baseline uses a fixed rate; a lower proposed rate needs envelope "
                    "testing or documented justification.")
        return Finding("CONS-INFIL", "Infiltration the same", status_diff, msg, clause=clause,
                       evidence=rows[:30], objects=objs[:30],
                       fixes=["Use the same infiltration objects, design flow and schedule in both models "
                              "(90.1-2010), or document the basis for any difference (90.1-2016)."])
    return Finding("CONS-INFIL", "Infiltration the same", PASS,
                   "Same infiltration design flow and schedule in every zone.", clause=clause)


def _lighting_schedules(ctx: Context) -> Finding:
    p = ctx.proposed
    if _comparable(ctx):
        return Finding("CONS-LSCHED", "Lighting schedules the same", SKIPPED, f"Lighting schedules "
                       f"{_comparable(ctx)}.", clause=CL_LIGHT)
    psched = _nominal_schedules(p, "NominalLighting")
    if not psched:
        return Finding("CONS-LSCHED", "Lighting schedules the same", SKIPPED, "No lighting objects found.",
                       clause=CL_LIGHT)
    pser = _schedule_series(p)
    rows, objs = [], []
    for b in ctx.baselines:
        mapping, _, _ = _zone_map(p, b)
        bsched = _nominal_schedules(b, "NominalLighting")
        bser = _schedule_series(b)
        for pk, names in sorted(psched.items()):
            bk = mapping.get(pk, pk)
            bn = bsched.get(bk, [])
            if not bn:
                continue
            a = pser.get(sorted(names)[0])
            c = bser.get(sorted(bn)[0])
            if a is None or c is None:
                continue
            n, mx, first = _compare(a, c, 0.0, 0.005)
            if n:
                rows.append({"Baseline": b.label, "Zone": pk, "Proposed schedule": sorted(names)[0],
                             "Baseline schedule": sorted(bn)[0], "Hours that differ": str(n),
                             "First difference": f"{_stamp(p, first)}: {a[first]:.3g} vs {c[first]:.3g}"})
                if pk not in objs:
                    objs.append(pk)
    if rows:
        return Finding("CONS-LSCHED", "Lighting schedules the same", REVIEW,
                       f"Lighting schedules differ in {len(objs)} zone{'s' if len(objs) != 1 else ''}. Appendix G "
                       f"keeps schedules identical; lighting-control savings are taken through Table G3.2 power "
                       f"adjustments (or daylighting simulation), not a reduced schedule.",
                       clause=CL_LIGHT, evidence=rows[:30], objects=objs[:30],
                       fixes=["Use the proposed lighting schedule in the baseline.",
                              "If the proposed schedule was reduced for occupancy sensors, apply the Table G3.2 "
                              "power adjustment instead and document it."])
    return Finding("CONS-LSCHED", "Lighting schedules the same", PASS,
                   "Lighting schedules are identical hour by hour.", clause=CL_LIGHT)


def _outdoor_air(ctx: Context) -> Finding:
    p = ctx.proposed

    def oa(model: ModelRun) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for zs in model.sql.zone_sizes():
            if (zs["load_type"] or "").lower() == "cooling" and zs["oa_flow"] is not None:
                out[zs["zone"].upper()] = float(zs["oa_flow"])
        return out

    pa = oa(p)
    if not pa:
        return Finding("CONS-OA", "Design outdoor air the same", SKIPPED,
                       "No zone sizing results (Sizing:Zone) to compare outdoor air.", clause=CL_OA)
    rows, objs = [], []
    for b in ctx.baselines:
        mapping, _, _ = _zone_map(p, b)
        ba = oa(b)
        for pk, x in sorted(pa.items()):
            y = ba.get(mapping.get(pk, pk))
            if y is None:
                continue
            if max(x, y) > 1e-6 and abs(x - y) / max(x, y) > 0.01:
                rows.append({"Baseline": b.label, "Zone": pk, "Proposed (m³/s)": f"{x:.4f}",
                             "Baseline (m³/s)": f"{y:.4f}", "Difference": f"{(x - y) / max(y, 1e-9):+.0%}"})
                if pk not in objs:
                    objs.append(pk)
    if rows:
        return Finding("CONS-OA", "Design outdoor air the same", REVIEW,
                       f"Design outdoor airflow differs in {len(objs)} zone{'s' if len(objs) != 1 else ''}. "
                       f"Minimum ventilation must match unless an exception applies (e.g. DCV in the proposed "
                       f"where not required).",
                       clause=CL_OA, evidence=rows[:30], objects=objs[:30],
                       fixes=["Use the same DesignSpecification:OutdoorAir rates in both models.",
                              "If the proposed uses demand-controlled ventilation, keep design rates equal and "
                              "document the exception."])
    return Finding("CONS-OA", "Design outdoor air the same", PASS, "Same design outdoor airflow in every zone.",
                   clause=CL_OA)


def _end_use_equipment(ctx: Context) -> Optional[Finding]:
    """Interior equipment energy from the ABUPS end-use table (all fuels)."""
    def eq(model: ModelRun) -> Optional[float]:
        t = model.sql.tabular("AnnualBuildingUtilityPerformanceSummary", "End Uses")
        row = None
        for k in t:
            if k.strip().upper() == "INTERIOR EQUIPMENT":
                row = t[k]
        if row is None:
            return None
        total = 0.0
        for col, (val, units) in row.items():
            if col.strip().lower() in ("water", "district cooling water"):
                continue
            try:
                total += float(val)
            except ValueError:
                pass
        return total

    p = eq(ctx.proposed)
    if p is None:
        return None
    if _comparable(ctx):
        return Finding("CONS-EQUIP-TOTAL", "Interior equipment energy the same", SKIPPED,
                       f"Annual equipment energy {_comparable(ctx)}.", clause=CL_PLUG)
    rows = []
    for b in ctx.baselines:
        v = eq(b)
        if v is not None and max(p, v) > 0 and abs(p - v) / max(p, v) > 0.005:
            rows.append({"Baseline": b.label, "Proposed (GJ)": f"{p:,.2f}", "Baseline (GJ)": f"{v:,.2f}",
                         "Difference": f"{(p - v) / max(v, 1e-9):+.1%}"})
    if rows:
        return Finding("CONS-EQUIP-TOTAL", "Interior equipment energy the same", FAIL,
                       "Annual interior equipment (plug and process) energy differs between proposed and baseline.",
                       clause=CL_PLUG, evidence=rows,
                       fixes=["Receptacle and process loads must be identical unless an exceptional calculation "
                              "is submitted; copy the equipment objects and schedules from the proposed model."])
    return Finding("CONS-EQUIP-TOTAL", "Interior equipment energy the same", PASS,
                   f"Interior equipment energy {p:,.2f} GJ in every model.", clause=CL_PLUG)


_MANAGER_SCHEDULE_FIELD = {
    "AVAILABILITYMANAGER:NIGHTCYCLE": 2,      # fan schedule
    "AVAILABILITYMANAGER:SCHEDULED": 1,
    "AVAILABILITYMANAGER:SCHEDULEDON": 1,
    "AVAILABILITYMANAGER:OPTIMUMSTART": 2,    # fan schedule
}


def _zone_operation_schedules(model: ModelRun) -> Dict[str, str]:
    """Zone (upper) -> name of the schedule that switches the HVAC serving it (upper)."""
    out: Dict[str, str] = {}
    idf = model.idf
    if idf is None or model.sql is None:
        return out
    loop_sched: Dict[str, str] = {}
    for loop in idf.by_class("AirLoopHVAC"):
        lst = idf.find("AvailabilityManagerAssignmentList", loop.get(2)) if loop.get(2) else None
        if lst is None:
            continue
        f = lst.fields[1:]
        for typ, name in zip(f[0::2], f[1::2]):
            i = _MANAGER_SCHEDULE_FIELD.get(typ.strip().upper())
            mgr = idf.find(typ, name) if i is not None else None
            if mgr is not None and mgr.get(i):
                loop_sched[loop.name.upper()] = mgr.get(i).strip().upper()
                break
    zone_equipment_sched: Dict[str, str] = {}
    for o in idf.objects:
        if o.key.startswith("ZONEHVAC:") and o.key not in ("ZONEHVAC:EQUIPMENTLIST", "ZONEHVAC:EQUIPMENTCONNECTIONS",
                                                            "ZONEHVAC:AIRDISTRIBUTIONUNIT"):
            if o.get(1):
                zone_equipment_sched[o.name.upper()] = o.get(1).strip().upper()
    for zone, svc in zone_services(model.sql).items():
        for loop in sorted(svc.air_loops):
            if loop.upper() in loop_sched:
                out[zone] = loop_sched[loop.upper()]
                break
        else:
            for eq in sorted(svc.zone_equipment):
                if eq.upper() in zone_equipment_sched:
                    out[zone] = zone_equipment_sched[eq.upper()]
                    break
    return out


def _hvac_operation(ctx: Context) -> Finding:
    p = ctx.proposed
    why = _comparable(ctx)
    if why:
        return Finding("CONS-HVAC-SCHED", "HVAC operation schedules the same", SKIPPED, f"HVAC schedules {why}.",
                       clause=CL_HVAC)
    pz = _zone_operation_schedules(p)
    if not pz:
        return Finding("CONS-HVAC-SCHED", "HVAC operation schedules the same", SKIPPED,
                       "Could not trace HVAC availability schedules (needs the run's in.idf next to the SQL).",
                       clause=CL_HVAC)
    pser = _schedule_series(p)
    rows, objs = [], []
    compared = 0
    for b in ctx.baselines:
        bz = _zone_operation_schedules(b)
        bser = _schedule_series(b)
        mapping, _, _ = _zone_map(p, b)
        for zone, ps in sorted(pz.items()):
            bs = bz.get(mapping.get(zone, zone))
            a, c = pser.get(ps), bser.get(bs) if bs else None
            if a is None or c is None:
                continue
            compared += 1
            on_a = [1.0 if v > 0 else 0.0 for v in a]
            on_c = [1.0 if v > 0 else 0.0 for v in c]
            n, _, first = _compare(on_a, on_c, 0.0, 0.5)
            if n:
                rows.append({"Baseline": b.label, "Zone": zone, "Proposed schedule": ps, "Baseline schedule": bs,
                             "Hours on/off differ": str(n),
                             "First difference": f"{_stamp(p, first)}: proposed {'on' if on_a[first] else 'off'}, "
                                                 f"baseline {'on' if on_c[first] else 'off'}"})
                if zone not in objs:
                    objs.append(zone)
    if compared == 0:
        return Finding("CONS-HVAC-SCHED", "HVAC operation schedules the same", SKIPPED,
                       "HVAC availability schedules found, but their hourly values were not reported "
                       "(use `prereview run`).", clause=CL_HVAC)
    if rows:
        return Finding("CONS-HVAC-SCHED", "HVAC operation schedules the same", FAIL,
                       f"The HVAC serving {len(objs)} zone{'s' if len(objs) != 1 else ''} runs on different hours "
                       f"in the proposed and baseline models.",
                       clause=CL_HVAC, evidence=rows[:30], objects=objs[:30],
                       fixes=["Use the proposed HVAC operation (fan/availability) schedule for the baseline systems "
                              "serving the same zones."])
    return Finding("CONS-HVAC-SCHED", "HVAC operation schedules the same", PASS,
                   "HVAC systems serving each zone run on the same hours in every model.", clause=CL_HVAC)


def check(ctx: Context) -> List[Finding]:
    if not ctx.pair_ready:
        return []
    ctx = Context(ctx.proposed, ctx.complete_baselines(), ctx.project)
    out = [_site(ctx), _zones(ctx)]
    out.append(_series_check(
        ctx, "CONS-PEOPLE", "Occupancy the same", V_PEOPLE, CL_PEOPLE, "person-h", 0.01, 0.01, FAIL,
        what="Occupancy (people × schedule)",
        fixes=["Use the same People objects (number of people and schedule) in both models."]))
    out.append(_series_check(
        ctx, "CONS-PLUG", "Receptacle loads the same", V_ELEC, CL_PLUG, "kWh", 0.01, 1.0, FAIL, scale=1 / 1000.0,
        what="Electric equipment power (level × schedule)",
        fixes=["Copy ElectricEquipment objects and schedules from the proposed model to the baseline.",
               "Differences are allowed only with an approved exceptional calculation (e.g. ENERGY STAR "
               "equipment), documented separately."]))
    out.append(_series_check(
        ctx, "CONS-GAS-EQUIP", "Gas equipment the same", V_GAS, CL_PLUG, "kWh", 0.01, 1.0, FAIL, scale=1 / 1000.0,
        what="Gas equipment power (level × schedule)", nominal="NominalGasEquipment",
        fixes=["Use identical GasEquipment objects and schedules in both models."]))
    out.append(_series_check(
        ctx, "CONS-OTHER-EQUIP", "Other equipment the same", V_OTHER, CL_PLUG, "kWh", 0.01, 1.0, FAIL,
        scale=1 / 1000.0, what="Other equipment heat gain", nominal="NominalOtherEquipment",
        fixes=["Use identical OtherEquipment objects and schedules in both models."]))
    out.append(_series_check(
        ctx, "CONS-TSTAT-HTG", "Heating setpoints the same", V_SPH, CL_TSTAT, "°C", 0.0, 0.05, FAIL, agg="max",
        what="Heating setpoint schedule",
        fixes=["Use the same thermostat setpoint schedules (including setback) in both models."]))
    out.append(_series_check(
        ctx, "CONS-TSTAT-CLG", "Cooling setpoints the same", V_SPC, CL_TSTAT, "°C", 0.0, 0.05, FAIL, agg="max",
        what="Cooling setpoint schedule",
        fixes=["Use the same thermostat setpoint schedules (including setup) in both models."]))
    eq = _end_use_equipment(ctx)
    if eq is not None:
        out.append(eq)
    out.append(_hvac_operation(ctx))
    out.append(_infiltration(ctx))
    out.append(_lighting_schedules(ctx))
    out.append(_outdoor_air(ctx))
    return out
