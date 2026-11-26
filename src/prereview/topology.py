"""Which HVAC components serve which zone, from the HVACTopology tabular report."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set

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
