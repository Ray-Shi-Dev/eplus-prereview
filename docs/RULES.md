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
| HEALTH-YEAR | Weather-file run periods cover 8760/8784 hours | FAIL otherwise (Appendix G requires an annual simulation) |
| HEALTH-SEVERE | Severe errors in a completed run | FAIL. Each group of messages comes with its meaning and a fix. |
| HEALTH-WARN | Warnings the knowledge base marks as able to change results (warm-up, psychrometrics, coil freezing, missing setpoints, plant temperature limits, location mismatch) | REVIEW |
| HEALTH-INFO | All other warnings, grouped and explained; unused schedules and constructions | INFO |

Models that did not complete are only checked for health; the other rules skip them, so one broken run
does not set off a cascade of findings.

## Unmet load hours (`rules/unmet.py`)

| Rule | Checks | FAIL / REVIEW when |
|---|---|---|
| UMLH-300 | Hours with any zone unmet while occupied (exact, from facility timestep output), per model | FAIL > 300. REVIEW when the exact count is ≤ 300 but EnergyPlus's heating + cooling total is > 300. Without timestep output: FAIL when heating or cooling alone is > 300, REVIEW when only their sum is > 300. |
| UMLH-50 | Proposed minus baseline (average of the submitted orientations) | FAIL > 50 h (90.1-2010; REVIEW under 2016). REVIEW when within 50 h of the average but > 50 h above one orientation. A baseline higher than the proposed is allowed. |
| SIZE-DD | Design days are for the weather-file location; under 2010, the baseline uses 99.6% heating and 1% cooling | FAIL for another location; REVIEW for a different percentile |

The diagnosis (top 6 zone/mode pairs) is attached to UMLH-300 and shown in the report. The cause categories
are listed in the README.

## Baseline vs proposed (`rules/consistency.py`)

Zones are matched by name, then by floor area. Values come from hourly EnergyPlus output.

| Rule | Compares | Status on difference |
|---|---|---|
| CONS-SITE | Weather file, EnergyPlus version, hours simulated, timesteps per hour | FAIL |
| CONS-ZONES | Zone set, net conditioned and total floor area | FAIL |
| CONS-PEOPLE | `Zone People Occupant Count`, hour by hour | FAIL |
| CONS-PLUG / CONS-GAS-EQUIP / CONS-OTHER-EQUIP | Zone electric / gas / other equipment, hour by hour | FAIL |
| CONS-EQUIP-TOTAL | Annual interior equipment energy (ABUPS) | FAIL |
| CONS-TSTAT-HTG / CONS-TSTAT-CLG | Thermostat setpoints, hour by hour (0.05 K tolerance) | FAIL |
| CONS-HVAC-SCHED | On/off hours of the availability schedule of the HVAC serving each zone | FAIL |
| CONS-INFIL | Infiltration design flow and schedule per zone | FAIL (2010) / REVIEW (2016) |
| CONS-LSCHED | Lighting schedule values per zone | REVIEW (control credits belong in Table G3.2 adjustments) |
| CONS-OA | Zone design outdoor air (sizing results) | REVIEW (DCV and other exceptions exist) |

When the runs have different lengths, the hour-by-hour comparisons are SKIPPED and CONS-SITE reports the
difference.

## Baseline rules (`rules/baseline2010.py`)

| Rule | Checks | Status |
|---|---|---|
| BASE-ROTATION | Baseline runs at +0/90/180/270° relative to the proposed (North Axis + Appendix G rotation) | FAIL if any is missing or duplicated. INFO with a declared exception, or (2016) when fenestration varies < 5% between orientations. |
| BASE-WWR | Baseline above-grade WWR = min(proposed, 40%) ± 0.5 points; window share by orientation matches the proposed after rotation (± 3%) | FAIL. Under 2016 a PASS is shown as REVIEW, because Table G3.1.1-1 may set a different value. |
| BASE-SKYLIGHT | Skylight-to-roof ratio = min(proposed, 5% [2010] / 3% [2016]) | FAIL |
| BASE-SYSTEM | Modeled baseline system type (Systems 1–10, read from the HVAC topology) against Table G3.1.1A/B for building type, floors, conditioned area and heating source | FAIL when wrong; REVIEW when several types are mixed (G3.1.1 exceptions) |
| LEED-PROCESS | Process share of baseline energy (site energy as a proxy for cost) | REVIEW < 25% |

## Error knowledge base (`kb.py`)

35 generic entries, matched by regular expression against the message and its continuation lines. They
cover:

- **Input processing:** invalid keywords, too many fields, missing references, branches and nodes,
  sub-surface area, degenerate geometry, upside-down surfaces, interzone mismatches, default ground
  temperatures, zones with no floor, non-convex surfaces, schedule limits, unused objects.
- **Sizing:** zero design loads, DX airflow per capacity, AHRI ratings, curve normalisation, plant sizing,
  location mismatch.
- **Simulation:** surface temperatures out of bounds, psychrometrics, warm-up, HVAC iterations, coil
  control, coil freezing, missing setpoints, plant limits.
- **Output requests.**

Messages caused by prereview's own output requests are ignored.
