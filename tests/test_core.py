"""Unit tests for parsing and the knowledge base (no EnergyPlus needed)."""

from prereview.errfile import group_messages, parse_err, total_count
from prereview.idf import Idf
from prereview.kb import lookup
from prereview.runner import _referenced_schedules, inject_outputs

IDF = """
! a comment
Version, 26.2;
Building, Test, 0.0, City, 0.04, 0.4, FullExterior, 25, 6;   !- trailing comment
Zone, Z1;
Schedule:Compact, OCC, Fraction, Through: 12/31, For: AllDays, Until: 24:00, 1;
Schedule:Compact, UNUSED, Fraction, Through: 12/31, For: AllDays, Until: 24:00, 0;
People, P1, Z1, OCC, People, 10;
OutputControl:Files, No, No, No, No, No, No, No, No, No;
Output:Table:SummaryReports, ZoneComponentLoadSummary;
"""


def test_idf_parse_and_edit():
    m = Idf(IDF)
    assert m.version() == "26.2"
    b = m.first("Building")
    assert b.fields[2] == "City"
    m2 = m.set_field(b, 1, "90")
    assert m2.first("Building").get(1) == "90"
    assert m2.find("people", "p1").get(2) == "OCC"
    m3 = m2.remove_classes(["People"])
    assert m3.by_class("People") == []
    assert "! a comment" in m3.text  # edits keep the rest of the file verbatim


def test_inject_outputs_keeps_user_reports_and_targets_schedules():
    m = inject_outputs(Idf(IDF))
    reports = m.by_class("Output:Table:SummaryReports")
    assert len(reports) == 1
    assert "AllSummary" in reports[0].fields and "ZoneComponentLoadSummary" in reports[0].fields
    assert m.by_class("OutputControl:Files") == []
    assert m.first("Output:SQLite").fields[0] == "SimpleAndTabular"
    keys = {(o.get(0).upper(), o.get(1), o.get(2)) for o in m.by_class("Output:Variable")}
    assert ("OCC", "Schedule Value", "Hourly") in keys
    assert ("UNUSED", "Schedule Value", "Hourly") not in keys
    assert ("*", "Facility Cooling Setpoint Not Met While Occupied Time", "Timestep") in keys
    # idempotent: injecting twice adds nothing new
    again = inject_outputs(m)
    assert len(again.by_class("Output:Variable")) == len(m.by_class("Output:Variable"))
    assert _referenced_schedules(Idf(IDF)) == ["OCC"]


ERR = """Program Version,EnergyPlus, Version 26.2.0-4bd7a1f26f, YMD=2026.11.26 22:51,
   ** Warning ** GetDXCoils: Coil:Heating:DX:SingleSpeed="HP COIL" curve values
   **   ~~~   ** ... Defrost Energy Input Ratio Function of Temperature Curve Name = HPACCOOLEIRFT output is not equal to 1.0 (+ or - 10%) at rated conditions.
   ** Warning ** Coil:Cooling:DX:MultiSpeed "C1" - Air volume flow rate per watt of rated total cooling capacity is out of range at speed 1.
   **   ~~~   **  Environment=ANNUAL, at Simulation time=01/02 09:00 - 09:15
   ** Warning ** The following Report Variables were requested but not generated -- check.rdd file
   **   ~~~   ** Either the IDF did not contain these elements, the variable name is misspelled,
   ************* Key=*, VarName=ZONE AIR TERMINAL VAV DAMPER POSITION, Frequency=Hourly
   ************* There are 26 unused schedules in input.
   ** Severe  ** CheckWarmupConvergence: Loads Initialization, Zone="CORE" did not converge after 1 warmup days.
   ************* ===== Recurring Error Summary =====
   *************  ** Warning ** Coil:Cooling:DX:MultiSpeed "C1" - Air volume flow rate per watt of rated total cooling capacity is out of range at speed 1 error continues...
   *************  **   ~~~   **   This error occurred 1000 total times;
   *************  **   ~~~   **   during Warmup 0 times;
   ************* EnergyPlus Warmup Error Summary. During Warmup: 0 Warning; 1 Severe Errors.
   ************* EnergyPlus Sizing Error Summary. During Sizing: 2 Warning; 0 Severe Errors.
   ************* EnergyPlus Completed Successfully-- 1003 Warning; 1 Severe Errors; Elapsed Time=00hr 00min 14.69sec
"""


def test_err_parse_counts_and_groups():
    e = parse_err(ERR)
    assert e.completed and not e.fatal
    assert e.warnings_total == 1003 and e.severe_total == 1
    assert e.unused == {"schedules": 26}
    assert e.version == "26.2.0"
    groups = group_messages(e.messages)
    flow = [g for g in groups if "per watt" in g[1]]
    assert len(flow) == 1 and flow[0][2] == 1000  # the recurring total includes the first occurrence
    assert groups[0][0] == "Severe"


def test_total_count_counts_unsummarised_copies():
    e = parse_err(ERR)
    msgs = [m for m in e.messages if "per watt" in m.text]
    assert total_count(msgs) == 1000


def test_kb_matches_specific_entries():
    e = parse_err(ERR)
    by_text = {m.text[:30]: lookup(m) for m in e.messages}
    curve = next(k for t, k in by_text.items() if t.startswith("GetDXCoils"))
    assert curve.id == "W-CURVE-RATED"  # not the coil-freezing entry despite the word "Defrost"
    gen = next(k for t, k in by_text.items() if t.startswith("The following Report"))
    assert gen.id == "O-PREREVIEW" and gen.ignore
    warm = next(k for t, k in by_text.items() if t.startswith("CheckWarmup"))
    assert warm.id == "S-WARMUP"


def test_terminated_run():
    text = ("Program Version,EnergyPlus, Version 26.2.0-x,\n"
            "   ** Severe  ** <root>[Building][B][terrain] - \"Suburbz\" - Failed to match against any enum values.\n"
            "   **  Fatal  ** Errors occurred on processing input file. Preceding condition(s) cause termination.\n"
            "   ************* EnergyPlus Terminated--Fatal Error Detected. 0 Warning; 1 Severe Errors; "
            "Elapsed Time=00hr 00min  0.14sec\n")
    e = parse_err(text)
    assert not e.completed and e.fatal
    assert lookup(e.messages[0]).id == "E-ENUM"
