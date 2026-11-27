"""Baseline rules of ASHRAE 90.1-2010 Appendix G (LEED v4), with 90.1-2016 (LEED v4.1) where they
differ: orientation runs, fenestration, skylights, baseline HVAC system type, and LEED's process-energy
threshold.

Where a check depends on a fact the models cannot state for certain (heating source, climate zone,
building type), prereview infers it, says so, and reports a mismatch as REVIEW instead of FAIL; facts
given in project.toml make the check firm.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..model import ModelRun
from ..topology import air_loop_components, zone_services
from .base import FAIL, INFO, PASS, REVIEW, SKIPPED, Context, Finding

FT2_PER_M2 = 10.7639
ORIENT = ["North (315 to 45 deg)", "East (45 to 135 deg)", "South (135 to 225 deg)", "West (225 to 315 deg)"]

CL_ROT = {"2010": "ASHRAE 90.1-2010 Table G3.1 #5(a) Orientation — simulate at actual, +90, +180, +270° and average",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(a) Orientation — four orientations unless an exception applies"}
CL_WWR = {"2010": "ASHRAE 90.1-2010 Table G3.1 #5(c) — vertical fenestration = proposed or 40% of gross above-grade "
                  "wall area, whichever is smaller, distributed on each face like the proposed",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(c) and Table G3.1.1-1 — WWR from the building-type table; types "
                  "not listed: proposed or 40%, whichever is smaller; distributed like the proposed"}
CL_SKY = {"2010": "ASHRAE 90.1-2010 Table G3.1 #5(e) — skylights = proposed or 5% of roof, whichever is smaller",
          "2016": "ASHRAE 90.1-2016 Table G3.1 #5(e) — skylights = proposed or 3% of roof, whichever is smaller"}
CL_SYS = {"2010": "ASHRAE 90.1-2010 G3.1.1 and Tables G3.1.1A/B — baseline HVAC system type (fossil or hybrid vs "
                  "electric heat)",
          "2016": "ASHRAE 90.1-2016 G3.1.1 and Table G3.1.1-3 — baseline HVAC system type by climate zone"}
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


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


# ----------------------------------------------------------------------------- orientation
def _rotation(model: ModelRun) -> Optional[float]:
    na = model.north_axis()
    if na is None:
        return None
    return (na + (model.appendix_g_rotation() or 0.0)) % 360.0


def _wwr_table(model: ModelRun) -> Dict[str, Dict[str, float]]:
    """Conditioned-space window-wall table when EnergyPlus reports it, else the all-walls table."""
    t = model.sql.tabular("InputVerificationandResultsSummary", "Conditioned Window-Wall Ratio") or \
        model.sql.tabular("InputVerificationandResultsSummary", "Window-Wall Ratio")
    out: Dict[str, Dict[str, float]] = {}
    for row, cols in t.items():
        out[row.strip()] = {}
        for col, (val, _) in cols.items():
            try:
                out[row.strip()][col.strip()] = float(val)
            except ValueError:
                pass
    return out


def _orientation_areas(model: ModelRun) -> Optional[List[float]]:
    w = _wwr_table(model).get("Window Opening Area", {})
    areas = [w.get(o) for o in ORIENT]
    return None if any(a is None for a in areas) else areas  # type: ignore[return-value]


def _rotation_from_windows(p: ModelRun, b: ModelRun) -> Optional[int]:
    """Rotation (0/90/180/270) that maps the proposed window areas by orientation onto the baseline's,
    for baselines rotated by transforming geometry instead of North Axis. None if not unique."""
    pa, ba = _orientation_areas(p), _orientation_areas(b)
    if not pa or not ba or sum(pa) <= 0 or sum(ba) <= 0:
        return None
    ps = [x / sum(pa) for x in pa]
    bs = [x / sum(ba) for x in ba]
    fits = [k for k in range(4) if max(abs(bs[(i + k) % 4] - ps[i]) for i in range(4)) < 0.02]
    if len(fits) == 1:
        return fits[0] * 90
    return None


def _orientation_finding(ctx: Context) -> Finding:
    clause = CL_ROT.get(ctx.edition, CL_ROT["2010"])
    p = ctx.proposed
    pr = _rotation(p)
    if ctx.project.rotation_exception:
        return Finding("BASE-ROTATION", "Four baseline orientations", INFO,
                       "Orientation exception declared in the project file (e.g. orientation dictated by the site); "
                       "keep the rating authority's acceptance with the submittal.", clause=clause)
    if ctx.edition == "2016":
        areas = _orientation_areas(p)
        if areas and max(areas) > 0:
            spread = (max(areas) - min(areas)) / max(areas)
            if spread < 0.05:
                return Finding("BASE-ROTATION", "Four baseline orientations", INFO,
                               f"Vertical fenestration area varies by {spread:.1%} between orientations (< 5%), so "
                               f"90.1-2016 allows a single baseline orientation.", clause=clause)
    rows = []
    rel: List[Optional[float]] = []
    inferred = False
    for b in ctx.baselines:
        r = _rotation(b)
        d = None if (r is None or pr is None) else round(((r - pr) % 360.0), 1)
        how = "North Axis"
        if d is not None and abs(d) < 0.05:
            k = _rotation_from_windows(p, b)
            if k:  # geometry rotated, North Axis left at the proposed value
                d, how, inferred = float(k), "window areas by orientation", True
        rel.append(d)
        rows.append({"Baseline run": b.label, "Rotation vs proposed (°)": "—" if d is None else f"{d:.0f}",
                     "Read from": how})
    snapped = [round(x / 90.0) * 90.0 % 360 for x in rel
               if x is not None and abs(x / 90.0 - round(x / 90.0)) < 0.02]
    odd = [x for x in rel if x is not None and abs(x / 90.0 - round(x / 90.0)) >= 0.02]
    missing = sorted({0.0, 90.0, 180.0, 270.0} - set(snapped))
    dupes = sorted({x for x in snapped if snapped.count(x) > 1})
    note = " Some rotations were read from window areas because North Axis was unchanged." if inferred else ""
    if missing or odd:
        msg = f"{len(ctx.baselines)} baseline run{'s' if len(ctx.baselines) != 1 else ''} submitted"
        if missing:
            msg += "; missing orientation" + ("s " if len(missing) > 1 else " ") + \
                   ", ".join(f"+{m:.0f}°" for m in missing)
        if odd:
            msg += "; rotations that are not multiples of 90°: " + ", ".join(f"{x:.0f}°" for x in odd)
        return Finding("BASE-ROTATION", "Four baseline orientations", FAIL, msg + "." + note, clause=clause,
                       evidence=rows,
                       fixes=["Simulate the baseline at the actual orientation and rotated 90, 180 and 270 degrees "
                              "(Building North Axis +90/+180/+270), then average the four results.",
                              "Pass all four baseline runs to prereview: --baseline B0 B90 B180 B270."])
    if dupes:
        return Finding("BASE-ROTATION", "Four baseline orientations", REVIEW,
                       "All four orientations are present, but " + ", ".join(f"+{x:.0f}°" for x in dupes) +
                       " appears more than once. The baseline result is the average of exactly one run per "
                       "orientation." + note, clause=clause, evidence=rows)
    return Finding("BASE-ROTATION", "Four baseline orientations", PASS,
                   "Baseline simulated at 0, 90, 180 and 270° relative to the proposed." + note, clause=clause,
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
        row = {"Baseline run": b.label, "Proposed WWR": f"{ptot:.1f}%", "Proposed or 40% rule": f"{target:.1f}%",
               "Baseline WWR": f"{btot:.1f}%", "OK": "yes" if ok else "NO"}
        # distribution by orientation, accounting for the run's rotation
        barea = bw.get("Window Opening Area", {})
        btotal_area = barea.get("Total") or 0.0
        r = _rotation(b)
        k_deg = None if r is None else (r - pr) % 360.0
        if k_deg is not None and abs(k_deg) < 0.05:
            k_deg = float(_rotation_from_windows(p, b) or 0)
        if btotal_area and k_deg is not None and abs(k_deg / 90.0 - round(k_deg / 90.0)) < 0.02:
            k = int(round(k_deg / 90.0)) % 4
            bshare = [(barea.get(o) or 0.0) / btotal_area for o in ORIENT]
            worst = max(abs(bshare[(i + k) % 4] - pshare[i]) for i in range(4))
            row["Largest orientation share difference"] = f"{worst:.1%}"
            if worst > 0.03:
                dist_bad = True
                row["OK"] = "NO"
        rows.append(row)
    if not rows:
        return []
    out: List[Finding] = []
    if dist_bad:
        out.append(Finding("BASE-WWR", "Baseline window-to-wall ratio", FAIL,
                           "Baseline windows are not distributed by orientation in the same proportion as the "
                           "proposed.", clause=clause, evidence=rows,
                           fixes=["Scale the proposed windows uniformly on every façade (keep their position and "
                                  "share by orientation) to reach the baseline ratio."]))
    if ctx.edition == "2016":
        msg = (f"Baseline WWR {', '.join(sorted({r['Baseline WWR'] for r in rows}))}. Under 90.1-2016, building types "
               f"listed in Table G3.1.1-1 take their WWR from the table (for example offices of 5,000–50,000 ft²: "
               f"31%); other types use the proposed or 40%, whichever is smaller ({target:.1f}% here). Confirm "
               f"which applies.")
        out.append(Finding("BASE-WWR-2016", "Baseline WWR under 90.1-2016", REVIEW, msg, clause=clause,
                           evidence=rows))
    elif bad:
        got = ", ".join(sorted({r["Baseline WWR"] for r in rows if r["OK"] == "NO"} or
                               {r["Baseline WWR"] for r in rows}))
        out.append(Finding("BASE-WWR", "Baseline window-to-wall ratio", FAIL,
                           f"Baseline WWR is {got}; it should be {target:.1f}% (the proposed {ptot:.1f}%"
                           + (", capped at 40%" if ptot > 40 else "") + ").", clause=clause, evidence=rows,
                           fixes=["Use the proposed window area when it is under 40% of the gross above-grade "
                                  "wall area; otherwise scale windows uniformly down to 40%."]))
    if not out:
        out.append(Finding("BASE-WWR", "Baseline window-to-wall ratio", PASS,
                           f"Baseline WWR {target:.1f}% with the proposed distribution by orientation.",
                           clause=clause, evidence=rows))
    return out


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
_COIL_FIELD = re.compile(r"^COIL:HEATING:", re.I)


def _coil_kind(field_value: str) -> str:
    t = field_value.strip().upper()
    if "WATER" in t:
        return "WATER"
    if "ELECTRIC" in t:
        return "ELECTRIC"
    if ":DX" in t:
        return "DX"
    return "FUEL"


def _object_heating_coil(model: ModelRun, prefix: str, name: str) -> Optional[str]:
    """Heating coil kind named in an IDF object's fields (WATER/ELECTRIC/FUEL/DX/NONE); None if unknown."""
    idf = model.idf
    if idf is None:
        return None
    for o in idf.objects:
        if o.key.startswith(prefix) and o.name.upper() == name.upper():
            for f in o.fields[1:]:
                if _COIL_FIELD.match(f.strip()):
                    return _coil_kind(f)
            return "NONE"
    return None


