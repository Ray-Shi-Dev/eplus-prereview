# Fault lab scorecard (2026-11-27 00:48)

- Seeded faults detected with the right rule (and zone/cause where specified): **5 / 5**
- FAIL findings on clean proposed/baseline sets: **0** (2 buildings × proposed + 4 baseline orientations)

| Fault | Building | Seeded in | What was seeded | Expected | Result |
|---|---|---|---|---|---|
| C01 | RS | proposed | Furnace hard-sized at 40% of design (Back_Space) | UMLH-300 * zone BACK_SPACE cause hard_sized_below_design | ✅ top contributor BACK_SPACE (heating): hard_sized_below_design |
| C02 | RS | proposed | Furnace never available (Front_Retail) | UMLH-300 FAIL zone FRONT_RETAIL cause never_delivers | ✅ top contributor FRONT_RETAIL (heating): never_delivers |
| C03 | OS | proposed | Heating sized on a +2 °C design day instead of the 99.6% condition | UMLH-300 * cause beyond_design_day; SIZE-DD FAIL | ✅ top contributor PERIMETER_ZN_3 (heating): beyond_design_day / Sizing design days do not match the simulated climate: heating design day 2.0 °C vs -19.8 °C in the weather fi |
| C04 | OS | proposed | 10 °C night setback with no night cycling (morning warm-up) | UMLH-300 * cause morning_recovery; CONS-TSTAT-HTG FAIL | ✅ top contributor PERIMETER_ZN_3 (heating): morning_recovery / Heating setpoint schedule differs between proposed and baseline in 4 zones (4 baseline runs). |
| C05 | OS | proposed | Heat pump backup heater hard-sized at 20% (Perimeter_ZN_1) | UMLH-300 * zone PERIMETER_ZN_1 cause hard_sized_below_design | ✅ top contributor PERIMETER_ZN_1 (heating): hard_sized_below_design |

## Clean sets

- **OS**: 0 FAIL, 0 REVIEW
- **RS**: 0 FAIL, 2 REVIEW
  - REVIEW HEALTH-SEVERE B000: 1 severe message of a kind that usually does not invalidate results (e.g. warm-up loads not converged while temperatures did). Fix or explain it — reviewers who read the .err file will ask.
  - REVIEW LEED-PROCESS : Process energy (interior and exterior equipment, refrigeration) is 13% of baseline site energy. LEED asks for documentation when process energy cost is under 25% of the baseline energy cost (cost basi
