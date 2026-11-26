# eplus-prereview

**Find the review comments before the reviewer does.** `prereview` reads EnergyPlus results for an
ASHRAE 90.1 Appendix G / LEED energy model (one proposed run plus the baseline runs) and reports what a
reviewer is likely to flag. Each finding comes with the numbers behind it, the clause it relates to, the
zones and objects involved, and what to change.

It is built for the checks that cost modelers the most time:

1. **Unmet load hours.** prereview measures the time in which *any* zone is unmet (the 90.1 definition, counted
   at the zone timestep as EnergyPlus counts it) and checks it against the 300-hour limit of G3.1.2.3 for the
   proposed and each baseline run. Then it explains where the hours come from, zone by zone. It separates a
   coil or plant that cannot hold its supply temperature from a VAV box stuck at maximum airflow, a system that
   is switched off while people are in the building, morning recovery, equipment hard-sized below its design
   size, and design days that do not match the climate.
2. **Baseline vs proposed items that must match.** It compares occupancy, plug and process loads, thermostat
   setpoints, HVAC operating hours, infiltration, lighting schedules, outdoor air, weather, run period and
   program version hour by hour, using the values EnergyPlus actually simulated.
3. **Simulation health.** It checks that each run completed and covered a full year. It sorts severe errors
   and warnings into groups and explains each group from a knowledge base: what the message means, whether
   it changes results, and how to fix it.
4. **Baseline rules (90.1-2010, with 2016 differences).** Four orientations, window-to-wall ratio and its
   distribution, skylights, the baseline HVAC system type (2010: by heating source; 2016: by climate zone), and
   LEED's 25% process-energy threshold. When a rule depends on a fact the models cannot state for certain
   (heating source, climate zone), prereview infers it, says so, and reports a mismatch as REVIEW; put the fact
   in `project.toml` to make the check firm.

```
FAIL   UMLH-300  Unmet load hours ≤ 300 [Proposed]: 1,192.8 unmet load hours: time with at least one zone
       outside setpoint while occupied (limit 300 for the proposed and each baseline). Main contributor:
       PERIMETER_ZN_1 (cooling, 1186 h) — Capacity-limited, and a component serving the zone is hard-sized
       below its design size. Coil:Cooling:DX:SingleSpeed “PSZ-AC:2 HEAT PUMP DX COOLING COIL” Gross Rated
       Total Cooling Capacity is 3,432 W, 50% of its design size (6,864).
FAIL   CONS-PLUG Receptacle loads the same: Electric equipment power (level × schedule) differs between
       proposed and baseline in 5 zones.
```

## Quick start

```bash
pip install -e .            # Python ≥ 3.9, no dependencies
# simulate (prereview adds the hourly outputs it needs; results are unchanged), then review:
prereview run --proposed proposed.idf --baseline b000.idf b090.idf b180.idf b270.idf \
              --weather site.epw --project project.toml --out review/
# or review runs you already have (folders with eplusout.sql + eplusout.err):
prereview check --proposed runs/proposed --baseline runs/b000 runs/b090 runs/b180 runs/b270 \
                --project project.toml --html review.html --json review.json
```

`project.toml` holds the facts that cannot be read from the models:

```toml
name = "Office tower, Taipei"
edition = "2010"                 # 90.1-2010 (LEED v4) or 2016 (LEED v4.1)
building_type = "nonresidential" # nonresidential | residential | retail | heated_only_storage | public_assembly | hospital
floors_above_grade = 12
heating_source = "fossil"        # 2010: fossil (incl. hybrid, purchased heat) | electric; inferred if omitted
climate_zone = "2A"              # 2016: ASHRAE 169 zone for Table G3.1.1-3; inferred from the weather if omitted
rotation_exception = false       # true if the rating authority accepted a single orientation
```

The exit code is 1 when any finding is FAIL, so the tool can run in a script or in CI.

### What `prereview run` adds to your model

`prereview run` copies each IDF into the output folder and appends output requests. It never edits the
building, HVAC, schedules or simulation settings. HVACTemplate models are expanded first (ExpandObjects), so
the outputs can name the real HVAC objects. It adds:

- `Output:SQLite` (SimpleAndTabular, SI units in the SQL even when your HTML tables are in IP) and the
  `AllSummary` reports, keeping any reports you already asked for.
- Hourly zone variables: unmet time, delivered heating and cooling, setpoints, temperature, occupancy, lights,
  equipment, VAV damper position, and outdoor dry-bulb and wet-bulb temperature.
- Facility unmet time at the zone timestep, which gives the exact count of hours with any zone unmet.
- `Schedule Value` for the schedules that loads, thermostats and HVAC availability refer to.
- Node temperature and setpoint at each air loop's supply outlet and each plant loop's setpoint node.

`prereview check` also works on runs that lack these outputs, and on tabular output in SI or IP units. When
it uses less exact data, the report says so: for example, it judges unmet hours on EnergyPlus's heating +
cooling total instead of the any-zone time. A folder must hold one run (eplusout.* or X.sql/X.err/X.idf); for
a folder with several runs, pass each run's .sql file.

## How the unmet-hours check works

