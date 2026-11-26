"""Prepare DOE prototype models for the fault lab.

- one annual RunPeriod (the prototypes ship with three one-month periods)
- design days and site location from a DDY file (default: 99.6% heating, 1% cooling DB=>MWB,
  the sizing basis Appendix G G3.1.2.2.1 allows)

Usage: python -I tools/faultlab/prepare.py PROTOTYPE.idf DDY OUT.idf
"""

from __future__ import annotations

import sys

from prereview.idf import Idf, format_object

HEATING_DD = "Ann Htg 99.6% Condns DB"
COOLING_DD = "Ann Clg 1% Condns DB=>MWB"

ANNUAL_RUNPERIOD = format_object("RunPeriod", [
    "Annual", "1", "1", "", "12", "31", "", "Sunday", "No", "No", "No", "Yes", "Yes", "No",
])


def prepare(proto_path: str, ddy_path: str, out_path: str) -> None:
    model = Idf.from_file(proto_path)
    ddy = Idf.from_file(ddy_path)
    days = [o for o in ddy.by_class("SizingPeriod:DesignDay")
            if o.name.endswith(HEATING_DD) or o.name.endswith(COOLING_DD)]
    if len(days) != 2:
        raise SystemExit(f"expected 2 design days in {ddy_path}, found {len(days)}")
    loc = ddy.first("Site:Location")
    model = model.remove_classes(["RunPeriod", "SizingPeriod:DesignDay", "Site:Location"])
    parts = [ANNUAL_RUNPERIOD]
    if loc is not None:
        parts.append(format_object(loc.cls, loc.fields))
    parts += [format_object(d.cls, d.fields) for d in days]
    model = model.append("!- ===== fault lab: annual run period and design days =====", *parts)
    model.write(out_path)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    prepare(sys.argv[1], sys.argv[2], sys.argv[3])
