"""Baseline rules of ASHRAE 90.1-2010 Appendix G (LEED v4), with 90.1-2016 (LEED v4.1) where
they differ: orientation runs, fenestration, skylights, baseline HVAC system type, and LEED's
process-energy threshold."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..model import ModelRun
from ..topology import air_loop_components, zone_services
from .base import FAIL, INFO, PASS, REVIEW, SKIPPED, Context, Finding

FT2_PER_M2 = 10.7639
ORIENT = ["North (315 to 45 deg)", "East (45 to 135 deg)", "South (135 to 225 deg)", "West (225 to 315 deg)"]

CL_ROT = {"2010": "ASHRAE 90.1-2010 Table G3.1 #5(a) Orientation — simulate at actual, +90, +180, +270° and average",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(a) Orientation — four orientations unless an exception applies"}
CL_WWR = {"2010": "ASHRAE 90.1-2010 G3.1.1.5(c) / Table G3.1 #5(c) — vertical fenestration = proposed or 40% of "
                  "gross above-grade wall area, whichever is smaller, distributed like the proposed",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(c) / Table G3.1.1-1 — baseline WWR by building type "
                  "(or 40% where not listed), distributed like the proposed"}
CL_SKY = {"2010": "ASHRAE 90.1-2010 Table G3.1 #5(e) — skylights = proposed or 5% of roof, whichever is smaller",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(e) — skylights = proposed or 3% of roof, whichever is smaller"}
CL_SYS = {"2010": "ASHRAE 90.1-2010 G3.1.1 and Tables G3.1.1A/B — baseline HVAC system type",
          "2016": "ASHRAE 90.1-2016 G3.1.1 and Tables G3.1.1-3/-4 — baseline HVAC system type"}
CL_PROC = ("LEED v4/v4.1 EA Prerequisite Minimum Energy Performance — process energy below 25% of baseline "
           "energy cost must be documented")

SYSTEM_NAMES = {
    1: "System 1 – PTAC (packaged terminal AC, hot-water fossil boiler)",
    2: "System 2 – PTHP (packaged terminal heat pump)",
    3: "System 3 – PSZ-AC (packaged rooftop AC, fossil furnace)",
    4: "System 4 – PSZ-HP (packaged rooftop heat pump)",
    5: "System 5 – Packaged VAV with reheat (DX, hot-water fossil boiler)",
    6: "System 6 – Packaged VAV with PFP boxes (DX, electric reheat)",
    7: "System 7 – VAV with reheat (chilled water, hot-water fossil boiler)",
    8: "System 8 – VAV with PFP boxes (chilled water, electric reheat)",
    9: "System 9 – Heating and ventilation (fossil furnace)",
    10: "System 10 – Heating and ventilation (electric)",
}


# ----------------------------------------------------------------------------- orientation
def _rotation(model: ModelRun) -> Optional[float]:
    na = model.north_axis()
    if na is None:
        return None
    return (na + (model.appendix_g_rotation() or 0.0)) % 360.0


def _wwr_table(model: ModelRun) -> Dict[str, Dict[str, float]]:
    t = model.sql.tabular("InputVerificationandResultsSummary", "Window-Wall Ratio")
    out: Dict[str, Dict[str, float]] = {}
    for row, cols in t.items():
        out[row.strip()] = {}
        for col, (val, _) in cols.items():
            try:
                out[row.strip()][col.strip()] = float(val)
            except ValueError:
                pass
    return out


def _orientation_finding(ctx: Context) -> Finding:
    clause = CL_ROT.get(ctx.edition, CL_ROT["2010"])
    p = ctx.proposed
    pr = _rotation(p)
    if ctx.project.rotation_exception:
        return Finding("BASE-ROTATION", "Four baseline orientations", INFO,
                       "Orientation exception declared in the project file (orientation dictated by the site); "
                       "keep the rating authority's approval with the submittal.", clause=clause)
    if ctx.edition == "2016":
        w = _wwr_table(p).get("Window Opening Area", {})
        areas = [w.get(o) for o in ORIENT]
        if all(a is not None for a in areas) and max(areas) > 0:
            spread = (max(areas) - min(areas)) / max(areas)
            if spread < 0.05:
                return Finding("BASE-ROTATION", "Four baseline orientations", INFO,
                               f"Vertical fenestration area varies by {spread:.1%} between orientations (< 5%), so "
                               f"90.1-2016 allows a single baseline orientation.", clause=clause)
    rows = []
    rel = []
    for b in ctx.baselines:
        r = _rotation(b)
        d = None if (r is None or pr is None) else round(((r - pr) % 360.0), 1)
        rel.append(d)
        rows.append({"Baseline run": b.label, "Rotation vs proposed (°)": "—" if d is None else f"{d:.0f}"})
    wanted = {0.0, 90.0, 180.0, 270.0}
    got = {round(x / 90.0) * 90.0 % 360 for x in rel if x is not None and abs(x / 90.0 - round(x / 90.0)) < 0.02}
    missing = sorted(wanted - got)
    odd = [x for x in rel if x is not None and abs(x / 90.0 - round(x / 90.0)) >= 0.02]
    if missing or odd or len(ctx.baselines) < 4:
        msg = f"{len(ctx.baselines)} baseline run{'s' if len(ctx.baselines) != 1 else ''} submitted"
        if missing:
            msg += "; missing orientation" + ("s " if len(missing) > 1 else " ") + \
                   ", ".join(f"+{m:.0f}°" for m in missing)
        if odd:
            msg += "; rotations that are not multiples of 90°: " + ", ".join(f"{x:.0f}°" for x in odd)
        return Finding("BASE-ROTATION", "Four baseline orientations", FAIL, msg + ".", clause=clause,
                       evidence=rows,
                       fixes=["Simulate the baseline at the actual orientation and rotated 90, 180 and 270 degrees "
                              "(Building North Axis +90/+180/+270), then average the four results.",
                              "Pass all four baseline runs to prereview: --baseline B0 B90 B180 B270."])
    return Finding("BASE-ROTATION", "Four baseline orientations", PASS,
                   "Baseline simulated at 0, 90, 180 and 270° relative to the proposed.", clause=clause,
                   evidence=rows)


# ----------------------------------------------------------------------------- fenestration
def _wwr_findings(ctx: Context) -> List[Finding]:
    clause = CL_WWR.get(ctx.edition, CL_WWR["2010"])
    p = ctx.proposed
    pw = _wwr_table(p)
    ptot = pw.get("Above Ground Window-Wall Ratio", {}).get("Total")
    if ptot is None:
        return [Finding("BASE-WWR", "Baseline window-to-wall ratio", SKIPPED,
                        "No Window-Wall Ratio table in the proposed results.", clause=clause)]
    target = min(ptot, 40.0)
    rows, bad, dist_bad = [], False, False
    parea = pw.get("Window Opening Area", {})
    ptotal_area = parea.get("Total") or 0.0
    pshare = [(parea.get(o) or 0.0) / ptotal_area if ptotal_area else 0.0 for o in ORIENT]
    pr = _rotation(p) or 0.0
    for b in ctx.baselines:
        bw = _wwr_table(b)
        btot = bw.get("Above Ground Window-Wall Ratio", {}).get("Total")
        if btot is None:
            continue
        ok = abs(btot - target) <= 0.5
        bad |= not ok
        row = {"Baseline run": b.label, "Proposed WWR": f"{ptot:.1f}%", "Required baseline WWR": f"{target:.1f}%",
               "Baseline WWR": f"{btot:.1f}%", "OK": "yes" if ok else "NO"}
        # distribution by orientation, accounting for the run's rotation
        barea = bw.get("Window Opening Area", {})
        btotal_area = barea.get("Total") or 0.0
        r = _rotation(b)
        if btotal_area and r is not None:
            k = (r - pr) % 360.0
            if abs(k / 90.0 - round(k / 90.0)) < 0.02:
                k = int(round(k / 90.0)) % 4
                bshare = [(barea.get(o) or 0.0) / btotal_area for o in ORIENT]
                worst = max(abs(bshare[(i + k) % 4] - pshare[i]) for i in range(4))
                row["Largest orientation share difference"] = f"{worst:.1%}"
                if worst > 0.03:
                    dist_bad = True
                    row["OK"] = "NO"
        rows.append(row)
    if not rows:
        return []
    if bad or dist_bad:
        why = []
        if bad:
            why.append(f"baseline WWR should be {target:.1f}% (the proposed {ptot:.1f}%"
                       + (", capped at 40%" if ptot > 40 else "") + ")")
        if dist_bad:
            why.append("baseline windows are not distributed by orientation like the proposed")
        return [Finding("BASE-WWR", "Baseline window-to-wall ratio", FAIL, "; ".join(why).capitalize() + ".",
                        clause=clause, evidence=rows,
                        fixes=["Scale the proposed windows uniformly on every façade to reach the required ratio "
                               "(keep their position and proportion by orientation).",
                               "Under 90.1-2016 check Table G3.1.1-1 for the building type's baseline WWR."
                               if ctx.edition == "2016" else
                               "Use the proposed window area when it is under 40% of the gross above-grade wall."])]
    note = "" if ctx.edition == "2010" else " (90.1-2016: compare with Table G3.1.1-1 for listed building types)"
    return [Finding("BASE-WWR", "Baseline window-to-wall ratio", PASS if ctx.edition == "2010" else REVIEW,
                    f"Baseline WWR {target:.1f}% matches the rule and the proposed distribution{note}.",
                    clause=clause, evidence=rows)]


def _skylight_findings(ctx: Context) -> List[Finding]:
    clause = CL_SKY.get(ctx.edition, CL_SKY["2010"])
    cap = 5.0 if ctx.edition == "2010" else 3.0

    def srr(m: ModelRun) -> Optional[float]:
        return m.sql.tab_float("InputVerificationandResultsSummary", "Skylight-Roof Ratio", "Skylight-Roof Ratio",
                               "Total")

    ps = srr(ctx.proposed)
    if ps is None:
        return []
    target = min(ps, cap)
    rows, bad = [], False
    for b in ctx.baselines:
        bs = srr(b)
        if bs is None:
            continue
        ok = abs(bs - target) <= 0.5
        bad |= not ok
        rows.append({"Baseline run": b.label, "Proposed SRR": f"{ps:.1f}%", "Required": f"{target:.1f}%",
                     "Baseline SRR": f"{bs:.1f}%", "OK": "yes" if ok else "NO"})
    if bad:
        return [Finding("BASE-SKYLIGHT", "Baseline skylight-to-roof ratio", FAIL,
                        f"Baseline skylight-to-roof ratio should be {target:.1f}% (proposed {ps:.1f}%, capped at "
                        f"{cap:.0f}%).", clause=clause, evidence=rows,
                        fixes=["Set baseline skylight area to the proposed area or the cap, whichever is smaller."])]
    return [Finding("BASE-SKYLIGHT", "Baseline skylight-to-roof ratio", PASS,
                    f"Baseline skylight-to-roof ratio {target:.1f}%.", clause=clause, evidence=rows)]


# ----------------------------------------------------------------------------- HVAC system type
def _classify_loop(types: List[str], terminal_types: List[str], n_zones: int) -> Optional[int]:
    t = " ".join(x.upper() for x in types)
    term = " ".join(x.upper() for x in terminal_types)
    dx_cool = "COIL:COOLING:DX" in t or "COILSYSTEM:COOLING:DX" in t
    chw_cool = "COIL:COOLING:WATER" in t
    any_cool = dx_cool or chw_cool
    dx_heat = "COIL:HEATING:DX" in t
    fuel_heat = "COIL:HEATING:FUEL" in t or "COIL:HEATING:GAS" in t or "COIL:HEATING:WATER" in t
    elec_heat = "COIL:HEATING:ELECTRIC" in t
    vav = "VAV" in term or "PIU" in term
    pfp = "PARALLELPIU" in term
    if not any_cool:
        if fuel_heat:
            return 9
        if elec_heat or dx_heat:
            return 10
        return None
    if n_zones <= 1 and not vav:
        if dx_heat:
            return 4
        if fuel_heat:
            return 3
        return None
    if dx_cool:
        return 6 if pfp else 5
    if chw_cool:
        return 8 if pfp else 7
    return None


def modeled_systems(model: ModelRun) -> Dict[int, List[str]]:
    """System number -> zones served, inferred from the HVAC topology report."""
    services = zone_services(model.sql)
    loops = air_loop_components(model.sql)
    loop_zones: Dict[str, List[str]] = {}
    loop_terms: Dict[str, List[str]] = {}
    for zone, svc in services.items():
        for loop in svc.air_loops:
            loop_zones.setdefault(loop.upper(), []).append(zone)
            loop_terms.setdefault(loop.upper(), []).extend(t for t in svc.component_types if t.upper().startswith(
                "AIRTERMINAL"))
    out: Dict[int, List[str]] = {}
    for loop, zones in loop_zones.items():
        types = [c["type"] for c in loops.get(loop, [])]
        n = _classify_loop(types, loop_terms.get(loop, []), len(zones))
        if n is not None:
            out.setdefault(n, []).extend(zones)
    for zone, svc in services.items():
        types = " ".join(t.upper() for t in svc.component_types)
        if "ZONEHVAC:PACKAGEDTERMINALAIRCONDITIONER" in types:
            out.setdefault(1, []).append(zone)
        elif "ZONEHVAC:PACKAGEDTERMINALHEATPUMP" in types:
            out.setdefault(2, []).append(zone)
    return out


def _heating_source(ctx: Context) -> Tuple[Optional[str], str]:
    if ctx.project.heating_source in ("fossil", "electric"):
        return ctx.project.heating_source, "project file"
    t = ctx.proposed.sql.tabular("AnnualBuildingUtilityPerformanceSummary", "End Uses")
    for k, cols in t.items():
        if k.strip().upper() == "HEATING":
            fossil = 0.0
            for col, (val, _) in cols.items():
                if col.strip().lower() in ("natural gas", "propane", "fuel oil no 1", "fuel oil no 2", "coal",
                                           "diesel", "gasoline", "district heating water", "district heating steam",
                                           "other fuel 1", "other fuel 2"):
                    try:
                        fossil += float(val)
                    except ValueError:
                        pass
            return ("fossil" if fossil > 0 else "electric"), "inferred from the proposed model's heating fuel"
    return None, ""


def expected_system(edition: str, building_type: str, floors: int, area_ft2: float, heat: str) -> Tuple[int, str]:
    fossil = heat == "fossil"
    if building_type == "residential":
        return (1 if fossil else 2), "residential"
    if building_type == "heated_only_storage":
        return (9 if fossil else 10), "heated-only storage"
    if edition == "2016" and building_type == "retail" and floors <= 2:
        return (3 if fossil else 4), "retail, 2 floors or fewer"
    if floors <= 3 and area_ft2 < 25000:
        return (3 if fossil else 4), "nonresidential, 3 floors or fewer and < 25,000 ft²"
    if (floors in (4, 5) and area_ft2 < 25000) or (floors <= 5 and 25000 <= area_ft2 <= 150000):
        return (5 if fossil else 6), "nonresidential, 4–5 floors and < 25,000 ft², or ≤ 5 floors and 25,000–150,000 ft²"
    return (7 if fossil else 8), "nonresidential, more than 5 floors or > 150,000 ft²"


def _system_finding(ctx: Context) -> Finding:
    clause = CL_SYS.get(ctx.edition, CL_SYS["2010"])
    pj = ctx.project
    heat, heat_basis = _heating_source(ctx)
    area_m2 = ctx.proposed.conditioned_area()
    missing = []
    if pj.floors_above_grade is None:
        missing.append("floors_above_grade")
    if heat is None:
        missing.append("heating_source")
    if area_m2 is None:
        missing.append("conditioned area")
    if missing:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", SKIPPED,
                       "Needs " + ", ".join(missing) + " (set them in project.toml).", clause=clause)
    btype = (pj.building_type or "nonresidential").lower()
    if ctx.edition == "2016" and btype in ("public_assembly", "hospital", "heated_only_storage_2016"):
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", REVIEW,
                       f"90.1-2016 has special rows for {btype.replace('_', ' ')}; check Table G3.1.1-3 by hand.",
                       clause=clause)
    area_ft2 = area_m2 * FT2_PER_M2
    num, why = expected_system(ctx.edition, btype, int(pj.floors_above_grade), area_ft2, heat)
    rows, bad, partial = [], False, False
    for b in ctx.baselines:
        sysmap = modeled_systems(b)
        found = sorted(sysmap)
        nz = {n: len(set(z)) for n, z in sysmap.items()}
        main = max(nz, key=nz.get) if nz else None
        rows.append({"Baseline run": b.label,
                     "Modeled": ", ".join(f"System {n} ({nz[n]} zones)" for n in found) or "not recognised",
                     "Expected": f"System {num}"})
        if main is None:
            partial = True
        elif main != num:
            bad = True
        elif len(found) > 1:
            partial = True
    basis = (f"{why}; {area_ft2:,.0f} ft² conditioned; heating {heat} ({heat_basis}); "
             f"{pj.floors_above_grade} floor{'s' if pj.floors_above_grade != 1 else ''} above grade")
    if bad:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", FAIL,
                       f"Expected {SYSTEM_NAMES[num]} for this building ({basis}).", clause=clause, evidence=rows,
                       fixes=[f"Model the baseline with {SYSTEM_NAMES[num]}.",
                              "If some zones qualify for G3.1.1 exceptions (different use, schedule or loads), "
                              "document which zones and why."])
    if partial:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", REVIEW,
                       f"Main baseline system matches {SYSTEM_NAMES[num]}, but other system types also appear (or the "
                       f"system was not recognised). Check G3.1.1 exceptions for those zones ({basis}).",
                       clause=clause, evidence=rows)
    return Finding("BASE-SYSTEM", "Baseline HVAC system type", PASS,
                   f"{SYSTEM_NAMES[num]}, as required ({basis}).", clause=clause, evidence=rows)


# ----------------------------------------------------------------------------- LEED process energy
def _process_share(ctx: Context) -> Optional[Finding]:
    shares = []
    for b in ctx.baselines:
        t = b.sql.tabular("AnnualBuildingUtilityPerformanceSummary", "End Uses")
        total = proc = 0.0
        for k, cols in t.items():
            name = k.strip().upper()
            if not name or name == "TOTAL END USES":
                continue
            val = 0.0
            for col, (v, _) in cols.items():
                if col.strip().lower() == "water":
                    continue
                try:
                    val += float(v)
                except ValueError:
                    pass
            total += val
            if name in ("INTERIOR EQUIPMENT", "EXTERIOR EQUIPMENT", "REFRIGERATION"):
                proc += val
        if total > 0:
            shares.append(proc / total)
    if not shares:
        return None
    s = sum(shares) / len(shares)
    if s < 0.25:
        return Finding("LEED-PROCESS", "Process energy share", REVIEW,
                       f"Process energy (interior and exterior equipment, refrigeration) is {s:.0%} of baseline site "
                       f"energy. LEED asks for documentation when process energy cost is under 25% of the "
                       f"baseline energy cost (cost basis — check with your rates).",
                       clause=CL_PROC,
                       fixes=["Document that the plug/process load inputs are appropriate for the building "
                              "(sources, equipment schedules), or revise them."])
    return Finding("LEED-PROCESS", "Process energy share", PASS,
                   f"Process energy is {s:.0%} of baseline site energy (≥ 25%).", clause=CL_PROC)


def check(ctx: Context) -> List[Finding]:
    if not ctx.pair_ready:
        return []
    ctx = Context(ctx.proposed, ctx.complete_baselines(), ctx.project)
    out = [_orientation_finding(ctx)]
    out.extend(_wwr_findings(ctx))
    out.extend(_skylight_findings(ctx))
    out.append(_system_finding(ctx))
    proc = _process_share(ctx)
    if proc is not None:
        out.append(proc)
    return out
