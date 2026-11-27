"""Rule logic on small synthetic EnergyPlus-like result files."""

import os

import pytest

from prereview.model import ModelRun
from prereview.rules import baseline2010, consistency, unmet
from prereview.rules.base import Context, ProjectInfo

from synth import SynthSql, err_text, facility_series, write

ABUPS = "AnnualBuildingUtilityPerformanceSummary"
NOTMET = "Comfort and Setpoint Not Met Summary"


def make_run(tmp, name, rep_h, rep_c, h_steps=None, c_steps=None, hours=96, people=None, weather="Taipei TMYx",
             north=0.0, sim_hours=8760.0, completed=True):
    folder = os.path.join(tmp, name)
    os.makedirs(folder, exist_ok=True)
    s = SynthSql(os.path.join(folder, "eplusout.sql"), hours=hours, timesteps=4)
    s.general(weather=weather, hours=sim_hours, north=north)
    s.zone(1, "Z1", 100.0)
    s.zone(2, "Z2", 100.0)
    s.tab(ABUPS, NOTMET, "Time Setpoint Not Met During Occupied Heating", "Facility", f"{rep_h:.2f}", "hr")
    s.tab(ABUPS, NOTMET, "Time Setpoint Not Met During Occupied Cooling", "Facility", f"{rep_c:.2f}", "hr")
    s.tab("LEEDsummary", "EAp2-2. Advisory Messages", "Number of hours not met", "Data", f"{rep_h + rep_c:.2f}")
    s.tab(ABUPS, "Building Area", "Net Conditioned Building Area", "Area", "200.00", "m2")
    s.tab(ABUPS, "Building Area", "Total Building Area", "Area", "200.00", "m2")
    if h_steps is not None:
        h, c = facility_series(h_steps, c_steps, hours * 4, dt=1.0)
        s.variable("Facility Heating Setpoint Not Met While Occupied Time", "Facility", h, "Zone Timestep", "hr")
        s.variable("Facility Cooling Setpoint Not Met While Occupied Time", "Facility", c, "Zone Timestep", "hr")
    for z, series in (people or {}).items():
        s.variable("Zone People Occupant Count", z, series)
    s.close()
    write(os.path.join(folder, "eplusout.err"), err_text([], completed=completed))
    return ModelRun.load(folder, "proposed", name)


def ctx(p, bl, edition="2010", **kw):
    for b in bl:
        b.role = "baseline"
    return Context(p, bl, ProjectInfo(edition=edition, **kw))


def status(findings, rule, model=None):
    got = [f for f in findings if f.rule_id == rule and (model is None or f.model == model)]
    assert got, f"no {rule} finding"
    return got[0]


def test_union_below_sum_is_review(tmp_path):
    # heating 200 h, cooling 150 h, 80 h overlap -> exact count 270 h, EnergyPlus total 350 h
    m = make_run(str(tmp_path), "p", 200, 150, list(range(0, 200)), list(range(120, 270)))
    st = unmet.compute(m, diagnose=False)
    assert st.union_hours == 270 and st.reported_sum == 350 and st.union_matches_reported
    f = status(unmet.check(ctx(m, [])), "UMLH-300", "p")
    assert f.status == "REVIEW"


def test_union_above_limit_fails(tmp_path):
    m = make_run(str(tmp_path), "p", 200, 120, list(range(0, 200)), list(range(200, 320)))
    assert unmet.compute(m, diagnose=False).union_hours == 320
    assert status(unmet.check(ctx(m, [])), "UMLH-300", "p").status == "FAIL"


def test_without_timestep_output_uses_reported_values(tmp_path):
    m = make_run(str(tmp_path), "a", 200, 150)
    assert status(unmet.check(ctx(m, [])), "UMLH-300", "a").status == "REVIEW"  # sum > 300, each <= 300
    m2 = make_run(str(tmp_path), "b", 0, 320)
    assert status(unmet.check(ctx(m2, [])), "UMLH-300", "b").status == "FAIL"
    m3 = make_run(str(tmp_path), "c", 10, 20)
    assert status(unmet.check(ctx(m3, [])), "UMLH-300", "c").status == "PASS"


@pytest.mark.parametrize("edition", ["2010", "2016"])
@pytest.mark.parametrize("prop,bases", [(200, [140, 150, 160, 170]), (260, [140, 150, 160, 170]),
                                        (100, [250, 260, 270, 280])])
