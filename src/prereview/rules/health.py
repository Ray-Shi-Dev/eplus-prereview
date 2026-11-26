"""Simulation health: did the run complete, is it a full year, and what do the errors mean."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Dict, List, Tuple

from ..errfile import ErrMessage, total_count
from ..kb import KB, KbEntry, lookup
from ..model import ModelRun
from .base import FAIL, INFO, PASS, REVIEW, Context, Finding

CLAUSE_RUN = "ASHRAE 90.1 Appendix G G2 simulation general requirements (8760-hour annual simulation)"
CLAUSE_ERR = "LEED EA Prerequisite / Credit energy model review — simulation output must be free of unresolved errors"
UNKNOWN_REVIEW_COUNT = 100  # an unexplained warning seen this often is not assumed harmless


def _example_objects(msgs: List[ErrMessage], limit: int = 5) -> List[str]:
    seen: "OrderedDict[str, None]" = OrderedDict()
    for m in msgs:
        for cls, name in m.objects():
            seen.setdefault(f"{cls} “{name}”", None)
        if len(seen) >= limit:
            break
    return list(seen)[:limit]


def _first_text(msgs: List[ErrMessage]) -> str:
    m = msgs[0]
    text = m.text
    if m.details:
        text += " — " + " ".join(m.details[:2])
    return text[:400]


def check_model(model: ModelRun) -> List[Finding]:
    out: List[Finding] = []
    err = model.err
    completed = model.completed
    if not completed:
        fatal_msgs = [m for m in (err.messages if err else []) if m.level in ("Fatal", "Severe")]
        fixes, explain = [], ""
        for m in fatal_msgs[:3]:
            kb = lookup(m)
            if kb is not None:
                explain = explain or kb.meaning
                fixes.extend(f for f in kb.fixes if f not in fixes)
        crashed = err is not None and err.warnings_total is None
        if err is None and model.sql is None:
            lead = "No EnergyPlus results (.sql or .err) were found for this model — check the folder or file path. "
        elif crashed:
            lead = ("EnergyPlus stopped without writing its final summary (it crashed or was killed) — the last "
                    "messages usually point at the cause. ")
        else:
            lead = "EnergyPlus did not complete this run. "
        out.append(Finding(
            "HEALTH-RUN", "Simulation completed", FAIL,
            lead + (explain or "See the severe/fatal messages below."),
            model=model.label, clause=CLAUSE_RUN,
            evidence=[{"Level": m.level, "Message": (m.text + " " + " ".join(m.details[:2]))[:300]}
                      for m in fatal_msgs[:8]],
            objects=_example_objects(fatal_msgs), fixes=fixes))
        return out
    out.append(Finding("HEALTH-RUN", "Simulation completed", PASS,
                       f"EnergyPlus {model.sql.version() or ''} completed successfully.".replace("  ", " "),
                       model=model.label, clause=CLAUSE_RUN))

    hours = model.hours_simulated()
    if hours is not None and round(hours) not in (8760, 8784):
        out.append(Finding(
            "HEALTH-YEAR", "Full-year simulation", FAIL,
            f"The weather-file run periods cover {hours:.0f} hours, not a full year (8760). Appendix G results "
            f"and unmet hours must come from an annual simulation.",
            model=model.label, clause=CLAUSE_RUN,
            evidence=[{"Metric": "Hours simulated", "Value": f"{hours:.0f}"}],
            fixes=["Use one RunPeriod from January 1 to December 31 (or several that together cover the year "
                   "exactly once)."]))
    elif hours is not None:
        out.append(Finding("HEALTH-YEAR", "Full-year simulation", PASS, f"{hours:.0f} hours simulated.",
                           model=model.label, clause=CLAUSE_RUN))

    if err is None:
        out.append(Finding("HEALTH-ERR", "Error file reviewed", INFO,
                           "No .err file was found next to the SQL output, so warnings were not reviewed.",
                           model=model.label))
        return out

    # group messages by KB entry (or by template when unknown)
    groups: "OrderedDict[Tuple[str, str], List[ErrMessage]]" = OrderedDict()
    kb_of: Dict[Tuple[str, str], KbEntry] = {}
    for m in err.messages:
        kb = lookup(m)
        key = (m.level, kb.id if kb else "?" + m.template())
        groups.setdefault(key, []).append(m)
        if kb:
            kb_of[key] = kb

    severe = [(k, v) for k, v in groups.items() if k[0] in ("Severe", "Fatal")]
    hard, soft = [], []
    for key, msgs in severe:
        kb = kb_of.get(key)
        detail = " | ".join(" | ".join(m.details) for m in msgs)
        if kb is not None and kb.severe_status and not (kb.fail_if and re.search(kb.fail_if, detail, re.I)):
            soft.append((key, msgs))
        else:
            hard.append((key, msgs))
    for group, status in ((hard, FAIL), (soft, REVIEW)):
        if not group:
            continue
        rows, fixes, objs = [], [], []
        for key, msgs in group:
            kb = kb_of.get(key)
            rows.append({"Count": str(total_count(msgs)), "Message": _first_text(msgs),
                         "What it means": kb.meaning if kb else "Not in the knowledge base — read the message "
                         "and its continuation lines in the .err file."})
            if kb:
                fixes.extend(f for f in kb.fixes if f not in fixes)
            objs.extend(o for o in _example_objects(msgs) if o not in objs)
        n = sum(total_count(v) for _, v in group)
        if status == FAIL:
            msg = (f"{n} severe error{'s' if n != 1 else ''} in a completed run. Reviewers ask for severe errors to "
                   f"be resolved (or explained) because results after a severe error may be invalid.")
        else:
            msg = (f"{n} severe message{'s' if n != 1 else ''} of a kind that usually does not invalidate results "
                   f"(e.g. warm-up loads not converged while temperatures did). Fix or explain it — reviewers who "
                   f"read the .err file will ask.")
        out.append(Finding("HEALTH-SEVERE", "No severe errors", status, msg, model=model.label, clause=CLAUSE_ERR,
                           evidence=rows, objects=objs[:10], fixes=fixes))
    if not severe:
        out.append(Finding("HEALTH-SEVERE", "No severe errors", PASS, "No severe errors.", model=model.label,
                           clause=CLAUSE_ERR))

    review_rows, review_fixes, review_objs = [], [], []
    info_rows = []
    for key, msgs in groups.items():
        if key[0] != "Warning":
            continue
        kb = kb_of.get(key)
        if kb is not None and kb.ignore:
            continue
        n = total_count(msgs)
        status = (kb.status if kb and kb.status else INFO)
        if status == REVIEW and kb is not None and n < kb.review_min_count:
            status = INFO
        if kb is None and n >= UNKNOWN_REVIEW_COUNT:
            status = REVIEW
        row = {"Count": str(n), "Topic": kb.title if kb else "Not in the knowledge base", "Message": _first_text(msgs),
               "What it means": kb.meaning if kb else ("Frequent warning prereview does not know — read it in the "
                                                       ".err file and decide whether it affects results."
                                                       if n >= UNKNOWN_REVIEW_COUNT else
                                                       "Not in the knowledge base (rare in this run).")}
        if status == REVIEW:
            review_rows.append(row)
            if kb:
                review_fixes.extend(f for f in kb.fixes if f not in review_fixes)
            review_objs.extend(o for o in _example_objects(msgs) if o not in review_objs)
        else:
            info_rows.append(row)
    if review_rows:
        out.append(Finding(
            "HEALTH-WARN", "Warnings that may affect results", REVIEW,
            f"{len(review_rows)} kind{'s' if len(review_rows) != 1 else ''} of warning may change results (or are "
            f"frequent and not in the knowledge base) and are likely to draw a review comment.",
            model=model.label, clause=CLAUSE_ERR, evidence=review_rows, objects=review_objs[:10],
            fixes=review_fixes))
    if info_rows:
        info_rows.sort(key=lambda r: -int(r["Count"]))
        unused = ", ".join(f"{v} {k}" for k, v in err.unused.items()) if err.unused else ""
        unknown = sum(1 for r in info_rows if r["Topic"] == "Not in the knowledge base")
        out.append(Finding(
            "HEALTH-INFO", "Other warnings", INFO,
            f"{len(info_rows)} other kind{'s' if len(info_rows) != 1 else ''} of warning: "
            f"{len(info_rows) - unknown} known to be informational, {unknown} rare and not in the knowledge base "
            f"(listed below)." + (f" Unused objects in the input: {unused}." if unused else ""),
            model=model.label, evidence=info_rows[:25], data={"unknown": unknown}))
    return out


def check(ctx: Context) -> List[Finding]:
    findings: List[Finding] = []
    for m in ctx.models:
        findings.extend(check_model(m))
    return findings
