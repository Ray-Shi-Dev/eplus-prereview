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


@pytest.mark.parametrize("prop,bases,expected", [
    (200, [140, 150, 160, 170], "REVIEW"),   # +45 vs the average, +60 vs one rotation
    (260, [140, 150, 160, 170], "FAIL"),     # +105 vs the average
    (150, [140, 150, 160, 170], "PASS"),
    (100, [250, 260, 270, 280], "PASS"),     # baseline higher than proposed is allowed
])
def test_fifty_hour_rule(tmp_path, prop, bases, expected):
    p = make_run(str(tmp_path), "p", 0, prop)
    bl = [make_run(str(tmp_path), f"b{i}", 0, v) for i, v in enumerate(bases)]
    assert status(unmet.check(ctx(p, bl)), "UMLH-50").status == expected


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
