"""Sheet indexing: per-page text, title-block fields, sheet numbers, references. Cached by file hash + settings."""
from __future__ import annotations

import re
from pathlib import Path

from .common import read_json, settings_key, sheet_key, write_json
from .pdfio import Cache, Docs, page_geometry, page_text, read_annotations, region_text

SHEET_RE = r"[A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?"

DEFAULT_FIELD_PATTERNS = {
    "sheet": rf"\bSHEET(?:\s*(?:NO\.?|NUMBER|#))?\s*[:#]?\s*({SHEET_RE})\b",
    "title": r"\b(?:SHEET\s+)?TITLE\s*:\s*(.+?)\s*$",
    "project": r"\bPROJECT\s*:\s*(.+?)\s*$",
    "community": r"\bCOMMUNITY\s*:\s*(.+?)\s*$",
    "model": r"\bMODEL\s*:\s*(.+?)\s*$",
    "lot": r"\bLOT\s*(?:NO\.?|#)?\s*:\s*(\d+[A-Z]?)\b",
    "elevation": r"\bELEV(?:ATION)?\s*:\s*['\"]?([A-Z0-9]{1,3})['\"]?(?=\s|$)",
    "handedness": r"\bHAND(?:ING|EDNESS)?\s*:\s*(LEFT|RIGHT|LH|RH|L|R)\b",
    "revision": r"\bREV(?:ISION)?\s*(?:NO\.?|#)?\s*:\s*([A-Z0-9]{1,4})\b",
    "date": r"\bDATE\s*:\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})",
}
UNLABELED_SHEET = re.compile(rf"^\s*({SHEET_RE})\s*$", re.I)
DETAIL_REF = re.compile(rf"\b(\d{{1,2}})\s*/\s*({SHEET_RE})\b")
INDEX_LINE = re.compile(rf"^\s*({SHEET_RE})\s{{1,}}([A-Z].{{2,}})$")

INDEX_SETTINGS = ("title_block_region", "field_patterns")


def parse_fields(tb_text: str, patterns: dict) -> dict:
    fields: dict[str, str] = {}
    lines = [ln.strip() for ln in tb_text.splitlines() if ln.strip()]
    for name, pat in patterns.items():
        rx = re.compile(pat, re.I)
        for ln in lines:
            m = rx.search(ln)
            if m:
                fields[name] = m.group(1).strip()
                break
    return fields


def index_file(sha: str, blob: Path, settings: dict, cache: Cache, docs: Docs) -> dict:
    sub = {k: settings[k] for k in INDEX_SETTINGS} | {"index_schema": 2}
    key = settings_key(sub)
    out_path = cache.path(sha, f"index_{key}.json")
    if out_path.exists():
        cache.hit("index")
        return read_json(out_path)
    cache.miss("index")

    patterns = dict(DEFAULT_FIELD_PATTERNS)
    patterns.update(settings.get("field_patterns") or {})
    result: dict = {"sha256": sha, "settings_key": key, "pages": [], "annotations": [], "error": None}
    try:
        doc = docs.get(sha, blob)
    except Exception as e:  # corrupt / password protected
        result["error"] = f"Cannot open PDF: {e}"
        write_json(out_path, result)
        return result

    result["page_count"] = len(doc)
    for i in range(len(doc)):
        page = doc[i]
        g = page_geometry(page)
        text = page_text(page)
        cache.path(sha, "text", f"p{i + 1:04d}.txt").write_text(text, encoding="utf-8")
        tb = region_text(page, settings["title_block_region"])
        fields = parse_fields(tb, patterns)
        sheet_source = "label" if fields.get("sheet") else "none"
        if not fields.get("sheet"):
            for ln in tb.splitlines():
                m = UNLABELED_SHEET.match(ln)
                if m:
                    fields["sheet"] = m.group(1).upper()
                    sheet_source = "unlabeled"
                    break
        tb_lines = {ln.strip() for ln in tb.splitlines() if ln.strip()}
        body_lines = [ln.strip() for ln in text.splitlines() if ln.strip() and ln.strip() not in tb_lines]
        refs = sorted({sheet_key(m.group(2)) for ln in body_lines for m in DETAIL_REF.finditer(ln)})
        idx_entries = []
        for ln in body_lines:
            m = INDEX_LINE.match(ln)
            if m and not DETAIL_REF.search(ln):
                idx_entries.append([m.group(1).upper(), m.group(2).strip()])
        result["pages"].append({
            "page": i + 1,
            "size_in": [round(g["vis_w"] / 72, 2), round(g["vis_h"] / 72, 2)],
            "rotation": g["rotation"],
            "sheet": fields.get("sheet"),
            "sheet_key": sheet_key(fields.get("sheet")),
            "sheet_source": sheet_source,
            "fields": fields,
            "title_block_text": tb.strip(),
            "text_chars": len(text),
            "detail_refs": refs,
            "index_entries": idx_entries,
        })
    try:
        result["annotations"] = read_annotations(blob, docs, sha)
    except Exception as e:
        result["annotations_error"] = str(e)
    write_json(out_path, result)
    return result


def load_page_text(cache: Cache, sha: str, page: int) -> str:
    p = cache.path(sha, "text", f"p{page:04d}.txt")
    return p.read_text(encoding="utf-8") if p.exists() else ""
