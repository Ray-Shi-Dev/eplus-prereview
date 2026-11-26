"""Parse eplusout.err into structured messages and summary counts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_LEVEL = re.compile(r"^\s*\*\*\s*(Warning|Severe|Fatal)\s*\*\*\s?(.*)$")
_CONT = re.compile(r"^\s*\*\*\s*~~~\s*\*\*\s?(.*)$")
_RECUR_LEVEL = re.compile(r"^\s*\*{13}\s+\*\*\s*(Warning|Severe|Fatal)\s*\*\*\s?(.*)$")
_RECUR_CONT = re.compile(r"^\s*\*{13}\s+\*\*\s*~~~\s*\*\*\s?(.*)$")
_COUNT = re.compile(r"This error occurred\s+(\d+)\s+total times", re.I)
_SUMMARY = re.compile(
    r"EnergyPlus (Completed Successfully|Terminated--Fatal Error Detected|Terminated--Error\(s\) Detected)"
    r"\S*\s+(\d+)\s+Warning;\s+(\d+)\s+Severe Errors", re.I)
_STAGE_SUMMARY = re.compile(r"EnergyPlus (Warmup|Sizing) Error Summary\. During \w+: (\d+) Warning; (\d+) Severe", re.I)
_OBJ = re.compile(r'([A-Za-z][A-Za-z0-9:]*)\s*=\s*"([^"]+)"')
_QUOTED = re.compile(r'"[^"]*"')
_NUMBER = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_STARS = re.compile(r"^\s*\*{13}\s+(.*\S)\s*$")
_UNUSED = re.compile(r"There (?:are|is) (\d+) (?:nominally )?unused (\w+(?: \w+)?) in input", re.I)
_CONTINUES = re.compile(r"\s*(?:-\s*)?(?:error continues\.*|continues\.*)\s*(?:Sensible load statistics:|statistics:)?\s*$", re.I)


@dataclass
class ErrMessage:
    level: str  # Warning | Severe | Fatal
    text: str
    details: List[str] = field(default_factory=list)
    count: int = 1
    recurring: bool = False

    @property
    def full_text(self) -> str:
        return " ".join([self.text] + self.details)

    def objects(self) -> List[Tuple[str, str]]:
        return _OBJ.findall(self.full_text)

    def template(self) -> str:
        t = _CONTINUES.sub("", self.text)
        t = re.sub(r"\s*error continues\.*.*$", "", t, flags=re.I)
        t = _QUOTED.sub('"…"', t)
        t = _NUMBER.sub("#", t)
        return re.sub(r"\s+", " ", t).strip().rstrip(".;: ")


@dataclass
class ErrSummary:
    messages: List[ErrMessage]
    completed: bool
    fatal: bool
    warnings_total: Optional[int]
    severe_total: Optional[int]
    warmup: Tuple[int, int] = (0, 0)
    sizing: Tuple[int, int] = (0, 0)
    version: Optional[str] = None
    unused: Dict[str, int] = field(default_factory=dict)

    def by_level(self, level: str) -> List[ErrMessage]:
        return [m for m in self.messages if m.level == level]


def parse_err(text: str) -> ErrSummary:
    messages: List[ErrMessage] = []
    current: Optional[ErrMessage] = None
    in_recurring = False
    completed = False
    fatal = False
    warn_total = sev_total = None
    warmup = sizing = (0, 0)
    version = None
    unused: Dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith("Program Version"):
            m = re.search(r"Version\s+([\d.]+)", line)
            version = m.group(1) if m else None
            continue
        if "Recurring Error Summary" in line:
            in_recurring = True
            current = None
            continue
        m = _SUMMARY.search(line)
        if m:
            completed = m.group(1).lower().startswith("completed")
            fatal = "fatal" in m.group(1).lower()
            warn_total, sev_total = int(m.group(2)), int(m.group(3))
            current = None
            continue
        m = _STAGE_SUMMARY.search(line)
        if m:
            pair = (int(m.group(2)), int(m.group(3)))
            if m.group(1).lower() == "warmup":
                warmup = pair
            else:
                sizing = pair
            continue
        if in_recurring:
            m = _RECUR_LEVEL.match(line)
            if m:
                current = ErrMessage(m.group(1), m.group(2).strip(), recurring=True)
                messages.append(current)
                continue
            m = _RECUR_CONT.match(line)
            if m and current is not None:
                c = _COUNT.search(m.group(1))
                if c:
                    current.count = int(c.group(1))
                else:
                    current.details.append(m.group(1).strip())
                continue
            continue
        m = _LEVEL.match(line)
        if m:
            current = ErrMessage(m.group(1), m.group(2).strip())
            messages.append(current)
            if current.level == "Fatal":
                fatal = True
            continue
        m = _CONT.match(line)
        if m and current is not None:
            current.details.append(m.group(1).strip())
            continue
        m = _STARS.match(line)
        if m:
            body = m.group(1)
            u = _UNUSED.search(body)
            if u:
                unused[u.group(2).lower()] = int(u.group(1))
            elif current is not None and body.startswith("Key="):
                current.details.append(body)
            continue
    for m in messages:
        u = _UNUSED.search(m.text)
        if u:
            unused.setdefault(u.group(2).lower(), int(u.group(1)))
    return ErrSummary(messages, completed, fatal, warn_total, sev_total, warmup, sizing, version, unused)


def read_err(path: str) -> ErrSummary:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_err(fh.read())


def total_count(msgs: List[ErrMessage]) -> int:
    """Occurrences of a group of messages. A recurring message's summary count already includes
    its first occurrence, so recurring totals win over individually printed copies."""
    rec = [m for m in msgs if m.recurring]
    if rec:
        # copies printed individually for objects that have no recurring summary still count
        rec_objs = {tuple(m.objects()) for m in rec}
        extra = sum(1 for m in msgs if not m.recurring and tuple(m.objects()) not in rec_objs)
        return sum(m.count for m in rec) + extra
    return sum(m.count for m in msgs)


def group_messages(messages: List[ErrMessage]) -> List[Tuple[str, str, int, List[ErrMessage]]]:
    """Group messages by (level, template). Returns (level, template, total_count, messages) sorted
    by severity then count."""
    groups: Dict[Tuple[str, str], List[ErrMessage]] = {}
    for m in messages:
        groups.setdefault((m.level, m.template()), []).append(m)
    rank = {"Fatal": 0, "Severe": 1, "Warning": 2}
    out = [(lvl, tpl, total_count(ms), ms) for (lvl, tpl), ms in groups.items()]
    out.sort(key=lambda g: (rank.get(g[0], 3), -g[2]))
    return out
