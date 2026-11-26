# Seeded-fault lab

A checker you cannot measure cannot be trusted. The lab builds proposed and baseline sets from the DOE
prototype buildings that ship with EnergyPlus. It seeds one realistic mistake per case, simulates the set,
and checks two things:

- **Detection.** prereview reports the seeded mistake under the expected rule and status. For unmet-hours
  faults, the expected zone and cause must also be the top contributor.
- **No false alarms.** The clean sets produce no FAIL findings. Each clean set is a proposed model plus four
  baseline orientations, where the baselines have 10% more lighting power, a legitimate difference.

How the models are prepared (`tools/faultlab/prepare.py`, `lab.py`):

- One annual run period. The prototypes ship with three one-month periods.
- Design days and site location come from the weather file's `.ddy`: 99.6% heating, 1% cooling DB⇒MWB.
- Sizing factors are set so that each clean model stays under 300 unmet hours in its climate. prereview's
  own diagnosis chose them. For example, the Taipei medium office started at 634 h. The diagnosis said the
  perimeter VAV boxes ran at maximum airflow and the packaged DX coil could not hold 12.8 °C supply air in
  humid weather. Raising the cooling sizing factor (1.0 → 1.8) brought it to about 230 h.
- Maximum warm-up days set to 50. Some prototypes stop at 15–25 days and then log severe warm-up
  convergence errors in cold climates, which prereview flags.

Two fault sets:

- **humid** (default): Taipei TMYx, 33 faults. Covers simulation health, unmet hours (cooling, including a
  chilled-water plant), baseline/proposed mismatches, and baseline rules.
- **cold**: Chicago O'Hare TMY3, 5 heating-side faults: hard-sized furnace, furnace never available, mild
  heating design day, morning warm-up, undersized heat-pump backup heater.

Some faults are mild on purpose: the building stays under 300 h. For those, the test is whether the
diagnosis names the right cause, and the expected status is "any".

## What the lab can and cannot prove

The lab checks that the code does what its rules say. It cannot show that a rule reads the standard
correctly, because the expected answers come from the same reading. An independent review compared the rules
with the standard's text and interpretations, and found several errors that the lab had passed. They are
now fixed, and the lab has cases taken from that text:

- **The 50-hour rule.** The proposed-minus-baseline limit was in 90.1-2007 (LEED 2009) only;
  90.1-2010 G3.1.2.3 limits each model to 300 h. F12, a compliant model, used to FAIL. It is now a
  no-false-alarm case.
- **90.1-2016 picks the baseline system by climate zone, not fuel** (Table G3.1.1-3). F35 is a Taipei
  (zone 2A) retail building whose baseline must be System 4.
- **90.1-2016 fixes the WWR of listed building types** (Table G3.1.1-1). F36 checks that prereview does not
  FAIL such a baseline.
- **Other corrections.** IP-unit tabular output is converted. HVACTemplate models are expanded before
  outputs are added, and they run safely in parallel. Run files are paired by name. A heating source or
  climate zone that prereview inferred never produces a FAIL on its own. Unknown frequent warnings are no
  longer called harmless.

The clean DOE prototypes are not Appendix G baselines. When prereview flags something that is genuinely
wrong in them, for example the medium office's packaged VAV with electric reheat, which is not System 5, the
scorecard lists it as a true finding and does not count it as a false alarm.

## Reproduce

```bash
export PREREVIEW_ENERGYPLUS=/path/to/energyplus
python -I tools/faultlab/lab.py build LAB clean && python -I tools/faultlab/lab.py run LAB clean
python -I tools/faultlab/lab.py build LAB faults && python -I tools/faultlab/lab.py run LAB faults
python -I tools/faultlab/lab.py score LAB
# cold set
LAB_SET=cold LAB_EPW=.../USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw LAB_DDY=...ddy \
  python -I tools/faultlab/lab.py ...
```

## Lessons from building the lab

Each lesson below changed the code:

- **Count unmet hours at the zone timestep.** Flagging an hour when any zone has any unmet time in it
  overcounts badly: 411 h against EnergyPlus's 296 h. prereview now rebuilds the count from EnergyPlus's
  facility-level timestep series, and it reproduces the tabular figures exactly.
- **A VAV box's minimum-flow fraction is not a capacity.** The first hard-sizing check flagged it. The check
  now looks only at capacity and maximum-flow fields that are relevant to the mode (heating or cooling).
- **Some hard-sized coils have no design size in ComponentSizes.** EnergyPlus reports only the user value.
  For single-zone systems, prereview compares the coil against the zone's design load instead.
- **EnergyPlus 26.2 leaves `Simulations.CompletedSuccessfully` FALSE after a clean run.** The `.err` summary
  line is the completion signal.
- **One failed or partial run set off a cascade of FAILs on every other rule.** Runs that did not complete
  are now reviewed only for health. Runs of different lengths are not compared hour by hour.
- **"Morning recovery" is often a symptom, not the cause.** When three quarters of the misses happen beyond
  the sizing design day, the design day is reported as the cause.
- **The DOE large office prototype carries Denver-sized chillers.** Moved to Taipei, it has 1,300 unmet
  hours. The first diagnosis said "the chilled-water plant cannot hold its supply temperature". Once
  water-flow fields were added to the hard-sizing check, it named the real cause: each chiller's reference
  chilled-water flow is hard-coded at 24% of what the Taipei design needs. Fault F33 reproduces this.
  A second plant fault, an operation-scheme load cap, had no effect on the simulation, so it was dropped
  rather than kept as a fake "pass".
- **Several faults that looked wrong were the tool being right.** In one, the HVAC schedule turned off while
  people were still in the building. In another, a heating setpoint ran past the HVAC hours. Whenever the
  diagnosis disagreed with the planned fault, we checked the hourly data before changing anything. Only
  faults whose design was itself mistaken were redefined; their history is in git.

## Latest scorecards

(See `scorecard-humid.md` and `scorecard-cold.md` in this folder, generated by `lab.py score`.)