def _classify_loop(types: List[str], terminal_types: List[str], reheats: List[Optional[str]],
                   n_zones: int) -> Tuple[Optional[int], str, bool]:
    """(system number or None, description, verified). verified=False when reheat types were unknown."""
    t = " ".join(x.upper() for x in types)
    term = " ".join(x.upper() for x in terminal_types)
    dx_cool = "COIL:COOLING:DX" in t or "COILSYSTEM:COOLING:DX" in t
    chw_cool = "COIL:COOLING:WATER" in t
    any_cool = dx_cool or chw_cool
    dx_heat = "COIL:HEATING:DX" in t
    fuel_heat = "COIL:HEATING:FUEL" in t or "COIL:HEATING:GAS" in t or "COIL:HEATING:WATER" in t
    elec_heat = "COIL:HEATING:ELECTRIC" in t
    vav = "VAV" in term
    pfp = "PARALLELPIU" in term
    series_piu = "SERIESPIU" in term
    cv_terminals = "CONSTANTVOLUME" in term or "UNCONTROLLED" in term
    known = [r for r in reheats if r is not None]
    verified = len(known) == len(reheats)
    water_rh = bool(known) and all(r in ("WATER", "NONE") for r in known) and "WATER" in known
    elec_rh = bool(known) and all(r in ("ELECTRIC", "NONE") for r in known) and "ELECTRIC" in known
    if not any_cool:
        if fuel_heat:
            return 9, "heating and ventilation, fuel or hot-water heat", True
        if elec_heat or dx_heat:
            return 10, "heating and ventilation, electric heat", True
        return None, "air loop without heating or cooling coils", True
    if n_zones <= 1 and not (vav or pfp or series_piu):
        if dx_cool and dx_heat:
            return 4, "single-zone packaged heat pump", True
        if dx_cool and fuel_heat:
            return 3, "single-zone packaged AC with fuel/hot-water heat", True
        if dx_cool and elec_heat:
            return None, "single-zone packaged AC with electric resistance heat (not a baseline system)", True
        return None, "single-zone system with chilled-water cooling (not a 90.1-2010 baseline system)", True
    if series_piu:
        return None, "multi-zone system with series fan-powered boxes (not a baseline system)", True
    if cv_terminals and not (vav or pfp):
        return None, "multi-zone constant-volume system (not a baseline system)", True
    kind = "packaged VAV (DX)" if dx_cool else "VAV (chilled water)"
    if pfp:
        if known and not elec_rh:
            return None, f"{kind} with parallel fan-powered boxes but non-electric reheat", True
        return (6 if dx_cool else 8), f"{kind} with parallel fan-powered boxes", verified
    if vav:
        if elec_rh:
            return None, (f"{kind} with electric reheat (System {'5' if dx_cool else '7'} needs hot-water reheat; "
                          f"System {'6' if dx_cool else '8'} needs parallel fan-powered boxes)"), True
        if known and not water_rh:
            return None, f"{kind} with mixed or non-hot-water reheat", True
        return (5 if dx_cool else 7), f"{kind} with hot-water reheat", verified
    return None, "unrecognised air system", True


