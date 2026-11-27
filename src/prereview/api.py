"""One call from run folders to findings, shared by the command line and the web version."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import __version__
from .model import ModelRun
from .rules import run_all
from .rules.base import Context, Finding, ProjectInfo


def load_context(proposed: str, baselines: List[str], project: ProjectInfo,
                 labels: Optional[List[str]] = None) -> Context:
    """Load the runs (raises ValueError for an ambiguous folder) and label baselines by rotation."""
    p = ModelRun.load(proposed, "proposed", "Proposed")
    bl = []
    for i, b in enumerate(baselines):
        label = labels[i] if labels and i < len(labels) else None
        bl.append(ModelRun.load(b, "baseline", label or f"Baseline {i + 1}"))
    for b in bl:
        if not (labels and b.label in labels) and b.sql is not None:
            rot = ((b.north_axis() or 0) + (b.appendix_g_rotation() or 0) - (p.north_axis() or 0)) % 360
            b.label = f"Baseline {rot:.0f}°"
    seen: Dict[str, int] = {}
    for b in bl:
        seen[b.label] = seen.get(b.label, 0) + 1
        if seen[b.label] > 1:
            b.label = f"{b.label} ({seen[b.label]})"
    return Context(p, bl, project)


def review(proposed: str, baselines: List[str], project: ProjectInfo,
           labels: Optional[List[str]] = None) -> Tuple[Context, List[Finding]]:
    ctx = load_context(proposed, baselines, project, labels)
    return ctx, run_all(ctx)


def to_json(ctx: Context, findings: List[Finding]) -> Dict:
    return {"prereview": __version__, "project": dict(ctx.project.__dict__),
            "models": [{"label": m.label, "role": m.role, "path": m.path} for m in ctx.models],
            "findings": [f.to_dict() for f in findings]}
