# Fault lab scorecard (2026-11-26 23:47)

- Seeded faults detected with the right rule (and zone/cause where specified): **32 / 32**
- FAIL findings on clean proposed/baseline sets: **0** (3 buildings × proposed + 4 baseline orientations)

| Fault | Building | Seeded in | What was seeded | Expected | Result |
|---|---|---|---|---|---|
| F01 | OS | proposed | Partial-year run period (Jan–Jun) | HEALTH-YEAR FAIL | ✅ The weather-file run periods cover 4344 hours, not a full year (8760). Appendix G results and unmet hours must |
| F02 | OS | proposed | Invalid keyword (Building terrain) | HEALTH-RUN FAIL | ✅ EnergyPlus did not complete this run. A field contains a value the IDD does not allow (typo, or a keyword rena |
| F03 | OS | proposed | People schedule that does not exist | HEALTH-RUN FAIL | ✅ EnergyPlus stopped without writing its final summary (it crashed or was killed) — the last messages usually po |
| F04 | OS | proposed | Window larger than its wall (multiplier 8) | HEALTH-RUN FAIL | ✅ EnergyPlus did not complete this run. A window with a multiplier greater than 1 is in a zone with daylighting  |
| F05 | OS | proposed | Warm-up limited to 1 day (no convergence) | HEALTH-SEVERE FAIL | ✅ 21 severe errors in a completed run. Reviewers ask for severe errors to be resolved (or explained) because res |
| F06 | OS | proposed | Design days from another climate (Denver) with Taipei weather | SIZE-DD FAIL; UMLH-300 FAIL cause beyond_design_day | ✅ The design days (DENVER-AURORA-BUCKLEY.AFB_CO_USA ANN HTG 99.6% CONDNS DB, DENVER-AURORA-BUCKLEY.AFB_CO_USA AN / top contributor CORE_ZN (cooling): beyond_design_day |
| F07 | OS | proposed | Cooling coil hard-sized at 50% of design (Perimeter_ZN_1) | UMLH-300 FAIL zone PERIMETER_ZN_1 cause hard_sized_below_design; UMLH-50 FAIL | ✅ top contributor PERIMETER_ZN_1 (cooling): hard_sized_below_design / Proposed has 1192.75 unmet hours vs a baseline average of 68.94 (+1123.81 h; limit +50). |
| F08 | OS | proposed | Cooling coil never available (Perimeter_ZN_2) | UMLH-300 FAIL zone PERIMETER_ZN_2 cause never_delivers | ✅ top contributor PERIMETER_ZN_2 (cooling): never_delivers |
| F09 | OS | proposed | HVAC starts at 11:00, after occupancy (stay off at night) | UMLH-300 FAIL cause system_off; CONS-HVAC-SCHED FAIL | ✅ top contributor CORE_ZN (cooling): system_off / The HVAC serving 5 zones runs on different hours in the proposed and baseline models. |
| F10 | OS | proposed | No night cycling and 40 °C setup: HVAC starts at occupancy into a hot building | UMLH-300 PASS cause morning_recovery; CONS-TSTAT-CLG FAIL | ✅ top contributor CORE_ZN (cooling): morning_recovery / Cooling setpoint schedule differs between proposed and baseline in 4 zones (4 baseline runs). |
| F32 | OS | proposed | Cooling sized with no margin (sizing factor 1.0) in a humid climate | UMLH-300 FAIL cause supply_air_not_at_setpoint | ✅ top contributor CORE_ZN (cooling): supply_air_not_at_setpoint |
| F11 | OM | proposed | Zone airflow sized for 6 °C supply air while the system supplies 12.8 °C (Perimeter_bot_ZN_1) | UMLH-300 FAIL zone PERIMETER_BOT_ZN_1 cause terminal_at_max_airflow | ✅ top contributor PERIMETER_BOT_ZN_1 (cooling): terminal_at_max_airflow |
| F12 | OS | proposed | Proposed coil at 90% (Core_ZN): within 300 but > baseline + 50 | UMLH-50 FAIL; UMLH-300 PASS | ✅ Proposed has 166.50 unmet hours vs a baseline average of 68.94 (+97.56 h; limit +50). / 166.5 unmet load hours (exact count); heating + cooling total 166.50 h. |
| F13 | OS | B000 | Baseline coil hard-sized at 50% (Perimeter_ZN_1) | UMLH-300 FAIL zone PERIMETER_ZN_1 cause hard_sized_below_design | ✅ top contributor PERIMETER_ZN_1 (cooling): hard_sized_below_design |
| F14 | OS | B000 | Baseline occupancy +25% in Perimeter_ZN_1 | CONS-PEOPLE FAIL zone PERIMETER_ZN_1 | ✅ zone PERIMETER_ZN_1 listed |
| F15 | OS | B000 | Baseline occupancy schedule changed (Core_ZN) | CONS-PEOPLE FAIL zone CORE_ZN | ✅ zone CORE_ZN listed |
| F16 | OS | B000 | Baseline plug loads −20% | CONS-PLUG FAIL; CONS-EQUIP-TOTAL FAIL | ✅ Electric equipment power (level × schedule) differs between proposed and baseline in 5 zones. / Annual interior equipment (plug and process) energy differs between proposed and baseline. |
| F17 | OS | B000 | Baseline plug-load schedule changed | CONS-PLUG FAIL | ✅ Electric equipment power (level × schedule) differs between proposed and baseline in 5 zones. |
| F18 | OS | B000 | Baseline cooling setpoint 25 °C instead of 24 °C | CONS-TSTAT-CLG FAIL | ✅ Cooling setpoint schedule differs between proposed and baseline in 4 zones. |
| F19 | OS | B000 | Baseline heating setback deeper | CONS-TSTAT-HTG FAIL | ✅ Heating setpoint schedule differs between proposed and baseline in 4 zones. |
| F20 | OS | B000 | Baseline infiltration +50% | CONS-INFIL FAIL | ✅ Infiltration differs in 5 zones. |
| F21 | OS | B000 | Baseline lighting schedule changed | CONS-LSCHED REVIEW | ✅ Lighting schedules differ in 5 zones. Appendix G keeps schedules identical; lighting-control savings are taken |
| F22 | OS | B000 | Baseline outdoor air +30% | CONS-OA REVIEW | ✅ Design outdoor airflow differs in 5 zones. Minimum ventilation must match unless an exception applies (e.g. DC |
| F23 | OS | B000 | Baseline simulated with another weather file | CONS-SITE FAIL | ✅ Proposed and baseline were not simulated under the same conditions. |
| F24 | OS | B000 | Baseline zone multiplier 2 (Perimeter_ZN_4) | CONS-ZONES FAIL | ✅ The models do not describe the same building: zones or floor areas differ. |
| F25 | OS | B000 | Gas equipment only in the baseline (Core_ZN) | CONS-GAS-EQUIP FAIL zone CORE_ZN | ✅ zone CORE_ZN listed |
| F26 | OS | B000 | Baseline HVAC runs 24/7 | CONS-HVAC-SCHED FAIL | ✅ The HVAC serving 5 zones runs on different hours in the proposed and baseline models. |
| F27 | OS | none | Only one baseline orientation submitted | BASE-ROTATION FAIL | ✅ 1 baseline run submitted; missing orientations +90°, +180°, +270°. |
| F28 | OS | none | Baseline orientations 0/90/90/270 (180 missing) | BASE-ROTATION FAIL | ✅ 4 baseline runs submitted; missing orientation +180°. |
| F29 | OS | B000 | Baseline window area differs from proposed (one window removed) | BASE-WWR FAIL | ✅ Baseline wwr should be 21.2% (the proposed 21.2%); baseline windows are not distributed by orientation like th |
| F30 | OS | none | Heat pump baseline for a fossil-fuel heated building | BASE-SYSTEM FAIL | ✅ Expected System 3 – PSZ-AC (packaged rooftop AC, fossil furnace) for this building (nonresidential, 3 floors o |
| F31 | RS | none | Furnace baseline for an electrically heated building | BASE-SYSTEM FAIL | ✅ Expected System 4 – PSZ-HP (packaged rooftop heat pump) for this building (nonresidential, 3 floors or fewer a |

## Clean sets

- **OS**: 0 FAIL, 0 REVIEW
- **RS**: 0 FAIL, 1 REVIEW
  - REVIEW LEED-PROCESS : Process energy (interior and exterior equipment, refrigeration) is 18% of baseline site energy. LEED asks for documentation when process energy cost is under 25% of the baseline energy cost (cost basi
- **OM**: 0 FAIL, 0 REVIEW
