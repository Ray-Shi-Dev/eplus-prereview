"""Small, explicit IDF edits used to seed faults (and to build Appendix G-style baselines)."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from prereview.idf import Idf, IdfObject, format_object

# field index of the numeric value for each design-level method (0-based, Name = 0)
_LEVEL_FIELD = {
    "LIGHTS": {"LIGHTINGLEVEL": 4, "WATTS/AREA": 5, "WATTS/PERSON": 6},
    "ELECTRICEQUIPMENT": {"EQUIPMENTLEVEL": 4, "WATTS/AREA": 5, "WATTS/PERSON": 6},
    "GASEQUIPMENT": {"EQUIPMENTLEVEL": 4, "WATTS/AREA": 5, "WATTS/PERSON": 6},
    "PEOPLE": {"PEOPLE": 4, "PEOPLE/AREA": 5, "AREA/PERSON": 6},
    "ZONEINFILTRATION:DESIGNFLOWRATE": {"FLOW/ZONE": 4, "FLOW/AREA": 5, "FLOW/EXTERIORAREA": 6,
                                        "FLOW/EXTERIORWALLAREA": 6, "AIRCHANGES/HOUR": 7},
}


def _objs(idf: Idf, cls: str, name_contains: Optional[str] = None) -> Iterable[IdfObject]:
    for o in idf.by_class(cls):
        if name_contains is None or name_contains.upper() in o.name.upper() or \
                (len(o.fields) > 1 and name_contains.upper() in o.fields[1].upper()):
            yield o


def scale_level(idf: Idf, cls: str, factor: float, name_contains: Optional[str] = None) -> Idf:
    """Multiply the design level of Lights/Equipment/People/Infiltration objects."""
    fields = _LEVEL_FIELD[cls.upper()]
    targets = [o.name for o in _objs(idf, cls, name_contains)]
    for name in targets:
        o = idf.find(cls, name)
        method = o.get(3).strip().upper()
        i = fields[method]
        val = float(o.get(i))
        if cls.upper() == "PEOPLE" and method == "AREA/PERSON":
            val = val / factor  # fewer m2 per person = more people
        else:
            val = val * factor
        idf = idf.set_field(o, i, f"{val:.8g}")
    return idf


def set_field(idf: Idf, cls: str, name: str, index: int, value: str) -> Idf:
    o = idf.find(cls, name)
    if o is None:
        raise KeyError(f"{cls} {name} not found")
    return idf.set_field(o, index, value)


def set_field_all(idf: Idf, cls: str, index: int, value: str, name_contains: Optional[str] = None) -> Idf:
    for name in [o.name for o in _objs(idf, cls, name_contains)]:
        idf = idf.set_field(idf.find(cls, name), index, value)
    return idf


def rename_reference(idf: Idf, cls: str, name: str, index: int, new_value: str) -> Idf:
    return set_field(idf, cls, name, index, new_value)


def add(idf: Idf, cls: str, fields) -> Idf:
    return idf.append(format_object(cls, list(fields)))


def compact_schedule(name: str, type_limits: str, lines) -> str:
    return format_object("Schedule:Compact", [name, type_limits] + list(lines))


def rotate(idf: Idf, degrees: float) -> Idf:
    b = idf.first("Building")
    base = float(b.get(1) or 0.0)
    return idf.set_field(b, 1, f"{(base + degrees) % 360:.1f}")


def remove(idf: Idf, cls: str, name: str) -> Idf:
    o = idf.find(cls, name)
    return idf.remove([o])


def replace_class(idf: Idf, cls: str, new_objects) -> Idf:
    idf = idf.remove_classes([cls])
    return idf.append(*new_objects)


Mutation = Callable[[Idf], Idf]