90.1-2010 and 2016 (G3.1.2.3) limit unmet load hours to 300 for the proposed design and for each baseline
run. The 50-hour proposed-minus-baseline rule was in 90.1-2007 (LEED 2009) only; prereview shows the
difference for information.

EnergyPlus reports occupied heating and occupied cooling not-met time separately, and the LEED summary table
adds the two together, which counts twice any time in which one zone is short of heat while another is short of
cooling. Under 90.1 an unmet load hour is one in which *one or more zones* is unmet. prereview rebuilds the
any-zone figure from EnergyPlus's facility-level timestep output, time-weighted exactly as EnergyPlus counts
(its heating and cooling totals match the tabular values). If the summed figure is over 300 but the any-zone
figure is not, the finding is REVIEW, so the modeler can report the right figure with an explanation. The
report also shows, for information, the stricter count of whole clock hours that contain any unmet timestep.

For the zones that contribute most, the diagnosis uses hourly data to show why each zone is unmet:

| Cause | Evidence used |
|---|---|
| No heating/cooling delivered | the zone's peak delivered heating/cooling is near zero or < 10% of its design load |
| System off while occupied | no delivery during most unmet hours, even though the system delivers at other times |
| Hard-sized below design | a capacity or flow field of equipment serving the zone (coils, terminals, unitary systems, chillers, boilers) is user-specified below EnergyPlus's design size (ComponentSizes), or below the zone design load when no design size is reported |
| Coil can't hold supply-air setpoint | the air loop's supply-outlet temperature misses its setpoint during the unmet hours |
| Plant can't hold supply-water setpoint | the chilled- or hot-water loop serving the zone's coils (followed through heat exchangers) misses its setpoint |
| Terminal at maximum airflow | the VAV damper is fully open; also notes when the zone was sized for a colder supply-air temperature than the system supplies |
| Weather beyond design day | outdoor dry-bulb or wet-bulb is beyond the sizing design day during the unmet hours |
| Morning recovery | most unmet hours fall in the first two occupied hours of the day |
| Small misses / controls | the zone misses setpoint by less than 0.5 K, or delivery is below peak with no system limit |

## Quality bar: the seeded-fault lab

`tools/faultlab` builds proposed/baseline sets from the DOE prototype buildings that ship with EnergyPlus.
For each fault, it seeds one error into one model, simulates the set, and checks that prereview reports that
error with the correct rule and, for unmet hours, the correct zone and cause. The clean sets must produce no
FAIL findings.

Latest results (EnergyPlus 26.2):

- Taipei (hot-humid): **33 of 33** faults detected; **0** FAIL findings on the 3 clean buildings (each with
  a proposed model and 4 baseline orientations).
- Chicago (heating faults): **5 of 5** faults detected; **0** FAIL findings on the 2 clean buildings.
- The faults cover health, unmet hours on DX, VAV and chilled-water plant systems, must-match items, and
  baseline rules. See `docs/FAULTLAB.md` and the scorecards in `docs/`.

```bash
python -I tools/faultlab/lab.py build LAB clean && python -I tools/faultlab/lab.py run LAB clean
python -I tools/faultlab/lab.py build LAB faults && python -I tools/faultlab/lab.py run LAB faults
python -I tools/faultlab/lab.py score LAB          # writes LAB/scorecard.md
```

## Limitations (read before relying on a result)

- prereview is a pre-submission aid, not a compliance certification. Clauses are paraphrased; your rating
  authority's interpretation decides.
- The baseline HVAC check recognises Systems 1–10 from the HVAC topology and the run's IDF (reheat and
  heating coil types). It does not check fan power, efficiencies, economizers, energy recovery or boiler and
  chiller counts yet. Special 90.1-2016 categories (public assembly, hospitals) are flagged REVIEW.
- The window-to-wall check applies the 2010 rule (proposed or 40%, on conditioned walls when EnergyPlus reports
  them). Under 2016 the building-type table (G3.1.1-1) is not computed: the total is reported as REVIEW with
  both targets; the distribution by orientation is still checked.
- The outdoor-air check compares zone design outdoor air and the air systems' minimum intake from
  Controller:OutdoorAir; DOAS and zone-equipment outdoor air are not added up yet.
- The schedule checks compare what EnergyPlus simulated, hour by hour. They cannot tell an approved
  exceptional calculation from a mistake, so they report the difference and leave the judgment to you.
- Models with GroundHeatTransfer objects are run with EnergyPlus's own `-x` expansion; their HVAC outputs are
  requested by wildcard only.
- The rules encode the author's reading of 90.1, checked by an independent review; the fault lab tests that the
  code does what that reading says, so it cannot catch a misread clause. Please report any rule you disagree
  with.
- The LEED process-energy check uses site energy as a proxy for energy cost.

## Layout

```
src/prereview/   idf.py (IDF parser/editor) · runner.py · sqlout.py · errfile.py · kb.py · topology.py
                 rules/ (health, unmet, consistency, baseline2010) · report/html.py · cli.py
tests/           unit tests on synthetic EnergyPlus-like SQLite files
tools/faultlab/  seeded-fault lab (prepare, mutate, lab, score)
docs/            PLAN.md · RULES.md · FAULTLAB.md
```
