"""Minimal, dependency-free IDF reader and editor.

EnergyPlus IDF syntax: comma-separated fields, objects end with ';', and '!' starts a
comment that runs to the end of the line. This module keeps character spans so objects
can be replaced or removed without disturbing the rest of the file.

Field meaning is not interpreted here (no IDD); callers that edit fields document the
field order they rely on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass
class IdfObject:
    cls: str
    fields: List[str]
    start: int
    end: int  # exclusive, just after the terminating ';'
    line: int

    @property
    def key(self) -> str:
        return self.cls.upper()

    @property
    def name(self) -> str:
        return self.fields[0] if self.fields else ""

    def get(self, index: int, default: str = "") -> str:
        return self.fields[index] if 0 <= index < len(self.fields) else default


def _strip_comment(line: str) -> str:
    cut = line.find("!")
    return line if cut < 0 else line[:cut]


def parse(text: str) -> List[IdfObject]:
    """Parse IDF text into objects with character spans."""
    objects: List[IdfObject] = []
    fields: List[str] = []
    buf: List[str] = []
    obj_start: Optional[int] = None
    obj_line = 0
    pos = 0
    line_no = 0
    for raw_line in text.splitlines(keepends=True):
        line_no += 1
        stripped = raw_line.lstrip()
        if stripped.startswith("##"):  # EPMacro directive, not IDF content
            pos += len(raw_line)
            continue
        content = _strip_comment(raw_line)
        for i, ch in enumerate(content):
            if ch in ",;":
                if obj_start is None:
                    obj_start = pos + i
                    obj_line = line_no
                fields.append("".join(buf).strip())
                buf = []
                if ch == ";":
                    cls = fields[0] if fields else ""
                    objects.append(IdfObject(cls, fields[1:], obj_start, pos + i + 1, obj_line))
                    fields = []
                    obj_start = None
            else:
                if obj_start is None and not ch.isspace():
                    obj_start = pos + i
                    obj_line = line_no
                if ch not in "\r\n":
                    buf.append(ch)
                else:
                    buf.append(" ")
        pos += len(raw_line)
    return objects


def format_object(cls: str, fields: Iterable[str]) -> str:
    vals = [cls] + [str(f) for f in fields]
    if len(vals) == 1:
        return vals[0] + ";\n"
    body = ",\n".join("    " + v for v in vals[1:])
    return f"{vals[0]},\n{body};\n"


class Idf:
    def __init__(self, text: str):
        self.text = text
        self.objects = parse(text)

    @classmethod
    def from_file(cls, path: str) -> "Idf":
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        return cls(text)

    # ----- queries -------------------------------------------------------------
    def by_class(self, cls: str) -> List[IdfObject]:
        k = cls.upper()
        return [o for o in self.objects if o.key == k]

    def first(self, cls: str) -> Optional[IdfObject]:
        found = self.by_class(cls)
        return found[0] if found else None

    def find(self, cls: str, name: str) -> Optional[IdfObject]:
        n = name.upper()
        for o in self.by_class(cls):
            if o.name.upper() == n:
                return o
        return None

    def classes(self) -> List[str]:
        seen: List[str] = []
        for o in self.objects:
            if o.key not in seen:
                seen.append(o.key)
        return seen

    def version(self) -> Optional[str]:
        v = self.first("Version")
        return v.get(0) or None if v else None

    def has_class_prefix(self, prefix: str) -> bool:
        p = prefix.upper()
        return any(o.key.startswith(p) for o in self.objects)

    # ----- edits (each returns a new Idf) --------------------------------------
    def _rebuild(self, replacements: List[tuple]) -> "Idf":
        """replacements: list of (start, end, new_text); non-overlapping."""
        text = self.text
        for start, end, new in sorted(replacements, key=lambda r: r[0], reverse=True):
            text = text[:start] + new + text[end:]
        return Idf(text)

    def remove(self, objs: Iterable[IdfObject]) -> "Idf":
        return self._rebuild([(o.start, o.end, "") for o in objs])

    def remove_classes(self, classes: Iterable[str]) -> "Idf":
        keys = {c.upper() for c in classes}
        return self.remove([o for o in self.objects if o.key in keys])

    def replace(self, obj: IdfObject, fields: List[str], cls: Optional[str] = None) -> "Idf":
        new = format_object(cls or obj.cls, fields).rstrip("\n")
        return self._rebuild([(obj.start, obj.end, new)])

    def set_field(self, obj: IdfObject, index: int, value: str) -> "Idf":
        fields = list(obj.fields)
        while len(fields) <= index:
            fields.append("")
        fields[index] = value
        return self.replace(obj, fields)

    def append(self, *texts: str) -> "Idf":
        text = self.text
        if not text.endswith("\n"):
            text += "\n"
        return Idf(text + "\n" + "\n".join(t if t.endswith("\n") else t + "\n" for t in texts))

    def write(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(self.text)
