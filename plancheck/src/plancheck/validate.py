"""Source validation: manifest vs files, hashes, roles, title blocks vs manifest, cross-source conflicts.

Every disagreement is flagged. Nothing is substituted or inferred.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from .common import DRAWING_ROLES, SINGLE_ROLES, Issues, norm_value, sheet_key
from .index import load_page_text
from .intake import SourceFile


def match_files(manifest: dict, found: list[SourceFile], ignore_rel: set[str]) -> tuple[list[dict], Issues]:
    """Pair manifest entries with found files by exact relative path (case-insensitive)."""
    issues = Issues()
    by_path = {f.relpath.lower(): f for f in found}
    used = set()
    sources = []
    for entry in manifest["files"]:
        f = by_path.get(entry["path"].lower())
        if f is None:
            same_name = [x.relpath for x in found if x.relpath.rsplit("/", 1)[-1].lower() == entry["path"].rsplit("/", 1)[-1].lower()]
            hint = f" A file with the same name exists at: {', '.join(same_name)}. Fix the manifest path; it was not substituted." if same_name else ""
            issues.add("ERROR", "file_missing", f"{entry['path']} ({entry['role']}) is listed in the manifest but not in the source.{hint}",
                       file=entry["path"])
            continue
        used.add(f.relpath.lower())
        if not f.is_pdf:
            issues.add("ERROR", "not_pdf", f"{f.relpath} is not a PDF.", file=f.relpath)
            continue
        if entry.get("sha256") and entry["sha256"].lower() != f.sha256:
            issues.add("ERROR", "hash_mismatch",
                       f"{f.relpath}: SHA-256 {f.sha256[:12]}... does not match manifest {entry['sha256'][:12]}... — this is a different version than recorded.",
                       file=f.relpath)
        sources.append({**entry, "path": f.relpath, "sha256": f.sha256, "size": f.size, "blob": str(f.blob),
                        "reused_blob": f.reused_blob})
    for f in found:
        if f.relpath.lower() in used or f.relpath.lower() in ignore_rel:
            continue
        if f.is_pdf:
            issues.add("WARN", "undeclared_file", f"{f.relpath} is in the source but not in the manifest; it was not used.", file=f.relpath)
        else:
            issues.add("INFO", "non_pdf_ignored", f"{f.relpath} is not a PDF; ignored.", file=f.relpath)

    roles = Counter(s["role"] for s in sources)
    if roles["set_to_review"] == 0:
        issues.add("ERROR", "role_missing", "No file has role set_to_review.")
    if roles["approved_baseline"] == 0:
        issues.add("WARN", "role_missing", "No approved_baseline: like-for-like backchecks cannot be done.")
    for r in SINGLE_ROLES:
        if roles[r] > 1:
            names = ", ".join(s["path"] for s in sources if s["role"] == r)
            issues.add("ERROR", "role_ambiguous", f"{roles[r]} files have role {r} ({names}). Keep exactly one; the tool will not choose.")
    by_hash = defaultdict(list)
    for s in sources:
        by_hash[s["sha256"]].append(s)
    for h, group in by_hash.items():
        if len(group) > 1:
            issues.add("WARN", "duplicate_content",
                       "Identical bytes under different entries: " + ", ".join(f"{g['path']} ({g['role']})" for g in group))
    return sources, issues


def _check(issues, level, field_name, expected, found, where, **kw):
    e, f = norm_value(field_name, expected), norm_value(field_name, found)
    if e is None or f is None or e == f:
        return
    issues.add(level, f"{field_name}_conflict", f"{where}: title block shows {field_name} '{found}' but manifest says '{expected}'.", **kw)


def validate_index(manifest: dict, sources: list[dict], indexes: dict[str, dict], cache, settings: dict) -> Issues:
    issues = Issues()
    proj = manifest["project"]
    for s in sources:
        idx = indexes.get(s["sha256"])
        if idx is None:
            continue
        path, role = s["path"], s["role"]
        if idx.get("error"):
            issues.add("ERROR", "pdf_unreadable", f"{path}: {idx['error']}", file=path)
            continue
        if role not in DRAWING_ROLES:
            continue
        file_rev = s.get("revision") or (proj.get("revision") if role == "set_to_review" else None)
        if not s.get("revision"):
            issues.add("WARN" if role in ("set_to_review", "approved_baseline", "master_set") else "INFO",
                       "revision_unrecorded", f"{path} ({role}) has no revision recorded in the manifest.", file=path)
        keys = Counter(p["sheet_key"] for p in idx["pages"] if p["sheet_key"])
        for k, n in keys.items():
            if n > 1:
                issues.add("WARN", "duplicate_sheet", f"{path}: sheet {k} appears on {n} pages.", file=path, sheet=k)
        missing = [str(p["page"]) for p in idx["pages"] if not p["sheet_key"]]
        if missing:
            issues.add("WARN", "sheet_number_not_found",
                       f"{path}: no sheet number found in the title block on page(s) {', '.join(missing)}. Check title_block_region.",
                       file=path)
        for p in idx["pages"]:
            f = p["fields"]
            where = f"{path} p{p['page']} ({p['sheet'] or '?'})"
            kw = {"file": path, "sheet": p["sheet"], "page": p["page"]}
            if p["sheet_source"] == "unlabeled":
                issues.add("INFO", "sheet_unlabeled", f"{where}: sheet number read without a 'SHEET' label.", **kw)
            if role == "prior_redlines":
                continue
            _check(issues, "ERROR", "model", proj.get("model"), f.get("model"), where, **kw)
            _check(issues, "WARN", "revision", file_rev, f.get("revision"), where, **kw)
            if role == "set_to_review":
                _check(issues, "ERROR", "lot", proj.get("lot"), f.get("lot"), where, **kw)
                _check(issues, "ERROR", "elevation", proj.get("elevation"), f.get("elevation"), where, **kw)
                _check(issues, "ERROR", "handedness", proj.get("handedness"), f.get("handedness"), where, **kw)
            else:
                for fld in ("elevation", "handedness"):
                    e, v = norm_value(fld, proj.get(fld)), norm_value(fld, f.get(fld))
                    if e and v and e != v:
                        issues.add("WARN", "not_like_for_like",
                                   f"{where}: {role} sheet is {fld} '{f.get(fld)}', project is '{proj.get(fld)}'. Do not backcheck against it without confirming.",
                                   **kw)

    review = next((s for s in sources if s["role"] == "set_to_review"), None)
    if review and review["sha256"] in indexes and not indexes[review["sha256"]].get("error"):
        ridx = indexes[review["sha256"]]
        rkeys = [p["sheet_key"] for p in ridx["pages"] if p["sheet_key"]]
        rset = set(rkeys)
        # sheet sets vs baseline / master
        for role in ("approved_baseline", "master_set"):
            other = next((s for s in sources if s["role"] == role), None)
            if not other or other["sha256"] not in indexes or indexes[other["sha256"]].get("error"):
                continue
            okeys = {p["sheet_key"] for p in indexes[other["sha256"]]["pages"] if p["sheet_key"]}
            if okeys - rset:
                issues.add("WARN", "sheet_set_diff", f"Sheets in {role} but not in set_to_review: {', '.join(sorted(okeys - rset))}.")
            if rset - okeys:
                issues.add("INFO", "sheet_set_diff", f"Sheets in set_to_review but not in {role}: {', '.join(sorted(rset - okeys))}.")
        # cover index vs actual sheets
        entries = []
        for p in ridx["pages"]:
            if len(p["index_entries"]) >= 3:
                entries = [sheet_key(e[0]) for e in p["index_entries"]]
                cover = p
                break
        if entries:
            where = f"cover index on {cover['sheet'] or 'p' + str(cover['page'])}"
            listed_missing = [e for e in entries if e not in rset]
            unlisted = [k for k in rkeys if k not in entries]
            if listed_missing:
                issues.add("WARN", "index_sheet_missing", f"{where} lists sheets not in the set: {', '.join(listed_missing)}.")
            if unlisted:
                issues.add("WARN", "index_sheet_unlisted", f"Sheets in the set but not on the {where}: {', '.join(unlisted)}.")
            common_index = [e for e in entries if e in rset]
            common_set = [k for k in rkeys if k in entries]
            if common_index != common_set:
                issues.add("WARN", "sheet_order", f"Sheet order differs from the {where}: set order {', '.join(common_set)}.")
        else:
            issues.add("INFO", "no_cover_index", "No cover sheet index found in set_to_review; sheet order not checked.")
        # detail references to sheets that do not exist
        for p in ridx["pages"]:
            bad = [r for r in p["detail_refs"] if r not in rset]
            if bad:
                issues.add("WARN", "detail_ref_missing_sheet",
                           f"{p['sheet']}: references sheet(s) not in the set: {', '.join(bad)}.", sheet=p["sheet"], page=p["page"])

    # lot diagram and buyer selections cross-checks
    from .index import DEFAULT_FIELD_PATTERNS, parse_fields

    patterns = dict(DEFAULT_FIELD_PATTERNS)
    patterns.update(settings.get("field_patterns") or {})
    for s in sources:
        if s["role"] not in ("lot_diagram", "buyer_selections") or s["sha256"] not in indexes:
            continue
        idx = indexes[s["sha256"]]
        if idx.get("error"):
            continue
        text = "\n".join(load_page_text(cache, s["sha256"], p["page"]) for p in idx["pages"])
        f = parse_fields(text, patterns)
        for fld in ("lot", "model", "elevation", "handedness"):
            _check(issues, "ERROR", fld, proj.get(fld), f.get(fld), s["path"], file=s["path"])
        if s["role"] == "buyer_selections":
            rx = re.compile(settings["buyer_option_pattern"], re.I | re.M)
            in_doc = {m.group(1).strip().upper() for m in rx.finditer(text)}
            in_manifest = {o["name"].strip().upper() for o in manifest["buyer_options"]}
            if not in_doc:
                issues.add("INFO", "buyer_options_unparsed",
                           f"{s['path']}: no option lines matched buyer_option_pattern; options not cross-checked.", file=s["path"])
            else:
                for o in sorted(in_manifest - in_doc):
                    issues.add("WARN", "buyer_option_conflict", f"Manifest option '{o}' is not on {s['path']}.", file=s["path"])
                for o in sorted(in_doc - in_manifest):
                    issues.add("WARN", "buyer_option_conflict", f"{s['path']} lists option '{o}' that is not in the manifest.", file=s["path"])
    return issues
