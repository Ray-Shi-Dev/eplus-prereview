"""Which HVAC components serve which zone, from the HVACTopology tabular report."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .sqlout import SqlResults


@dataclass
class ZoneService:
    zone: str
    air_loops: Set[str] = field(default_factory=set)
    terminals: Set[str] = field(default_factory=set)       # terminal unit names
    zone_equipment: Set[str] = field(default_factory=set)  # ZoneHVAC:* and similar
    component_types: Set[str] = field(default_factory=set)


def _table_rows(sql: SqlResults, table: str) -> List[Dict[str, str]]:
    rows = sql.q(
        "select RowName, ColumnName, Value from TabularDataWithStrings where ReportName='HVACTopology' "
        "and TableName=?", (table,))
    by_row: Dict[str, Dict[str, str]] = defaultdict(dict)
    for r, c, v in rows:
        by_row[r][c] = (v or "").strip()
    return [by_row[k] for k in sorted(by_row, key=lambda x: int(x) if x.isdigit() else 0)]


def zone_services(sql: SqlResults) -> Dict[str, ZoneService]:
    """Map upper-case zone name -> what serves it."""
    out: Dict[str, ZoneService] = {}

    def get(zone: str) -> ZoneService:
        k = zone.upper()
        if k not in out:
            out[k] = ZoneService(zone)
        return out[k]

    for row in _table_rows(sql, "Air Loop Demand Side Component Arrangement"):
        zone = row.get("Zone Name", "")
        if not zone:
            continue
        zs = get(zone)
        if row.get("Airloop Name"):
            zs.air_loops.add(row["Airloop Name"])
        if row.get("Terminal Unit Name"):
            zs.terminals.add(row["Terminal Unit Name"])
            zs.component_types.add(row.get("Terminal Unit Type", ""))
    for row in _table_rows(sql, "Zone Equipment Component Arrangement"):
        zone = row.get("Zone Name", "")
        if not zone:
            continue
        zs = get(zone)
        for col_t, col_n in (("Component Type", "Component Name"),
                             ("Sub-Component Type", "Sub-Component Name"),
                             ("Sub-Sub-Component Type", "Sub-Sub-Component Name")):
            if row.get(col_n):
                zs.zone_equipment.add(row[col_n])
                zs.component_types.add(row.get(col_t, ""))
    return out


def air_loop_components(sql: SqlResults) -> Dict[str, List[Dict[str, str]]]:
    """Map upper-case air loop name -> supply side components (type, name)."""
    out: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in _table_rows(sql, "Air Loop Supply Side Component Arrangement"):
        loop = row.get("Airloop Name", "")
        for col_t, col_n in (("Component Type", "Component Name"),
                             ("Sub-Component Type", "Sub-Component Name"),
                             ("Sub-Sub-Component Type", "Sub-Sub-Component Name")):
            if row.get(col_n):
                out[loop.upper()].append({"type": row.get(col_t, ""), "name": row[col_n]})
    return dict(out)


PLANT_EQUIPMENT_PREFIXES = ("CHILLER", "BOILER", "DISTRICTCOOLING", "DISTRICTHEATING", "HEATPUMP:PLANTLOOP",
                            "HEATPUMP:WATERTOWATER", "CHILLERHEATER", "THERMALSTORAGE", "WATERHEATER",
                            "CENTRALHEATPUMPSYSTEM", "HEATEXCHANGER:FLUIDTOFLUID", "PLANTCOMPONENT:TEMPERATURESOURCE")


def plant_loops(sql: SqlResults) -> Dict[str, Dict[str, List[Dict[str, str]]]]:
    """Upper-case plant loop name -> {"supply": [{type, name}], "demand": [{type, name}]}."""
    out: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    for row in _table_rows(sql, "Plant Loop Component Arrangement"):
        loop = row.get("Loop Name", "").upper()
        side = row.get("Side", "").lower()
        ctype, cname = row.get("Component Type", ""), row.get("Component Name", "")
        if not loop or side not in ("supply", "demand") or not cname:
            continue
        d = out.setdefault(loop, {"supply": [], "demand": []})
        item = {"type": ctype.upper(), "name": cname.upper()}
        if item not in d[side]:
            d[side].append(item)
    return out


def plants_serving(sql: SqlResults, component_names: Set[str]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Plant loops (and their plant equipment) that serve any of the given demand components, following
    heat exchangers and other components that sit on the supply side of one loop and the demand side of
    another (e.g. primary/secondary chilled water)."""
    loops = plant_loops(sql)
    names = {n.upper() for n in component_names}
    start = [lp for lp, d in loops.items() if any(c["name"] in names for c in d["demand"])]
    seen: List[str] = []
    equipment: List[Dict[str, str]] = []
    queue = list(start)
    while queue:
        lp = queue.pop(0)
        if lp in seen:
            continue
        seen.append(lp)
        for comp in loops[lp]["supply"]:
            if comp["type"].startswith(PLANT_EQUIPMENT_PREFIXES) and comp not in equipment:
                equipment.append(comp)
            for other, d in loops.items():
                if other not in seen and any(c["name"] == comp["name"] for c in d["demand"]):
                    queue.append(other)
    return seen, equipment
