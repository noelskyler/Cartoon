"""Shared helpers: issues, hashing, JSON IO, normalisation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROLES = (
    "set_to_review",
    "approved_baseline",
    "master_set",
    "community_checklist",
    "buyer_selections",
    "lot_diagram",
    "prior_redlines",
)
# Roles where more than one file would be ambiguous.
SINGLE_ROLES = ("set_to_review", "approved_baseline", "master_set", "buyer_selections", "lot_diagram")
# Roles whose pages are drawing sheets with title blocks.
DRAWING_ROLES = ("set_to_review", "approved_baseline", "master_set", "prior_redlines")

BUYER_OPTION_STATUSES = ("Shown", "Missing", "Conflicting", "Not Verified")
REDLINE_STATUSES = ("Picked Up", "Missed", "Changed Beyond Scope", "Not Verified")
FINDING_TYPES = {"correction": "red", "question": "orange"}
RULE_SCOPES = ("general", "project", "community")


@dataclass
class Issue:
    level: str  # ERROR | WARN | INFO
    code: str
    message: str
    file: str | None = None
    sheet: str | None = None
    page: int | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Issues:
    items: list[Issue] = field(default_factory=list)

    def add(self, level: str, code: str, message: str, **kw: Any) -> None:
        self.items.append(Issue(level, code, message, **kw))

    def extend(self, other: "Issues") -> None:
        self.items.extend(other.items)

    def count(self, level: str) -> int:
        return sum(1 for i in self.items if i.level == level)

    def to_list(self) -> list[dict]:
        order = {"ERROR": 0, "WARN": 1, "INFO": 2}
        return [i.to_dict() for i in sorted(self.items, key=lambda i: order.get(i.level, 9))]


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def settings_key(settings: dict) -> str:
    from . import TOOL_VERSION

    blob = json.dumps({"v": TOOL_VERSION, "s": settings}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:12]


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


_SHEET_PREFIX = re.compile(r"^([A-Z]+)[-.\s]?(?=\d)")


def sheet_key(s: str | None) -> str | None:
    """Normalise a sheet number for matching: 'a-2.1' -> 'A2.1'."""
    if not s:
        return None
    s = re.sub(r"\s+", "", s.upper())
    return _SHEET_PREFIX.sub(r"\1", s)


def norm_value(field_name: str, v: Any) -> str | None:
    """Normalise a title-block / manifest value for comparison."""
    if v is None:
        return None
    s = str(v).strip().upper().strip("'\"")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    if field_name == "handedness":
        s = {"L": "LEFT", "R": "RIGHT", "LH": "LEFT", "RH": "RIGHT"}.get(s, s)
    if field_name in ("revision", "lot"):
        s = re.sub(r"^(?:REV(?:ISION)?|LOT)\s*", "", s)
        if s.isdigit():
            s = str(int(s))
    return s
