"""Score prereview against the seeded-fault lab (see lab.py)."""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List

from faultlab.lab import BUILDINGS, CLEAN_PROJECT, ROTATIONS, Expect, faults
from prereview.model import ModelRun
from prereview.rules import run_all
from prereview.rules.base import FAIL, REVIEW, Context, Finding, ProjectInfo


def _project(d: Dict) -> ProjectInfo:
    return ProjectInfo(name=d.get("name", "lab"), edition=d.get("edition", "2010"),
                       building_type=d.get("building_type"), floors_above_grade=d.get("floors_above_grade"),
                       heating_source=d.get("heating_source"), rotation_exception=d.get("rotation_exception", False))


def _load(path: str, role: str, label: str, cache: Dict[str, ModelRun]) -> ModelRun:
    key = f"{path}|{label}"
    if key not in cache:
        cache[key] = ModelRun.load(path, role, label)
    return cache[key]


def _match(e: Expect, findings: List[Finding]) -> Dict:
    cands = [f for f in findings if f.rule_id == e.rule and (e.status == "*" or f.status == e.status)
             and (e.model is None or f.model == e.model)]
    detail = ""
    for f in cands:
        if e.zone or e.cause:
            diag = f.data.get("diagnosis") or []
            if diag:
                top = diag[0]
                zone_ok = e.zone is None or top["zone"].upper() == e.zone.upper()
                cause_ok = e.cause is None or top["cause"] == e.cause
                detail = f"top contributor {top['zone']} ({top['mode']}): {top['cause']}"
                if zone_ok and cause_ok:
                    return {"ok": True, "detail": detail}
            elif e.zone and e.zone.upper() in [o.upper() for o in f.objects]:
                return {"ok": True, "detail": f"zone {e.zone} listed"}
            elif e.zone:
                detail = "zones listed: " + ", ".join(f.objects[:5])
        else:
            return {"ok": True, "detail": f.message[:160]}
    if not cands:
        same_rule = [f for f in findings if f.rule_id == e.rule and (e.model is None or f.model == e.model)]
        detail = "; ".join(f"{f.status}: {f.message[:120]}" for f in same_rule[:2]) or "rule produced no finding"
    return {"ok": False, "detail": detail}


def score(lab: str) -> Dict:
    cache: Dict[str, ModelRun] = {}
    t0 = time.time()
    result: Dict = {"clean": {}, "faults": [], "generated": time.strftime("%Y-%m-%d %H:%M")}
    for b in BUILDINGS:
        runs = os.path.join(lab, "runs", b)
        p = _load(os.path.join(runs, "proposed"), "proposed", "proposed", cache)
        bl = [_load(os.path.join(runs, f"B{r:03d}"), "baseline", f"B{r:03d}", cache) for r in ROTATIONS]
        findings = run_all(Context(p, bl, _project(CLEAN_PROJECT[b])))
        result["clean"][b] = {
            "fails": [f.to_dict() for f in findings if f.status == FAIL],
            "reviews": [{"rule": f.rule_id, "model": f.model, "message": f.message} for f in findings
                        if f.status == REVIEW],
            "n_findings": len(findings),
        }
    for f in faults():
        runs = os.path.join(lab, "runs", f.building)
        proj = dict(CLEAN_PROJECT[f.building])
        proj.update(f.project)
        if f.target == "proposed":
            p = _load(os.path.join(lab, "runs", "faults", f.id), "proposed", "proposed", cache)
        else:
            p = _load(os.path.join(runs, "proposed"), "proposed", "proposed", cache)
        labels = f.baselines or [f"B{r:03d}" for r in ROTATIONS]
        bl = []
        for lbl in labels:
            if f.target == lbl:
                bl.append(_load(os.path.join(lab, "runs", "faults", f.id), "baseline", lbl, cache))
            else:
                bl.append(_load(os.path.join(runs, lbl), "baseline", lbl, cache))
        findings = run_all(Context(p, bl, _project(proj)))
        checks = [dict(rule=e.rule, status=e.status, model=e.model, zone=e.zone, cause=e.cause, **_match(e, findings))
                  for e in f.expect]
        expected_rules = {e.rule for e in f.expect}
        collateral = [{"rule": x.rule_id, "model": x.model, "message": x.message[:200]} for x in findings
                      if x.status == FAIL and x.rule_id not in expected_rules]
        result["faults"].append({"id": f.id, "building": f.building, "target": f.target, "title": f.title,
                                 "detected": all(c["ok"] for c in checks), "checks": checks,
                                 "other_fails": collateral})
    n = len(result["faults"])
    det = sum(1 for x in result["faults"] if x["detected"])
    clean_fails = sum(len(v["fails"]) for v in result["clean"].values())
    result["summary"] = {"faults": n, "detected": det, "clean_sets": len(result["clean"]),
                         "clean_fail_findings": clean_fails, "seconds": round(time.time() - t0, 1)}
    with open(os.path.join(lab, "scorecard.json"), "w") as fh:
        json.dump(result, fh, indent=1, default=str)
    with open(os.path.join(lab, "scorecard.md"), "w") as fh:
        fh.write(markdown(result))
    print(json.dumps(result["summary"]))
    for x in result["faults"]:
        if not x["detected"]:
            print("MISSED", x["id"], x["title"], [c["detail"] for c in x["checks"] if not c["ok"]])
    for b, v in result["clean"].items():
        for fl in v["fails"]:
            print("CLEAN FAIL", b, fl["rule_id"], fl["model"], fl["message"][:150])
    return result


def markdown(r: Dict) -> str:
    s = r["summary"]
    lines = [f"# Fault lab scorecard ({r['generated']})", "",
             f"- Seeded faults detected with the right rule (and zone/cause where specified): "
             f"**{s['detected']} / {s['faults']}**",
             f"- FAIL findings on clean proposed/baseline sets: **{s['clean_fail_findings']}** "
             f"({s['clean_sets']} buildings × proposed + 4 baseline orientations)", "",
             "| Fault | Building | Seeded in | What was seeded | Expected | Result |", "|---|---|---|---|---|---|"]
    for x in r["faults"]:
        exp = "; ".join(f"{c['rule']} {c['status']}" + (f" zone {c['zone']}" if c["zone"] else "") +
                        (f" cause {c['cause']}" if c["cause"] else "") for c in x["checks"])
        res = "✅ " if x["detected"] else "❌ "
        res += " / ".join(c["detail"].replace("|", "/")[:110] for c in x["checks"])
        lines.append(f"| {x['id']} | {x['building']} | {x['target']} | {x['title']} | {exp} | {res} |")
    lines += ["", "## Clean sets", ""]
    for b, v in r["clean"].items():
        lines.append(f"- **{b}**: {len(v['fails'])} FAIL, {len(v['reviews'])} REVIEW")
        for rv in v["reviews"]:
            lines.append(f"  - REVIEW {rv['rule']} {rv['model']}: {rv['message'][:200]}")
        for fl in v["fails"]:
            lines.append(f"  - FAIL {fl['rule_id']} {fl['model']}: {fl['message'][:200]}")
    return "\n".join(lines) + "\n"
