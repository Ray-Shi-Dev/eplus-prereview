"""Command line: `prereview run` (simulate, then review) and `prereview check` (review existing runs)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional

from . import __version__
from .rules.base import FAIL, REVIEW, ProjectInfo


def _load_toml(path: str) -> Dict:
    try:
        import tomllib  # Python 3.11+
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except ImportError:  # minimal fallback: flat key = value pairs
        data: Dict = {}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if "=" not in line or line.startswith("["):
                    continue
                k, v = (x.strip() for x in line.split("=", 1))
                if v.lower() in ("true", "false"):
                    data[k] = v.lower() == "true"
                elif v.strip('"').strip("'") != v:
                    data[k] = v.strip('"').strip("'")
                else:
                    try:
                        data[k] = int(v)
                    except ValueError:
                        data[k] = v
        return data


def project_from(path: Optional[str], args: argparse.Namespace) -> ProjectInfo:
    d: Dict = _load_toml(path) if path else {}
    d = d.get("project", d)
    pj = ProjectInfo(
        name=d.get("name", "Untitled project"),
        edition=str(d.get("edition", "2010")),
        building_type=d.get("building_type"),
        floors_above_grade=d.get("floors_above_grade"),
        heating_source=d.get("heating_source"),
        climate_zone=str(d["climate_zone"]) if d.get("climate_zone") is not None else None,
        rotation_exception=bool(d.get("rotation_exception", False)),
    )
    if getattr(args, "edition", None):
        pj.edition = args.edition
    if getattr(args, "name", None):
        pj.name = args.name
    for key in ("building_type", "floors_above_grade", "heating_source", "climate_zone"):
        v = getattr(args, key, None)
        if v is not None:
            setattr(pj, key, v)
    if pj.edition not in ("2010", "2016"):
        raise SystemExit(f"edition must be 2010 or 2016, not {pj.edition}")
    return pj


def review(proposed: str, baselines: List[str], project: ProjectInfo, out_html: Optional[str],
           out_json: Optional[str], labels: Optional[List[str]] = None) -> int:
    from .api import review as _review, to_json
    try:
        ctx, findings = _review(proposed, baselines, project, labels)
    except ValueError as exc:
        print(f"prereview: {exc}", file=sys.stderr)
        return 2
    if out_html:
        from .report.html import render
        os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
        with open(out_html, "w", encoding="utf-8") as fh:
            fh.write(render(ctx, findings))
    if out_json:
        os.makedirs(os.path.dirname(os.path.abspath(out_json)), exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(to_json(ctx, findings), fh, indent=1, default=str)
    n_fail = sum(1 for f in findings if f.status == FAIL)
    n_rev = sum(1 for f in findings if f.status == REVIEW)
    for f in sorted(findings, key=lambda f: {"FAIL": 0, "REVIEW": 1}.get(f.status, 2)):
        if f.status in (FAIL, REVIEW):
            where = f" [{f.model}]" if f.model else ""
            print(f"{f.status:6} {f.rule_id:16} {f.title}{where}: {f.message}")
    print(f"\n{n_fail} FAIL, {n_rev} REVIEW, {len(findings) - n_fail - n_rev} other findings."
          + (f" Report: {out_html}" if out_html else ""))
    return 1 if n_fail else 0


def _run_one(args):
    from .runner import run_model
    idf, epw, out, exe = args
    r = run_model(idf, epw, out, energyplus=exe)
    return out, r.returncode, r.elapsed_s


def cmd_run(a: argparse.Namespace) -> int:
    jobs = [(a.proposed, a.weather, os.path.join(a.out, "proposed"), a.energyplus)]
    for i, b in enumerate(a.baseline):
        jobs.append((b, a.weather, os.path.join(a.out, f"baseline-{i + 1}"), a.energyplus))
    print(f"Simulating {len(jobs)} model(s) with {a.jobs} parallel job(s)…", flush=True)
    with ProcessPoolExecutor(max(1, a.jobs)) as ex:
        for out, rc, t in ex.map(_run_one, jobs):
            print(f"  {out}: {'ok' if rc == 0 else 'EnergyPlus exit code ' + str(rc)} ({t:.0f} s)", flush=True)
    project = project_from(a.project, a)
    return review(jobs[0][2], [j[2] for j in jobs[1:]], project, a.html or os.path.join(a.out, "prereview.html"),
                  a.json or os.path.join(a.out, "prereview.json"))


def cmd_check(a: argparse.Namespace) -> int:
    project = project_from(a.project, a)
    return review(a.proposed, a.baseline or [], project, a.html, a.json)


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles/pipes may not encode "≤", "°", "—"
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="prereview", description="Find what an Appendix G / LEED reviewer would "
                                 "flag in EnergyPlus models, before you submit.")
    ap.add_argument("--version", action="version", version=f"prereview {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--project", help="project.toml with building facts (edition, floors, heating source…)")
        p.add_argument("--edition", choices=["2010", "2016"], help="ASHRAE 90.1 edition (default 2010 = LEED v4)")
        p.add_argument("--name", help="project name for the report")
        p.add_argument("--building-type", dest="building_type",
                       choices=["nonresidential", "residential", "heated_only_storage", "retail", "public_assembly",
                                "hospital"])
        p.add_argument("--climate-zone", dest="climate_zone",
                       help="ASHRAE 169 climate zone, e.g. 2A (90.1-2016 baseline system type)")
        p.add_argument("--floors", dest="floors_above_grade", type=int, help="floors above grade")
        p.add_argument("--heating", dest="heating_source", choices=["fossil", "electric"])
        p.add_argument("--html", help="write the HTML report here")
        p.add_argument("--json", help="write findings as JSON here")

    r = sub.add_parser("run", help="simulate proposed and baseline IDFs (adds the outputs prereview needs), "
                                   "then review them")
    r.add_argument("--proposed", required=True, help="proposed design IDF")
    r.add_argument("--baseline", nargs="+", default=[], help="baseline IDF(s), e.g. the four rotations")
    r.add_argument("--weather", required=True, help="EPW weather file")
    r.add_argument("--out", default="prereview-out", help="output folder (default prereview-out)")
    r.add_argument("--energyplus", help="path to the energyplus executable or install folder")
    r.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    common(r)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("check", help="review existing EnergyPlus run folders (or .sql files)")
    c.add_argument("--proposed", required=True, help="proposed run folder (eplusout.sql + eplusout.err)")
    c.add_argument("--baseline", nargs="*", default=[], help="baseline run folder(s)")
    common(c)
    c.set_defaults(func=cmd_check)

    a = ap.parse_args(argv)
    if a.cmd == "check" and not a.html and not a.json:
        a.html = "prereview.html"
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