def classify_systems(model: ModelRun) -> Dict[object, Tuple[List[str], str, bool]]:
    """System number (or a description for non-baseline systems) -> (zones, description, verified)."""
    services = zone_services(model.sql)
    loops = air_loop_components(model.sql)
    loop_zones: Dict[str, List[str]] = {}
    loop_terms: Dict[str, List[str]] = {}
    loop_reheats: Dict[str, List[Optional[str]]] = {}
    for zone, svc in services.items():
        for loop in svc.air_loops:
            loop_zones.setdefault(loop.upper(), []).append(zone)
            loop_terms.setdefault(loop.upper(), []).extend(
                t for t in svc.component_types if t.upper().startswith("AIRTERMINAL"))
            for term in svc.terminals:
                loop_reheats.setdefault(loop.upper(), []).append(_object_heating_coil(model, "AIRTERMINAL:", term))
    out: Dict[object, Tuple[List[str], str, bool]] = {}

    def add(key, zones, desc, verified):
        z, d, v = out.get(key, ([], desc, True))
        out[key] = (z + [x for x in zones if x not in z], d, v and verified)

    for loop, zones in loop_zones.items():
        types = [c["type"] for c in loops.get(loop, [])]
        n, desc, verified = _classify_loop(types, loop_terms.get(loop, []), loop_reheats.get(loop, []), len(zones))
        add(n if n is not None else desc, zones, desc, verified)
    for zone, svc in services.items():
        types = " ".join(t.upper() for t in svc.component_types)
        for name in sorted(svc.zone_equipment):
            heat = _object_heating_coil(model, "ZONEHVAC:", name)
            if "ZONEHVAC:PACKAGEDTERMINALHEATPUMP" in types:
                add(2, [zone], "packaged terminal heat pump", True)
                break
            if "ZONEHVAC:PACKAGEDTERMINALAIRCONDITIONER" in types:
                if heat in (None, "WATER"):
                    add(1, [zone], "PTAC with hot-water heat", heat is not None)
                else:
                    desc = f"PTAC with {heat.lower()} heat (System 1 uses hot water)"
                    add(desc, [zone], desc, True)
                break
    return out


