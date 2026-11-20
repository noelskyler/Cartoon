"""Checkpoint: exact sources, pages checked, findings, unresolved items. Lets another reviewer resume."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from . import TOOL_VERSION
from .common import (BUYER_OPTION_STATUSES, FINDING_TYPES, REDLINE_STATUSES, RULE_SCOPES, read_json, sheet_key,
                     write_json)

RULE_STATUSES = ("Checked", "Finding Raised", "Not Applicable", "Not Verified")
LABEL_RE = re.compile(r"^\s*\(?[RO]\d{1,3}\)?\s*[:.)\-]", re.I)
MAX_NOTE = 200


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def fingerprint(sources: list[dict]) -> str:
    rows = sorted((s["role"], s["path"], s["sha256"]) for s in sources)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def update_after_run(work: Path, manifest: dict, sources: list[dict], review_sheets: list[str],
                     issues: list[dict], packet: dict | None, settings: dict) -> tuple[dict, str | None]:
    path = work / "checkpoint.json"
    fp = fingerprint(sources)
    note = None
    cp = read_json(path) if path.exists() else None
    if cp and cp.get("source_fingerprint") != fp:
        archived = work / f"checkpoint_superseded_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
        path.rename(archived)
        note = (f"Sources changed since the last checkpoint. Previous checkpoint archived as {archived.name}; "
                "its checks do NOT carry over.")
        cp = None
    if cp is None:
        cp = {"created": _now(), "packets": [], "findings": [], "checklist_status": {}, "reviewer_unresolved": [],
              "checked": {}}
    cp.update({
        "tool_version": TOOL_VERSION,
        "updated": _now(),
        "project": manifest["project"],
        "source_fingerprint": fp,
        "sources": [{"role": s["role"], "path": s["path"], "sha256": s["sha256"], "revision": s.get("revision"),
                     "date": s.get("date")} for s in sources],
        "settings": settings,
        "review_sheets": review_sheets,
        "intake_unresolved": [i for i in issues if i["level"] in ("ERROR", "WARN")],
    })
    if packet:
        cp["packets"].append({"created": _now(), "sheets": packet["sheets"], "path": packet["path"]})
        items = cp.setdefault("checklist_items", {})
        for kind in ("buyer_options", "prior_redlines", "rules"):
            for it in packet["checklists"][kind]:
                items[it["id"]] = {"kind": kind, **{k: v for k, v in it.items() if k not in ("id", "status")}}
                cp["checklist_status"].setdefault(it["id"], {"status": "Not Verified"})
    cp["coverage"] = coverage(cp)
    write_json(path, cp)
    return cp, note


def coverage(cp: dict) -> dict:
    checked = cp.get("checked", {})
    return {
        "checked": {k: checked[k] for k in cp.get("review_sheets", []) if k in checked},
        "not_checked": [k for k in cp.get("review_sheets", []) if k not in checked],
        "checklist_not_verified": sorted(k for k, v in cp.get("checklist_status", {}).items() if v["status"] == "Not Verified"),
        "_note": "Nothing is ever marked passed. 'checked' only means a reviewer recorded that scope as reviewed.",
    }


def validate_findings(data: dict, cp: dict) -> list[str]:
    errs: list[str] = []
    sheets = set(cp.get("review_sheets", []))
    roles = {s["role"] for s in cp.get("sources", [])}
    items = cp.get("checklist_items", {})
    for i, c in enumerate(data.get("checked", [])):
        k = sheet_key(c.get("sheet"))
        if k not in sheets:
            errs.append(f"checked[{i}]: sheet '{c.get('sheet')}' is not in the set to review.")
        if not str(c.get("scope", "")).strip():
            errs.append(f"checked[{i}] ({c.get('sheet')}): describe the scope you checked, or remove the entry.")
    seen = set()
    for i, f in enumerate(data.get("findings", [])):
        w = f"findings[{i}]"
        if f.get("type") not in FINDING_TYPES:
            errs.append(f"{w}: type must be 'correction' (red) or 'question' (orange).")
        if f.get("scope", "general") not in RULE_SCOPES:
            errs.append(f"{w}: scope must be one of {', '.join(RULE_SCOPES)}.")
        note = str(f.get("note", "")).strip()
        if not note:
            errs.append(f"{w}: note is empty.")
        if len(note) > MAX_NOTE:
            errs.append(f"{w}: note is {len(note)} chars; keep it short and actionable (<= {MAX_NOTE}).")
        if LABEL_RE.match(note):
            errs.append(f"{w}: remove the R#/O# label from the note.")
        if sheet_key(f.get("target_sheet")) not in sheets:
            errs.append(f"{w}: target_sheet '{f.get('target_sheet')}' is not in the set to review.")
        locs = f.get("locations") or []
        if not locs:
            errs.append(f"{w}: at least one location is required.")
        for loc in locs:
            if sheet_key(loc.get("sheet")) not in sheets:
                errs.append(f"{w}: location sheet '{loc.get('sheet')}' is not in the set to review.")
            r = loc.get("region")
            if r is not None and not (isinstance(r, list) and len(r) == 4 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in r)
                                      and r[0] < r[2] and r[1] < r[3]):
                errs.append(f"{w}: region must be [left, top, right, bottom] fractions 0-1.")
        srcs = f.get("sources") or []
        if not srcs:
            errs.append(f"{w}: cite at least one supporting source (role + sheet).")
        for s in srcs:
            if s.get("role") not in roles:
                errs.append(f"{w}: source role '{s.get('role')}' is not in this project's sources.")
        key = (note.upper(), sheet_key(f.get("target_sheet")))
        if key in seen:
            errs.append(f"{w}: duplicate note on the same sheet; write it once and add locations instead.")
        seen.add(key)
    for kind, allowed in (("buyer_options", BUYER_OPTION_STATUSES), ("prior_redlines", REDLINE_STATUSES),
                          ("rules", RULE_STATUSES)):
        for i, it in enumerate(data.get(kind, [])):
            if it.get("id") not in items:
                errs.append(f"{kind}[{i}]: unknown id '{it.get('id')}'.")
            elif items[it["id"]]["kind"] != kind:
                errs.append(f"{kind}[{i}]: id '{it['id']}' belongs to {items[it['id']]['kind']}.")
            if it.get("status") not in allowed:
                errs.append(f"{kind}[{i}]: status '{it.get('status')}' must be one of {', '.join(allowed)}.")
            elif it["status"] != "Not Verified" and not str(it.get("evidence", "")).strip():
                errs.append(f"{kind}[{i}]: status '{it['status']}' needs evidence (sheet + source).")
    return errs


def record(work: Path, findings_path: Path) -> tuple[list[str], dict | None]:
    cp_path = work / "checkpoint.json"
    if not cp_path.exists():
        return ["No checkpoint.json in the work folder. Run 'plancheck run' first."], None
    cp = read_json(cp_path)
    data = read_json(findings_path)
    errs = validate_findings(data, cp)
    if errs:
        return errs, None
    who = str(data.get("reviewer") or "unnamed reviewer")
    stamp = _now()
    for c in data.get("checked", []):
        cp["checked"][sheet_key(c["sheet"])] = {"scope": c["scope"], "by": who, "at": stamp}
    for f in data.get("findings", []):
        f = dict(f)
        f["color"] = FINDING_TYPES[f["type"]]
        f["target_sheet"] = sheet_key(f["target_sheet"])
        f.update(by=who, at=stamp)
        key = (f["note"].strip().upper(), f["target_sheet"])
        existing = next((x for x in cp["findings"] if (x["note"].strip().upper(), x["target_sheet"]) == key), None)
        if existing:
            have = {json.dumps(l, sort_keys=True) for l in existing["locations"]}
            existing["locations"] += [l for l in f["locations"] if json.dumps(l, sort_keys=True) not in have]
        else:
            cp["findings"].append(f)
    for kind in ("buyer_options", "prior_redlines", "rules"):
        for it in data.get(kind, []):
            if it["status"] != "Not Verified" or it["id"] not in cp["checklist_status"]:
                cp["checklist_status"][it["id"]] = {"status": it["status"], "evidence": it.get("evidence", ""),
                                                    "by": who, "at": stamp}
    for u in data.get("unresolved", []):
        if u not in cp["reviewer_unresolved"]:
            cp["reviewer_unresolved"].append(u)
    cp["updated"] = stamp
    cp["coverage"] = coverage(cp)
    write_json(cp_path, cp)
    return [], cp
