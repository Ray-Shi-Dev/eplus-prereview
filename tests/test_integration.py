"""End-to-end runs with a real EnergyPlus (skipped when it is not installed)."""

import glob
import os

import pytest

from prereview.runner import RunError, find_energyplus


def _energyplus():
    try:
        return find_energyplus()
    except RunError:
        return None


EXE = _energyplus()
pytestmark = pytest.mark.skipif(EXE is None, reason="EnergyPlus not installed")


def test_hvactemplate_models_run_in_parallel_with_outputs(tmp_path):
    from prereview.cli import main
    from prereview.idf import Idf
    install = os.path.dirname(os.path.realpath(EXE))
    examples = [os.path.join(install, "ExampleFiles", n) for n in
                ("HVACTemplate-5ZonePackagedVAV.idf", "HVACTemplate-5ZoneVAVWaterCooled.idf",
                 "HVACTemplate-5ZoneFanCoil-DOAS.idf")]
    weather = glob.glob(os.path.join(install, "WeatherData", "USA_IL_Chicago*.epw"))
    if not all(os.path.isfile(e) for e in examples) or not weather:
        pytest.skip("example files not found")
    out = str(tmp_path / "out")
    code = main(["run", "--proposed", examples[0], "--baseline", examples[1], examples[2], "--weather", weather[0],
                 "--out", out, "--jobs", "3", "--floors", "1", "--heating", "fossil"])
    assert code == 1  # the examples simulate 2 design days only -> HEALTH-YEAR FAIL, but nothing crashes
    for run in ("proposed", "baseline-1", "baseline-2"):
        idf = Idf.from_file(os.path.join(out, run, "in.idf"))
        assert not idf.has_class_prefix("HVACTemplate:")  # expanded before outputs were added
        keys = {(o.get(1), o.get(2)) for o in idf.by_class("Output:Variable")}
        assert any(k == ("System Node Temperature", "Hourly") for k in keys)
        assert os.path.isfile(os.path.join(out, run, "eplusout.sql"))
    assert os.path.isfile(os.path.join(out, "prereview.html"))