def modeled_systems(model: ModelRun) -> Dict[int, List[str]]:
    """System number -> zones served (baseline-system types only)."""
    return {n: zones for n, (zones, _, _) in classify_systems(model).items() if isinstance(n, int)}


FOSSIL_EQUIPMENT = ("COIL:HEATING:FUEL", "COIL:HEATING:GAS", "BOILER:STEAM", "DISTRICTHEATING",
                    "ZONEHVAC:HIGHTEMPERATURERADIANT")


def _heating_source(ctx: Context) -> Tuple[Optional[str], str, bool]:
    """(fossil | electric, basis, firm). Fossil includes fossil/electric hybrid and purchased heat."""
    if ctx.project.heating_source in ("fossil", "electric"):
        return ctx.project.heating_source, "project file", True
    p = ctx.proposed
    if p.idf is not None:
        found = []
        for o in p.idf.objects:
            k = o.key
            if k.startswith(FOSSIL_EQUIPMENT):
                found.append(f"{o.cls} “{o.name}”")
            elif k == "BOILER:HOTWATER" and o.get(1).strip().upper() not in ("ELECTRICITY", "ELECTRIC"):
                found.append(f"{o.cls} “{o.name}” ({o.get(1)})")
        if found:
            return "fossil", ("inferred from the proposed heating equipment: " + ", ".join(found[:2]) +
                              (" …" if len(found) > 2 else "") + "; hybrid systems count as fossil"), False
        return "electric", "inferred: no fuel-fired or purchased-heat equipment in the proposed model", False
    heat = p.end_uses_gj().get("HEATING", {})
    if heat:
        fossil = sum(v for c, v in heat.items() if c.strip().lower() != "electricity")
        return ("fossil" if fossil > 0 else "electric"), "inferred from the proposed model's heating fuel use", False
    return None, "", False