def test_no_fifty_hour_rule_after_2007(tmp_path, edition, prop, bases):
    """90.1-2010/2016 G3.1.2.3 limits each model to 300 h; the 50-hour differential was 90.1-2007 only.
    A proposed model at 260 h against baselines at 140-170 h complies."""
    p = make_run(str(tmp_path), "p", 0, prop)
    bl = [make_run(str(tmp_path), f"b{i}", 0, v) for i, v in enumerate(bases)]
    out = unmet.check(ctx(p, bl, edition=edition))
    assert status(out, "UMLH-DIFF").status == "INFO"
    assert not [f for f in out if f.status == "FAIL"]
    assert "G3.1.2.3" in status(out, "UMLH-300", "p").clause


def test_people_mismatch_fails_and_names_zone(tmp_path):
    occ = [0.0] * 8 + [10.0] * 10 + [0.0] * 6
    occ = occ * 4
    alt = [x * 1.2 for x in occ]
    p = make_run(str(tmp_path), "p", 0, 0, people={"Z1": occ, "Z2": occ})
    b = make_run(str(tmp_path), "b", 0, 0, people={"Z1": occ, "Z2": alt})
    f = status(consistency.check(ctx(p, [b])), "CONS-PEOPLE")
    assert f.status == "FAIL" and f.objects == ["Z2"]
    same = make_run(str(tmp_path), "s", 0, 0, people={"Z1": occ, "Z2": occ})
    assert status(consistency.check(ctx(p, [same])), "CONS-PEOPLE").status == "PASS"


def test_unequal_run_lengths_skip_hourly_comparisons(tmp_path):
    occ = ([0.0] * 8 + [10.0] * 10 + [0.0] * 6) * 4
    p = make_run(str(tmp_path), "p", 0, 0, people={"Z1": occ})
    b = make_run(str(tmp_path), "b", 0, 0, hours=48, people={"Z1": occ[:48]}, sim_hours=4344)
    out = consistency.check(ctx(p, [b]))
    assert status(out, "CONS-SITE").status == "FAIL"
    assert status(out, "CONS-PEOPLE").status == "SKIPPED"


def test_failed_run_is_left_to_health(tmp_path):
    p = make_run(str(tmp_path), "p", 0, 0)
    b = make_run(str(tmp_path), "b", 0, 0, completed=False)
    c = ctx(p, [b])
    assert c.simulated == [p] and not c.pair_ready
    assert consistency.check(c) == []


@pytest.mark.parametrize("rot,expected", [((0, 90, 180, 270), "PASS"), ((0, 90, 90, 270), "FAIL"),
                                          ((0,), "FAIL"), ((10, 100, 190, 280), "PASS")])
def test_rotations(tmp_path, rot, expected):
    p = make_run(str(tmp_path), "p", 0, 0, north=rot[0])
    bl = [make_run(str(tmp_path), f"b{i}", 0, 0, north=r) for i, r in enumerate(rot)]
    f = baseline2010._orientation_finding(ctx(p, bl))
    assert f.status == expected


@pytest.mark.parametrize("edition,btype,floors,ft2,heat,num", [
    ("2010", "nonresidential", 1, 5000, "electric", 4),
    ("2010", "nonresidential", 3, 24000, "fossil", 3),
    ("2010", "nonresidential", 4, 20000, "fossil", 5),
    ("2010", "nonresidential", 5, 100000, "electric", 6),
    ("2010", "nonresidential", 6, 100000, "fossil", 7),
    ("2010", "nonresidential", 2, 200000, "electric", 8),
    ("2010", "residential", 10, 200000, "fossil", 1),
    ("2010", "heated_only_storage", 1, 10000, "electric", 10),
    ("2016", "retail", 2, 60000, "fossil", 3),
])
def test_expected_system(edition, btype, floors, ft2, heat, num):
    assert baseline2010.expected_system(edition, btype, floors, ft2, heat)[0] == num


def test_cli_check_writes_reports(tmp_path):
    import json

    from prereview.cli import main
    occ = ([0.0] * 8 + [10.0] * 10 + [0.0] * 6) * 4
    make_run(str(tmp_path), "p", 0, 350, people={"Z1": occ})
    for i, r in enumerate((0, 90, 180, 270)):
        make_run(str(tmp_path), f"b{r}", 0, 100, people={"Z1": occ}, north=r)
    html_out, json_out = str(tmp_path / "r.html"), str(tmp_path / "r.json")
    code = main(["check", "--proposed", str(tmp_path / "p"), "--baseline"] +
                [str(tmp_path / f"b{r}") for r in (0, 90, 180, 270)] +
                ["--html", html_out, "--json", json_out, "--floors", "1", "--heating", "electric"])
    assert code == 1  # 350 unmet hours -> FAIL -> non-zero exit for CI use
    data = json.load(open(json_out))
    rules = {(f["rule_id"], f["status"]) for f in data["findings"]}
    assert ("UMLH-300", "FAIL") in rules and ("BASE-ROTATION", "PASS") in rules
    html = open(html_out, encoding="utf-8").read()
    assert "What to fix first" in html and "Unmet load hours" in html
    labels = [m["label"] for m in data["models"]]
    assert labels == ["Proposed", "Baseline 0°", "Baseline 90°", "Baseline 180°", "Baseline 270°"]


