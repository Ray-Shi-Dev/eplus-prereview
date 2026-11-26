# eplus-prereview — overnight build plan (2026-11-26, approved by Ray)

Goal: a pre-submission reviewer for EnergyPlus-based LEED / ASHRAE 90.1 Appendix G
energy models. It reads simulation outputs (eplusout.sql, eplusout.err, the IDF) for
the proposed model and the baseline rotations and produces review-comment style
findings: what a reviewer would flag, the evidence, the clause, and how to fix it.

Positioning: PNNL's Ruleset Checking Tool already covers 90.1-2019/2022 via ASHRAE 229
RPD files. We do what it does not: work directly on EnergyPlus outputs, target the
editions LEED v4 (90.1-2010) and v4.1 (90.1-2016) projects still use until 2033, and
diagnose *why* a model fails (unmet hours root cause, err triage, baseline/proposed drift).

## Tier 1 — must be perfect tonight
1. Unmet load hours: reported (facility occupied heating/cooling) and exact union of
   hours; <=300 per model; proposed - baseline <= 50; per-zone root cause
   (capacity-limited / hard-sized below design / system off / never delivers /
   morning recovery / design days do not match the weather file).
2. Baseline vs proposed must-match diff (Table G3.1 items that must be identical):
   zones and conditioned area, occupancy and schedules, plug/process loads,
   thermostat setpoints, infiltration, lighting schedules, outdoor air, weather/run period.
3. Simulation health and err triage: completion, severe/fatal, warmup convergence,
   grouped warnings with a knowledge base of causes and fixes.

## Tier 2 — only after Tier 1 meets the bar
90.1-2010 baseline checks: fenestration (min(proposed, 40%), same proportion per face),
skylights (min(proposed, 5%)), four rotations, baseline HVAC system type (Table G3.1.1A).

## Quality bar
- Seeded-fault lab on DOE prototypes with Taipei TMYx weather: every seeded fault is
  detected by the right rule, pointing at the right zone and cause.
- Clean pairs: zero FAIL findings.
- Every finding: clause/source, numeric evidence, affected objects, fix steps.
- Prefer EnergyPlus's own reported numbers over re-computation.
- Unit tests in CI; integration fault lab runnable locally.
- Independent fresh-eyes review at the end; fix what it finds.

## Guardrails
- Private repo, no public release, no use of Ray's name publicly.
- No Arup or client data (A21EnergyPlus repo is off limits). Knowledge base is generic.
- No money, no messages sent on Ray's behalf.

## Schedule (Taipei time)
22:45 setup · 23:15 reading layer · 00:45 core checks · 03:15 fault lab ·
04:45 tier 2 · 05:30 report/CLI/docs · 06:15 independent review · 06:45 handoff.