def _climate_zone(ctx: Context) -> Tuple[Optional[str], str, bool]:
    """('warm' = zones 0-3A | 'cold' = 3B, 3C, 4-8, basis, firm). Explicit zone from project.toml, else
    inferred from the simulated outdoor temperatures (HDD18/CDD10); zone 3 or 4 needs the explicit value."""
    cz = (ctx.project.climate_zone or "").strip().upper()
    m = re.match(r"^([0-8])([ABC]?)$", cz)
    if m:
        num, letter = int(m.group(1)), m.group(2)
        if num <= 2 or (num == 3 and letter == "A"):
            return "warm", f"climate zone {cz} (project file)", True
        if num == 3 and not letter:
            return None, "climate zone 3 needs its letter (A, B or C)", False
        return "cold", f"climate zone {cz} (project file)", True
    odb = next(iter(ctx.proposed.sql.hourly("Site Outdoor Air Drybulb Temperature").values()), None)
    if not odb or len(odb) < 8760 // 2:
        return None, "", False
    days = [odb[i:i + 24] for i in range(0, len(odb) - len(odb) % 24, 24)]
    means = [sum(d) / len(d) for d in days]
    hdd18 = sum(max(0.0, 18.0 - t) for t in means)
    cdd10 = sum(max(0.0, t - 10.0) for t in means)
    basis = f"climate inferred from the weather file (HDD18 {hdd18:,.0f}, CDD10 {cdd10:,.0f})"
    if cdd10 > 3500:
        return "warm", basis + ": zone 0–2, Table G3.1.1-3 column for zones 0–3A", False
    if hdd18 > 3000:
        return "cold", basis + ": zone 5–8, Table G3.1.1-3 column for zones 3B, 3C, 4–8", False
    return None, basis + ": zone 3 or 4 — give the climate zone as a project fact (3A uses the warm column)", False


