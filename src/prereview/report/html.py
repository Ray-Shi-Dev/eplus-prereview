"""Self-contained HTML report (inline CSS and SVG, no external files)."""

from __future__ import annotations

import datetime as _dt
import html
from typing import Dict, List, Optional

from .. import __version__
from ..rules.base import FAIL, INFO, PASS, REVIEW, SKIPPED, STATUS_ORDER, Context, Finding

SECTIONS = [
    ("health", "Simulation health", ("HEALTH-",)),
    ("unmet", "Unmet load hours", ("UMLH-", "SIZE-")),
    ("pair", "Baseline vs proposed — items that must match", ("CONS-",)),
    ("baseline", "Baseline rules", ("BASE-",)),
    ("leed", "LEED submittal checks", ("LEED-",)),
]

CAUSE_LABEL = {
    "never_delivers": "No heating/cooling delivered",
    "system_off": "System off while occupied",
    "morning_recovery": "Morning recovery",
    "hard_sized_below_design": "Hard-sized below design",
    "supply_air_not_at_setpoint": "Coil can't hold supply-air setpoint",
    "plant_not_at_setpoint": "Plant can't hold supply-water setpoint",
    "terminal_at_max_airflow": "Terminal at maximum airflow",
    "beyond_design_day": "Weather beyond design day",
    "capacity_limited": "At capacity",
    "partial_output": "Controls / part-load",
    "marginal_misses": "Small misses (< 0.5 K)",
}

