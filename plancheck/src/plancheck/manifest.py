"""Load and structurally validate the project manifest (YAML or JSON)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import ROLES, Issues

PROJECT_FIELDS = ("name", "lot", "model", "elevation", "handedness", "revision")

DEFAULT_SETTINGS: dict[str, Any] = {
    # Title block region as fractions of the *visible* sheet: [left, top, right, bottom]
    "title_block_region": [0.70, 0.66, 1.0, 1.0],
    "overview_dpi": 60,         # rendered overview images
    "overview_max_px": 1600,    # long edge cap for overview images in packets
    "diff_dpi": 72,             # rendering used for image comparison
    "crop_dpi": 150,            # detail crops
    "diff_threshold": 48,       # 0-255 grey difference counted as a change
    "diff_grid": 24,            # grid cells across the long edge for change clustering
    "max_crops_per_sheet": 6,
    "max_text_chars": 6000,     # sheet text cap in packets
    "field_patterns": {},       # overrides for title-block regexes (see index.py)
    "buyer_option_pattern": r"^\s*OPTION\s*:\s*(.+?)\s*$",
}


def load_manifest(path: Path) -> tuple[dict, Issues]:
    issues = Issues()
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        issues.add("ERROR", "manifest_invalid", "Manifest must be a mapping at the top level.")
        return {}, issues

    proj = data.get("project") or {}
    if not isinstance(proj, dict):
        issues.add("ERROR", "manifest_invalid", "'project' must be a mapping.")
        proj = {}
    for f in PROJECT_FIELDS:
        if proj.get(f) in (None, ""):
            issues.add("ERROR", "project_field_missing",
                       f"project.{f} is not set. Record it explicitly; the tool will not infer it.")
    if proj.get("community") in (None, ""):
        issues.add("WARN", "project_field_missing", "project.community is not set; community rules cannot be scoped.")
    for k in list(proj):
        if proj[k] is not None and not isinstance(proj[k], (dict, list)):
            proj[k] = str(proj[k])

    files = data.get("files") or []
    if not isinstance(files, list) or not files:
        issues.add("ERROR", "manifest_no_files", "Manifest 'files' list is empty.")
        files = []
    clean_files = []
    seen_paths = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not f.get("path"):
            issues.add("ERROR", "manifest_file_invalid", f"files[{i}] needs a 'path'.")
            continue
        p = str(f["path"]).replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        role = f.get("role")
        if role not in ROLES:
            issues.add("ERROR", "manifest_role_invalid",
                       f"files[{i}] ({p}) role '{role}' is not one of: {', '.join(ROLES)}.", file=p)
            continue
        if p.lower() in seen_paths:
            issues.add("ERROR", "manifest_duplicate_path", f"{p} is listed more than once.", file=p)
            continue
        seen_paths.add(p.lower())
        entry = {"path": p, "role": role}
        for k in ("revision", "date", "sha256", "label", "notes"):
            if f.get(k) not in (None, ""):
                entry[k] = str(f[k])
        clean_files.append(entry)

    opts = []
    for i, o in enumerate(data.get("buyer_options") or []):
        if isinstance(o, str):
            o = {"name": o}
        if not isinstance(o, dict) or not o.get("name"):
            issues.add("ERROR", "manifest_option_invalid", f"buyer_options[{i}] needs a 'name'.")
            continue
        opts.append({
            "name": str(o["name"]),
            "keywords": [str(k) for k in (o.get("keywords") or [o["name"]])],
            "expect_on": [str(s) for s in (o.get("expect_on") or [])],
        })

    redlines = []
    for i, r in enumerate(data.get("prior_redlines") or []):
        if not isinstance(r, dict) or not r.get("sheet") or not r.get("note"):
            issues.add("ERROR", "manifest_redline_invalid", f"prior_redlines[{i}] needs 'sheet' and 'note'.")
            continue
        redlines.append({"sheet": str(r["sheet"]), "note": str(r["note"]),
                         "color": str(r.get("color", "red")), "source": str(r.get("source", "manifest"))})

    rules_in = data.get("rules") or {}
    rules = {"project": [str(x) for x in rules_in.get("project") or []],
             "community": [str(x) for x in rules_in.get("community") or []]}

    settings = dict(DEFAULT_SETTINGS)
    for k, v in (data.get("settings") or {}).items():
        if k not in DEFAULT_SETTINGS:
            issues.add("WARN", "manifest_unknown_setting", f"Unknown setting '{k}' ignored.")
            continue
        settings[k] = v

    return {
        "project": proj,
        "files": clean_files,
        "buyer_options": opts,
        "prior_redlines": redlines,
        "rules": rules,
        "settings": settings,
        "manifest_path": str(path),
    }, issues