def expected_system(edition: str, building_type: str, floors: int, area_ft2: float, heat: str) -> Tuple[int, str]:
    """`heat`: fossil|electric under 2010. Under 2016 pass 'fossil' for climate zones 3B, 3C, 4–8 and
    'electric' for zones 0–3A — the two columns of Table G3.1.1-3."""
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
    if ctx.edition == "2016":
        col, basis_txt, firm = _climate_zone(ctx)
        heat = None if col is None else ("fossil" if col == "cold" else "electric")
    else:
        heat, src, firm = _heating_source(ctx)
        basis_txt = f"heating {heat} ({src})" if heat else src
    area_m2 = ctx.proposed.conditioned_area()
    missing = []
    if pj.floors_above_grade is None:
        missing.append("the number of floors above grade")
    if heat is None:
        missing.append("the climate zone" if ctx.edition == "2016" else "the heating source")
    if area_m2 is None:
        missing.append("the conditioned area (from the results)")
    if missing:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", SKIPPED,
                       "Needs " + " and ".join(missing) + " (project facts: the web form or project.toml)." +
                       (f" {_sentence(basis_txt)}." if basis_txt else ""), clause=clause)
    btype = (pj.building_type or "nonresidential").lower()
    if ctx.edition == "2016" and btype in ("public_assembly", "hospital"):
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", REVIEW,
                       f"90.1-2016 has special rows for {btype.replace('_', ' ')}; check Table G3.1.1-3 by hand.",
                       clause=clause)
    area_ft2 = area_m2 * FT2_PER_M2
    num, why = expected_system(ctx.edition, btype, int(pj.floors_above_grade), area_ft2, heat)
    rows, bad, partial, unverified = [], False, False, False
    for b in ctx.baselines:
        systems = classify_systems(b)
        nz = {k: len(set(z)) for k, (z, _, _) in systems.items()}
        main = max(nz, key=nz.get) if nz else None
        rows.append({"Baseline run": b.label,
                     "Modeled": "; ".join(
                         (f"System {k}" if isinstance(k, int) else str(k)) + f" ({nz[k]} zones)" for k in nz)
                     or "not recognised", "Expected": f"System {num}"})
        if main is None:
            partial = True
        elif main != num:
            bad = True
        elif len(systems) > 1:
            partial = True
        if any(not v for (_, _, v) in systems.values()):
            unverified = True
    basis = (f"{why}; {area_ft2:,.0f} ft² conditioned; {basis_txt}; "
             f"{pj.floors_above_grade} floor{'s' if pj.floors_above_grade != 1 else ''} above grade")
    if bad:
        status = FAIL if firm else REVIEW
        msg = f"Expected {SYSTEM_NAMES[num]} for this building ({basis})."
        if not firm:
            msg += " The expectation rests on an inferred input; set it in project.toml to make this check firm."
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", status, msg, clause=clause, evidence=rows,
                       fixes=[f"Model the baseline with {SYSTEM_NAMES[num]}.",
                              "If some zones qualify for G3.1.1 exceptions (different use, schedule or loads), "
                              "document which zones and why."])
    if partial:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", REVIEW,
                       f"Main baseline system matches {SYSTEM_NAMES[num]}, but other system types also appear (or a "
                       f"system was not recognised). Check G3.1.1 exceptions for those zones ({basis}).",
                       clause=clause, evidence=rows)
    if unverified:
        return Finding("BASE-SYSTEM", "Baseline HVAC system type", REVIEW,
                       f"Air-system type matches {SYSTEM_NAMES[num]}, but the reheat/heating coil types could not be "
                       f"verified (the run's IDF was not found next to the results) ({basis}).",
                       clause=clause, evidence=rows)
    return Finding("BASE-SYSTEM", "Baseline HVAC system type", PASS,
                   f"{SYSTEM_NAMES[num]}, as required ({basis}).", clause=clause, evidence=rows)


# ----------------------------------------------------------------------------- LEED process energy
def _process_share(ctx: Context) -> Optional[Finding]:
    shares = []
    for b in ctx.baselines:
        uses = b.end_uses_gj()
        total = sum(sum(v.values()) for k, v in uses.items() if k != "TOTAL END USES")
        proc = sum(sum(uses.get(k, {}).values()) for k in ("INTERIOR EQUIPMENT", "EXTERIOR EQUIPMENT",
                                                            "REFRIGERATION"))
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
                   f"Process energy is {s:.0%} of baseline site energy (≥ 25%; LEED's threshold is on cost).",
                   clause=CL_PROC)


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
