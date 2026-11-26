"""Findings, statuses and the review context shared by all rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..model import ModelRun

# Status of one finding, ordered from worst to best for sorting.
FAIL = "FAIL"        # a reviewer would reject this
REVIEW = "REVIEW"    # likely to draw a comment; check or justify
PASS = "PASS"        # checked and fine
INFO = "INFO"        # context, no action needed
SKIPPED = "SKIPPED"  # could not be checked (missing data)

STATUS_ORDER = {FAIL: 0, REVIEW: 1, SKIPPED: 2, INFO: 3, PASS: 4}

EDITIONS = ("2010", "2016")  # ASHRAE 90.1 Appendix G editions (LEED v4, v4.1)


@dataclass
class Finding:
    rule_id: str
    title: str
    status: str
    message: str
    model: str = ""                 # label of the model the finding is about ("" = pair)
    clause: str = ""                # standard reference shown to the user
    evidence: List[Dict[str, Any]] = field(default_factory=list)  # rows for an evidence table
    objects: List[str] = field(default_factory=list)              # EnergyPlus object names involved
    fixes: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)            # machine-readable extras

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id, "title": self.title, "status": self.status,
            "message": self.message, "model": self.model, "clause": self.clause,
            "evidence": self.evidence, "objects": self.objects, "fixes": self.fixes,
            "data": self.data,
        }


@dataclass
class ProjectInfo:
    """Facts about the building that the models alone cannot tell us."""
    name: str = "Untitled project"
    edition: str = "2010"
    building_type: Optional[str] = None      # nonresidential | residential | heated_only_storage
    floors_above_grade: Optional[int] = None
    heating_source: Optional[str] = None     # fossil (incl. hybrid, purchased heat) | electric — 90.1-2010
    climate_zone: Optional[str] = None       # ASHRAE 169 zone, e.g. "2A", "4C" — 90.1-2016 system type
    rotation_exception: bool = False         # orientation dictated by site (rating authority approval)
    notes: List[str] = field(default_factory=list)


@dataclass
class Context:
    proposed: Optional[ModelRun]
    baselines: List[ModelRun]
    project: ProjectInfo

    @property
    def models(self) -> List[ModelRun]:
        return ([self.proposed] if self.proposed else []) + list(self.baselines)

    @property
    def simulated(self) -> List[ModelRun]:
        """Models that ran to completion and produced results (a run that stopped early is only
        reviewed for simulation health)."""
        return [m for m in self.models if m.sql is not None and m.completed]

    @property
    def pair_ready(self) -> bool:
        ok = self.simulated
        return self.proposed is not None and self.proposed in ok and any(b in ok for b in self.baselines)

    def complete_baselines(self) -> List[ModelRun]:
        ok = self.simulated
        return [b for b in self.baselines if b in ok]

    @property
    def edition(self) -> str:
        return self.project.edition


def worst(statuses: List[str]) -> str:
    if not statuses:
        return PASS
    return min(statuses, key=lambda s: STATUS_ORDER.get(s, 9))
