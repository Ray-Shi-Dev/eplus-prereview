# Rules catalog

Statuses:
- **FAIL**: a reviewer would reject this as submitted.
- **REVIEW**: likely to draw a comment. Fix it, or document why it is right.
- **PASS**: checked and fine.
- **INFO**: context; no action needed.
- **SKIPPED**: the data needed for the check was not available.

Clause references are paraphrased.

## Simulation health (`rules/health.py`)

| Rule | Checks | FAIL / REVIEW when |
|---|---|---|
| HEALTH-RUN | The run finished. The completion signal is the `.err` summary line; EnergyPlus 26 leaves the SQL completion flag FALSE. Crashed runs are detected too. | FAIL: fatal error, crash, or no summary line. The explanation comes from the knowledge base. |
| HEALTH-SQL | A completed run has a usable eplusout.sql. Without it nothing beyond the `.err` can be checked. | FAIL: completed, but no SQL output (add Output:SQLite, SimpleAndTabular). |
| HEALTH-YEAR | Weather-file run periods cover 8760/8784 hours | FAIL otherwise (Appendix G requires an annual simulation) |
| HEALTH-SEVERE | Severe errors in a completed run | FAIL. Each group of messages comes with its meaning and a fix. |
| HEALTH-WARN | Warnings the knowledge base marks as able to change results (warm-up, psychrometrics, coil freezing, missing setpoints, plant temperature limits, location mismatch) | REVIEW |
| HEALTH-INFO | All other warnings, grouped and explained; unused schedules and constructions | INFO |

Models that did not complete are only checked for health; the other rules skip them, so one broken run
does not set off a cascade of findings.

## Unmet load hours (`rules/unmet.py`)

| Rule | Checks | FAIL / REVIEW when |
|---|---|---|
| UMLH-300 | G3.1.2.3: time with any zone unmet while occupied (facility timestep output, time-weighted as EnergyPlus counts), for the proposed and each baseline run | FAIL > 300. REVIEW when the any-zone figure is ≤ 300 but EnergyPlus's heating + cooling total (the LEED table figure) is > 300. Without timestep output: FAIL when heating or cooling alone is > 300, REVIEW when only their sum is. Evidence also shows the whole-clock-hour count. |
| UMLH-DIFF | Proposed minus baseline average | INFO only: 90.1-2010/2016 set no limit (the 50-hour rule was 90.1-2007). A gap over 50 h gets a note for the narrative. |
| SIZE-DD | Sizing design days against the weather file's own 0.4% / 99% hourly dry-bulb (the 99.6% heating and 1% cooling conditions); baseline design-day names that state another percentile | FAIL when a design day is more than 6 K from the weather file's condition (another climate); REVIEW for a stated 99% heating or 0.4%/2% cooling baseline design day; name-only REVIEW when no hourly outdoor temperature is available |
| UMLH-TOL | `OutputControl:ReportingTolerances` is the same in every model | FAIL when models differ; REVIEW above 1.11 K (2 °F) |

The diagnosis (top 6 zone/mode pairs) is attached to UMLH-300 and shown in the report. The cause categories
are listed in the README.

## Baseline vs proposed (`rules/consistency.py`)

Zones are matched by name, then by floor area. Values come from hourly EnergyPlus output.

| Rule | Compares | Status on difference |
|---|---|---|
| CONS-SITE | Weather file, EnergyPlus version, hours simulated (FAIL); timesteps per hour (REVIEW) | FAIL / REVIEW |
| CONS-ZONES | Zone set, net conditioned and total floor area | FAIL |
| CONS-PEOPLE | `Zone People Occupant Count`, hour by hour | FAIL |
| CONS-PLUG / CONS-GAS-EQUIP / CONS-OTHER-EQUIP | Zone electric / gas / other equipment, hour by hour | FAIL |
| CONS-EQUIP-TOTAL | Annual interior equipment energy (ABUPS) | FAIL |
| CONS-TSTAT-HTG / CONS-TSTAT-CLG | Thermostat setpoints, hour by hour (0.05 K tolerance) | FAIL |
| CONS-HVAC-SCHED | On/off hours of the availability schedule of the HVAC serving each zone | FAIL |
| CONS-INFIL | Infiltration design flow and schedule per zone | REVIEW (a lower proposed rate is allowed with testing or justification) |
| CONS-LSCHED | Lighting schedule values per zone | REVIEW; the message follows the edition (2010: credits via Table G3.2 power adjustments; 2016: proposed schedules may be reduced per Table G3.7). SKIPPED when no schedule values were reported |
| CONS-OA | Zone design outdoor air (sizing results) and the air systems' minimum outdoor-air intake (Controller:OutdoorAir) | REVIEW (DCV and other exceptions exist) |

When the runs have different lengths, the hour-by-hour comparisons are SKIPPED and CONS-SITE reports the
difference.

## Baseline rules (`rules/baseline2010.py`)

| Rule | Checks | Status |
|---|---|---|
| BASE-ROTATION | Baseline runs at +0/90/180/270° relative to the proposed (North Axis + Appendix G rotation, or window areas by orientation when the geometry itself was rotated) | FAIL if an orientation is missing; REVIEW if one is duplicated. INFO with a declared exception, or (2016) when fenestration varies < 5% between orientations. |
| BASE-WWR | 2010: baseline above-grade WWR (conditioned walls) = min(proposed, 40%) ± 0.5 points. Both editions: window share by orientation matches the proposed after rotation (± 3%) | FAIL |
| BASE-WWR-2016 | 2016: the total WWR may come from Table G3.1.1-1 by building type | REVIEW with both targets |
| BASE-SKYLIGHT | Skylight-to-roof ratio = min(proposed, 5% [2010] / 3% [2016]) | FAIL |
| BASE-SYSTEM | Modeled baseline system (Systems 1–10, from the HVAC topology and the IDF's reheat and heating coil types) against Table G3.1.1A/B (2010, by heating source; hybrids count as fossil) or Table G3.1.1-3 (2016, by climate zone) | FAIL when wrong and the inputs are firm (project.toml); REVIEW when the heating source or climate zone was inferred, when several types are mixed (G3.1.1 exceptions), or when coil types could not be verified |
| LEED-PROCESS | Process share of baseline energy (site energy as a proxy for cost) | REVIEW < 25% |

## Error knowledge base (`kb.py`)

40 generic entries, matched by regular expression against the message and its continuation lines. A warning
not in the knowledge base that occurs 100 times or more is reported as REVIEW, never as harmless. They cover:

- **Input processing:** invalid keywords, too many fields, missing references, branches and nodes,
  sub-surface area, degenerate geometry, upside-down surfaces, interzone mismatches, default ground
  temperatures, zones with no floor, non-convex surfaces, schedule limits, unused objects.
- **Sizing:** zero design loads, DX airflow per capacity, AHRI ratings, curve normalisation, plant sizing,
  location mismatch.
- **Simulation:** surface temperatures out of bounds, psychrometrics, warm-up, HVAC iterations, coil
  control, water-coil controllers, coil freezing, missing setpoints, plant loop temperature limits, chiller and
  cooling-tower curves outside their range.
- **Output requests.**

Messages caused by prereview's own output requests are ignored.