CSS = """
:root{--fail:#b42318;--fail-bg:#fef3f2;--review:#b54708;--review-bg:#fffaeb;--pass:#067647;--pass-bg:#ecfdf3;
--info:#344054;--info-bg:#f2f4f7;--skip:#667085;--skip-bg:#f9fafb;--ink:#101828;--muted:#475467;--line:#e4e7ec;
--accent:#1d4ed8}
*{box-sizing:border-box}
body{margin:0;font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans TC","Helvetica Neue",Arial,sans-serif;
color:var(--ink);background:#f6f7f9}
main{max-width:1120px;margin:0 auto;padding:32px 24px 64px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:19px;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:16px;margin:0}
.sub{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.stat{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat b{display:block;font-size:26px;line-height:1.2}
.stat span{color:var(--muted);font-size:13px}
.verdict{border-radius:12px;padding:16px 18px;margin:8px 0 4px;font-size:16px;border:1px solid}
.verdict.fail{background:var(--fail-bg);border-color:#fecdca;color:#7a271a}
.verdict.review{background:var(--review-bg);border-color:#fedf89;color:#7a2e0e}
.verdict.pass{background:var(--pass-bg);border-color:#abefc6;color:#074d31}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f9fafb;font-weight:600;color:var(--muted)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.card{background:#fff;border:1px solid var(--line);border-left:4px solid var(--info);border-radius:10px;padding:14px 16px;margin:10px 0}
.card.FAIL{border-left-color:var(--fail)}.card.REVIEW{border-left-color:var(--review)}
.card.PASS{border-left-color:var(--pass)}.card.SKIPPED{border-left-color:var(--skip)}
.head{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.pill{font-size:12px;font-weight:700;letter-spacing:.03em;border-radius:999px;padding:2px 9px}
.pill.FAIL{color:var(--fail);background:var(--fail-bg)}.pill.REVIEW{color:var(--review);background:var(--review-bg)}
.pill.PASS{color:var(--pass);background:var(--pass-bg)}.pill.INFO{color:var(--info);background:var(--info-bg)}
.pill.SKIPPED{color:var(--skip);background:var(--skip-bg)}
.model{color:var(--muted);font-size:13px}
.rid{color:var(--muted);font:12px ui-monospace,SFMono-Regular,Menlo,monospace;margin-left:auto}
.msg{margin:6px 0 4px}
.clause{color:var(--muted);font-size:12.5px;margin:4px 0}
.fixes{margin:6px 0 0 0;padding-left:20px}
.fixes li{margin:2px 0}
.objs{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;color:#344054;margin-top:6px;word-break:break-word}
details{margin-top:8px}
summary{cursor:pointer;color:var(--accent);font-size:13px}
.passlist{background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0}
.passlist li{margin:4px 0}
.todo{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px 12px 36px}
.todo li{margin:6px 0}.todo a{color:var(--ink);font-weight:600;text-decoration:none}
.todo a:hover{text-decoration:underline}
.diag{display:grid;grid-template-columns:1fr;gap:10px}
.tag{display:inline-block;font-size:12px;border-radius:6px;padding:1px 7px;background:#eef4ff;color:#1e40af;margin-right:6px}
.note{color:var(--muted);font-size:13px}
footer{margin-top:40px;color:var(--muted);font-size:13px}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:#f2f4f7;padding:1px 4px;border-radius:4px}
@media print{body{background:#fff}.card,.stat,table{break-inside:avoid}details{display:block}}
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _section_of(f: Finding) -> str:
    for key, _, prefixes in SECTIONS:
        if f.rule_id.startswith(prefixes):
            return key
    return "other"


def _table(rows: List[Dict], max_rows: int = 40) -> str:
    if not rows:
        return ""
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    out = ["<table><thead><tr>"]
    for c in cols:
        out.append(f"<th>{_e(c)}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows[:max_rows]:
        out.append("<tr>")
        for c in cols:
            v = r.get(c, "")
            cls = ' class="num"' if isinstance(v, (int, float)) or (isinstance(v, str) and
                                                                     v.replace(",", "").replace(".", "", 1)
                                                                     .replace("-", "", 1).isdigit()) else ""
            out.append(f"<td{cls}>{_e(v)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    if len(rows) > max_rows:
        out.append(f'<p class="note">… {len(rows) - max_rows} more rows in the JSON output.</p>')
    return "".join(out)


def _anchor(f: Finding) -> str:
    raw = f"{f.rule_id}-{f.model}".lower()
    return "f-" + "".join(ch if ch.isalnum() else "-" for ch in raw)


def _card(f: Finding) -> str:
    parts = [f'<div class="card {f.status}" id="{_anchor(f)}"><div class="head"><span class="pill {f.status}">'
             f'{f.status}</span><h3>{_e(f.title)}</h3>']
    if f.model:
        parts.append(f'<span class="model">{_e(f.model)}</span>')
    parts.append(f'<span class="rid">{_e(f.rule_id)}</span></div>')
    parts.append(f'<p class="msg">{_e(f.message)}</p>')
    if f.clause:
        parts.append(f'<p class="clause">{_e(f.clause)}</p>')
    if f.fixes:
        parts.append('<ul class="fixes">' + "".join(f"<li>{_e(x)}</li>" for x in f.fixes) + "</ul>")
    if f.objects:
        parts.append('<div class="objs">Objects: ' + _e(", ".join(f.objects[:20])) +
                     (" …" if len(f.objects) > 20 else "") + "</div>")
    if f.evidence:
        open_attr = " open" if f.status in (FAIL, REVIEW) and len(f.evidence) <= 8 else ""
        parts.append(f"<details{open_attr}><summary>Evidence ({len(f.evidence)} row"
                     f"{'s' if len(f.evidence) != 1 else ''})</summary>{_table(f.evidence)}</details>")
    parts.append("</div>")
    return "".join(parts)


def _unmet_chart(findings: List[Finding]) -> str:
    bars = []
    summed = False
    for f in findings:
        if f.rule_id != "UMLH-300" or not f.data:
            continue
        v = f.data.get("union_hours")
        if v is None:
            rh, rc = f.data.get("reported_heating"), f.data.get("reported_cooling")
            v = (rh or 0) + (rc or 0) if (rh is not None or rc is not None) else None
            summed = summed or v is not None
        s = f.data.get("reported_leed")
        if v is not None:
            bars.append((f.model, float(v), float(s) if s is not None else None))
    if not bars:
        return ""
    w, row_h, left = 760, 30, 120
    top = 26
    h = top + row_h * len(bars) + 30
    vmax = max([300.0] + [b[1] for b in bars] + [b[2] or 0 for b in bars]) * 1.1
    x = lambda v: left + (w - left - 70) * v / vmax  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Unmet load hours by model" '
           f'style="background:#fff;border:1px solid #e4e7ec;border-radius:10px">']
    title = ("Unmet load hours (any zone, occupied) — dashed: EnergyPlus heating + cooling total"
             if not summed else "Unmet load hours — heating + cooling total (no timestep output for an any-zone count)")
    out.append(f'<text x="{left}" y="17" font-size="12" fill="#475467">{_e(title)}</text>')
    for i, (label, v, s) in enumerate(bars):
        y = top + i * row_h
        color = "#d92d20" if v > 300 else "#1d4ed8"
        out.append(f'<text x="{left - 8}" y="{y + 18}" font-size="12.5" text-anchor="end" fill="#101828">'
                   f"{_e(label)}</text>")
        out.append(f'<rect x="{left}" y="{y + 6}" width="{max(1.0, x(v) - left):.1f}" height="16" rx="3" '
                   f'fill="{color}" opacity="0.85"/>')
        if s is not None and abs(s - v) > 0.5:
            out.append(f'<line x1="{x(s):.1f}" x2="{x(s):.1f}" y1="{y + 3}" y2="{y + 25}" stroke="#101828" '
                       f'stroke-dasharray="3 2"/>')
        out.append(f'<text x="{x(max(v, s or 0)) + 6:.1f}" y="{y + 18}" font-size="12" fill="#101828">'
                   f"{v:,.0f} h</text>")
    lx = x(300)
    out.append(f'<line x1="{lx:.1f}" x2="{lx:.1f}" y1="{top - 2}" y2="{h - 22}" stroke="#b42318" stroke-width="1.5"/>')
    out.append(f'<text x="{lx:.1f}" y="{h - 8}" font-size="12" text-anchor="middle" fill="#b42318">300 h limit</text>')
    out.append("</svg>")
    return "".join(out)


def _diagnosis_block(findings: List[Finding]) -> str:
    out = []
    for f in findings:
        if f.rule_id != "UMLH-300":
            continue
        diag = f.data.get("diagnosis") or []
        if not diag:
            continue
        if f.status == PASS:
            top = diag[0]
            out.append(f'<details class="passlist"><summary>{_e(f.model)}: within the limit — the remaining unmet '
                       f'hours come mostly from {_e(top["zone"])} ({_e(top["mode"])}), '
                       f'{_e(CAUSE_LABEL.get(top["cause"], top["cause"]).lower())}</summary>'
                       + _table([{"Zone": d["zone"], "Mode": d["mode"], "Unmet h": f"{d['hours']:.1f}",
                                  "Primary cause": CAUSE_LABEL.get(d["cause"], d["cause"])} for d in diag])
                       + "</details>")
            continue
        rows = []
        for d in diag:
            rows.append({"Zone": d["zone"], "Mode": d["mode"], "Unmet h": f"{d['hours']:.1f}",
                         "Share": f"{d['share']:.0%}", "Primary cause": CAUSE_LABEL.get(d["cause"], d["cause"]),
                         "Served by": ", ".join(d.get("served_by") or [])})
        ev = "".join(f"<li><b>{_e(d['zone'])} ({_e(d['mode'])})</b><ul>" +
                     "".join(f"<li>{_e(x)}</li>" for x in d["evidence"]) + "</ul></li>" for d in diag[:4])
        out.append(f'<div class="card {f.status}"><div class="head"><span class="pill {f.status}">{f.status}'
                   f'</span><h3>Where the unmet hours come from — {_e(f.model)}</h3></div>{_table(rows)}'
                   f"<details open><summary>Evidence by zone</summary><ul>{ev}</ul></details></div>")
    return "".join(out)


def _todo(findings: List[Finding], order: Dict[str, int]) -> str:
    items = [f for f in findings if f.status in (FAIL, REVIEW)]
    if not items:
        return ""
    items.sort(key=lambda f: (STATUS_ORDER.get(f.status, 9), _section_rank(f), f.rule_id, order.get(f.model, -1)))
    lis = []
    for f in items:
        where = f" — {_e(f.model)}" if f.model else ""
        lis.append(f'<li><span class="pill {f.status}">{f.status}</span> <a href="#{_anchor(f)}">{_e(f.title)}</a>'
                   f'{where}<div class="note">{_e(f.message[:240])}{"…" if len(f.message) > 240 else ""}</div></li>')
    return '<h2>What to fix first</h2><ol class="todo">' + "".join(lis) + "</ol>"


def _section_rank(f: Finding) -> int:
    for i, (key, _, prefixes) in enumerate(SECTIONS):
        if f.rule_id.startswith(prefixes):
            return i
    return len(SECTIONS)


def _harmless_warnings(infos: List[Finding], order: Dict[str, int]) -> str:
    """One card for the HEALTH-INFO findings of all models: topic × model counts."""
    if not infos:
        return ""
    infos = sorted(infos, key=lambda f: order.get(f.model, 99))
    models = [f.model for f in infos]
    table: Dict[str, Dict[str, str]] = {}
    meaning: Dict[str, str] = {}
    for f in infos:
        for row in f.evidence:
            key = row.get("Topic", "") + "|" + row.get("Message", "")[:90]
            table.setdefault(key, {})[f.model] = row.get("Count", "")
            meaning[key] = row.get("What it means", "")
    rows = []
    for key, counts in sorted(table.items(), key=lambda kv: -max(int(v or 0) for v in kv[1].values())):
        topic, msg = key.split("|", 1)
        r = {"Topic": topic, "Example message": msg}
        for m in models:
            r[m] = counts.get(m, "")
        r["What it means"] = meaning[key]
        rows.append(r)
    unused = "; ".join(f"{f.model}: {f.message.split('Unused objects in the input: ')[1].rstrip('.')}"
                       for f in infos if "Unused objects in the input: " in f.message)
    return (f'<div class="card INFO"><div class="head"><span class="pill INFO">INFO</span><h3>Other warnings '
            f'(informational or rare)</h3><span class="rid">HEALTH-INFO</span></div><p class="msg">'
            f'{len(rows)} kinds of warning across {len(models)} model{"s" if len(models) != 1 else ""}. '
            f'Rows marked “Not in the knowledge base” are rare warnings prereview cannot explain; the others are '
            f'known to be informational.</p>'
            + (f'<p class="note">Unused objects — {_e(unused)}</p>' if unused else "")
            + f"<details><summary>Warnings by model ({len(rows)} rows)</summary>{_table(rows)}</details></div>")


def _passed(passed: List[Finding], order: Dict[str, int]) -> str:
    if not passed:
        return ""
    groups: Dict[str, List[Finding]] = {}
    for f in passed:
        groups.setdefault(f.rule_id, []).append(f)
    lis = []
    for rid, fs in groups.items():
        fs.sort(key=lambda f: order.get(f.model, -1))
        if len(fs) > 1 and all(f.model for f in fs):
            msgs = {f.message for f in fs}
            detail = fs[0].message if len(msgs) == 1 else "; ".join(f"{f.model}: {f.message}" for f in fs)
            lis.append(f'<li><span class="pill PASS">PASS</span> {_e(fs[0].title)} — '
                       f'{_e(", ".join(f.model for f in fs))}<div class="note">{_e(detail)}</div></li>')
        else:
            for f in fs:
                lis.append(f'<li><span class="pill PASS">PASS</span> {_e(f.title)}'
                           f'{(" — " + _e(f.model)) if f.model else ""}<div class="note">{_e(f.message)}</div></li>')
    return '<div class="passlist"><b>Passed</b><ul>' + "".join(lis) + "</ul></div>"


def _models_table(ctx: Context) -> str:
    rows = []
    for m in ctx.models:
        sql = m.sql
        rows.append({
            "Model": m.label, "Role": m.role,
            "EnergyPlus": (sql.version() if sql else "") or (m.err.version if m.err else "") or "—",
            "Weather": m.weather() or "—",
            "Hours": f"{m.hours_simulated():.0f}" if m.hours_simulated() else "—",
            "Rotation": f"{(m.north_axis() or 0) + (m.appendix_g_rotation() or 0):.0f}°" if sql else "—",
            "Conditioned area (m²)": f"{m.conditioned_area():,.0f}" if m.conditioned_area() else "—",
            "Folder": m.path,
        })
    return _table(rows)


def render(ctx: Context, findings: List[Finding], title: Optional[str] = None) -> str:
    counts = {s: sum(1 for f in findings if f.status == s) for s in (FAIL, REVIEW, PASS, INFO, SKIPPED)}
    name = title or ctx.project.name
    if counts[FAIL]:
        verdict = ("fail", f"Not ready to submit: {counts[FAIL]} FAIL finding{'s' if counts[FAIL] != 1 else ''} "
                           f"(issues a reviewer is likely to reject) and {counts[REVIEW]} more to check or justify.")
    elif counts[REVIEW]:
        verdict = ("review", f"No blocking issues found. {counts[REVIEW]} item{'s' if counts[REVIEW] != 1 else ''} "
                             f"likely to draw a review comment — check or document them.")
    else:
        verdict = ("pass", "No issues found in the checks prereview runs. See the list of what was checked below.")
    edition = {"2010": "ASHRAE 90.1-2010 Appendix G (LEED v4)", "2016": "ASHRAE 90.1-2016 Appendix G (LEED v4.1)"}
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
           f"content=\"width=device-width,initial-scale=1\"><title>{_e(name)} — prereview</title>"
           f"<style>{CSS}</style></head><body><main>"]
    out.append(f"<h1>{_e(name)}</h1><p class=\"sub\">EnergyPlus pre-submission review · "
               f"{_e(edition.get(ctx.edition, ctx.edition))} · prereview {__version__} · {now}</p>")
    out.append(f'<div class="verdict {verdict[0]}">{_e(verdict[1])}</div>')
    out.append('<div class="grid">' + "".join(
        f'<div class="stat"><b style="color:var(--{k.lower() if k != SKIPPED else "skip"})">{counts[k]}</b>'
        f"<span>{k.title()}</span></div>" for k in (FAIL, REVIEW, PASS, INFO, SKIPPED)) + "</div>")
    order = {m.label: i for i, m in enumerate(ctx.models)}
    order[""] = -1
    out.append(_todo(findings, order))
    out.append("<h2>Models reviewed</h2>" + _models_table(ctx))
    for m in ctx.models:
        for n in m.notes:
            out.append(f'<p class="note">{_e(m.label)}: {_e(n)}</p>')

    by_sec: Dict[str, List[Finding]] = {}
    for f in findings:
        by_sec.setdefault(_section_of(f), []).append(f)
    for key, label, _ in SECTIONS + [("other", "Other", ())]:
        fs = by_sec.get(key)
        if not fs:
            continue
        fs.sort(key=lambda f: (STATUS_ORDER.get(f.status, 9), f.rule_id, order.get(f.model, 99)))
        out.append(f"<h2>{_e(label)}</h2>")
        if key == "unmet":
            out.append(_unmet_chart(sorted(fs, key=lambda f: order.get(f.model, 99))))
            out.append(_diagnosis_block(sorted(fs, key=lambda f: order.get(f.model, 99))))
        harmless = [f for f in fs if f.rule_id == "HEALTH-INFO"]
        shown = [f for f in fs if f.status in (FAIL, REVIEW, SKIPPED, INFO) and f.rule_id != "HEALTH-INFO"]
        passed = [f for f in fs if f.status == PASS]
        out.extend(_card(f) for f in shown)
        out.append(_harmless_warnings(harmless, order))
        out.append(_passed(passed, order))
    out.append("<footer><p><b>How to read this report.</b> FAIL: a reviewer would reject this as submitted. "
               "REVIEW: likely to draw a comment — fix it or document why it is right. SKIPPED: prereview could "
               "not check it with the data available. Numbers come from EnergyPlus's own output (SQL tables and "
               "hourly variables).</p><p>prereview is a pre-submission aid, not a compliance certification. "
               "Standard clauses are paraphrased; check the standard and your rating authority's guidance for "
               "final decisions.</p></footer></main></body></html>")
    return "".join(out)