def test_reporting_tolerance(tmp_path):
    from prereview.idf import Idf
    p = make_run(str(tmp_path), "p", 0, 10)
    b = make_run(str(tmp_path), "b", 0, 10)
    p.idf = Idf("OutputControl:ReportingTolerances, 1.11, 1.11;")
    b.idf = Idf("Version, 26.2;")
    f = status(unmet.check(ctx(p, [b])), "UMLH-TOL")
    assert f.status == "FAIL"  # 1.11 K vs EnergyPlus default 0.2 K
    b.idf = Idf("OutputControl:ReportingTolerances, 1.11, 1.11;")
    assert status(unmet.check(ctx(p, [b])), "UMLH-TOL").status == "PASS"
    p.idf = b.idf = Idf("OutputControl:ReportingTolerances, 2.0, 2.0;")
    assert status(unmet.check(ctx(p, [b])), "UMLH-TOL").status == "REVIEW"


def test_plant_traversal_follows_heat_exchangers(tmp_path):
    from prereview.sqlout import SqlResults
    from prereview.topology import plants_serving
    s = SynthSql(str(tmp_path / "t.sql"), hours=2)
    rows = [("SEC", "Demand", "COIL:COOLING:WATER", "C1"), ("SEC", "Supply", "HEATEXCHANGER:FLUIDTOFLUID", "HX"),
            ("PRI", "Demand", "HEATEXCHANGER:FLUIDTOFLUID", "HX"), ("PRI", "Supply", "CHILLER:ELECTRIC:EIR", "CH1"),
            ("CW", "Demand", "CHILLER:ELECTRIC:EIR", "CH1"), ("CW", "Supply", "COOLINGTOWER:SINGLESPEED", "T1"),
            ("HW", "Demand", "COIL:HEATING:WATER", "R1"), ("HW", "Supply", "BOILER:HOTWATER", "B1")]
    for i, (loop, side, ctype, name) in enumerate(rows, start=1):
        for col, val in (("Loop Name", loop), ("Side", side), ("Component Type", ctype), ("Component Name", name)):
            s.tab("HVACTopology", "Plant Loop Component Arrangement", str(i), col, val)
    sql = SqlResults(s.close())
    loops, equipment = plants_serving(sql, {"c1"})
    assert loops == ["SEC", "PRI", "CW"]
    assert [e["name"] for e in equipment] == ["HX", "CH1"]
    loops, equipment = plants_serving(sql, {"R1"})
    assert loops == ["HW"] and [e["name"] for e in equipment] == ["B1"]


def test_ip_units_are_converted(tmp_path):
    """OutputControl:Table:Style InchPound puts ft2, °F and kBtu in the tabular data."""
    folder = tmp_path / "ip"
    folder.mkdir()
    s = SynthSql(str(folder / "eplusout.sql"), hours=2)
    s.general()
    s.tab(ABUPS, "Building Area", "Net Conditioned Building Area", "Area", "10000.00", "ft2")
    s.tab(ABUPS, "End Uses", "Interior Equipment", "Electricity", "1000.00", "kBtu")
    s.tab(ABUPS, "End Uses", "Interior Equipment", "Natural Gas", "100.00", "kBtu")
    s.tab(ABUPS, "End Uses", "Interior Equipment", "Water", "5.00", "gal")
    s.tab("ClimaticDataSummary", "SizingPeriod:DesignDay", "X ANN CLG 1% CONDNS DB=>MWB", "Maximum Dry Bulb", "95.00", "F")
    s.tab("ClimaticDataSummary", "SizingPeriod:DesignDay", "X ANN CLG 1% CONDNS DB=>MWB", "Humidity Type",
          "Wetbulb [F]", "")
    s.tab("ClimaticDataSummary", "SizingPeriod:DesignDay", "X ANN CLG 1% CONDNS DB=>MWB", "Humidity Value", "75.00", "")
    s.close()
    write(str(folder / "eplusout.err"), err_text([]))
    m = ModelRun.load(str(folder), "proposed", "ip")
    assert abs(m.conditioned_area() - 929.0304) < 0.01
    eq = m.end_uses_gj()["INTERIOR EQUIPMENT"]
    assert abs(sum(eq.values()) - 1.1 * 1.055056) < 1e-6
    dd = unmet._design_days(m)[0]
    assert abs(dd["max_db"] - 35.0) < 1e-6 and abs(dd["humidity"] - 23.8889) < 1e-3


