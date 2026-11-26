"""Rule modules. Each exposes check(ctx) -> list[Finding]."""

from __future__ import annotations

from typing import List

from .base import Context, Finding


def run_all(ctx: Context) -> List[Finding]:
    from . import health, unmet, consistency, baseline2010

    findings: List[Finding] = []
    for module in (health, unmet, consistency, baseline2010):
        findings.extend(module.check(ctx))
    return findings