def test_2016_system_type_follows_climate_zone(tmp_path):
    from prereview.rules import baseline2010
    p = make_run(str(tmp_path), "p", 0, 0)
    for cz, warm in (("2A", True), ("1A", True), ("3A", True), ("3B", False), ("4C", False), ("5A", False)):
        c = ctx(p, [], edition="2016", climate_zone=cz)
        col, basis, firm = baseline2010._climate_zone(c)
        assert firm and col == ("warm" if warm else "cold"), cz
    c = ctx(p, [], edition="2016", climate_zone="3")
    assert baseline2010._climate_zone(c)[0] is None


def test_heating_source_inferred_from_equipment(tmp_path):
    from prereview.idf import Idf
    from prereview.rules import baseline2010
    p = make_run(str(tmp_path), "p", 0, 0)
    p.idf = Idf("Coil:Heating:Fuel, Furnace 1, Always On, NaturalGas, 0.8, autosize;\n"
                "Coil:Heating:Electric, Reheat 1, Always On, 1.0, autosize;")
    src, basis, firm = baseline2010._heating_source(ctx(p, []))
    assert src == "fossil" and not firm and "hybrid" in basis
    p.idf = Idf("Boiler:HotWater, B1, Electricity, autosize, 1.0;")
    assert baseline2010._heating_source(ctx(p, []))[0] == "electric"
    assert baseline2010._heating_source(ctx(p, [], heating_source="fossil"))[:3:2] == ("fossil", True)


@pytest.mark.parametrize("types,terms,reheats,nz,expected", [
    (["Coil:Cooling:DX:TwoSpeed", "Coil:Heating:Fuel"], ["AirTerminal:SingleDuct:VAV:Reheat"], ["WATER"], 5, 5),
    (["Coil:Cooling:DX:TwoSpeed", "Coil:Heating:Fuel"], ["AirTerminal:SingleDuct:VAV:Reheat"], ["ELECTRIC"], 5, None),
    (["Coil:Cooling:Water", "Coil:Heating:Water"], ["AirTerminal:SingleDuct:VAV:Reheat"], ["WATER"], 8, 7),
    (["Coil:Cooling:Water"], ["AirTerminal:SingleDuct:ParallelPIU:Reheat"], ["ELECTRIC"], 8, 8),
    (["Coil:Cooling:DX:SingleSpeed", "Coil:Heating:DX:SingleSpeed"], ["AirTerminal:SingleDuct:Uncontrolled"],
     [None], 1, 4),
    (["Coil:Cooling:DX:SingleSpeed", "Coil:Heating:Fuel"], ["AirTerminal:SingleDuct:ConstantVolume:NoReheat"],
     ["NONE", "NONE"], 2, None),
])
def test_system_classifier(types, terms, reheats, nz, expected):
    from prereview.rules import baseline2010
    assert baseline2010._classify_loop(types, terms, reheats, nz)[0] == expected


def test_files_are_paired_by_name(tmp_path):
    from prereview.rules import health
    folder = tmp_path / "shared"
    folder.mkdir()
    for stem in ("Proposed", "Baseline_0"):
        s = SynthSql(str(folder / f"{stem}.sql"), hours=2)
        s.general()
        s.close()
    write(str(folder / "Proposed.err"), err_text([]))
    write(str(folder / "Baseline_0.idf"), "Version, 26.2;")
    with pytest.raises(ValueError):
        ModelRun.load(str(folder), "proposed")
    m = ModelRun.load(str(folder / "Proposed.sql"), "proposed")
    assert m.err is not None and m.idf is None  # Baseline_0.idf is not picked up for Proposed
    empty = tmp_path / "empty"
    empty.mkdir()
    e = ModelRun.load(str(empty), "proposed")
    f = [x for x in health.check_model(e) if x.rule_id == "HEALTH-RUN"][0]
    assert f.status == "FAIL" and "No EnergyPlus results" in f.message


def test_completed_run_without_sql_is_flagged_not_crashed(tmp_path):
    from prereview.rules import health
    folder = tmp_path / "proposed"
    folder.mkdir()
    write(str(folder / "eplusout.err"), err_text([]))
    m = ModelRun.load(str(folder), "proposed", "Proposed")
    by_rule = {f.rule_id: f for f in health.check_model(m)}
    assert by_rule["HEALTH-RUN"].status == "PASS" and "26.2.0" in by_rule["HEALTH-RUN"].message
    assert by_rule["HEALTH-SQL"].status == "FAIL"
