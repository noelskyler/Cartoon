"""Command line interface.

  plancheck run SOURCE --manifest M [--sheets A1.1,A2.1|all] [--work DIR]
  plancheck render --work DIR --sheet A2.1 [--role set_to_review] [--dpi 300] [--region l,t,r,b] [--out f.png]
  plancheck record --work DIR FINDINGS.json
  plancheck status --work DIR
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path

from . import TOOL_VERSION
from .checkpoint import record, update_after_run
from .common import read_json, sheet_key, write_json
from .index import index_file
from .intake import collect_sources
from .manifest import load_manifest
from .packet import build_packet
from .pdfio import Cache, Docs, Timer, crop_frac, render
from .validate import match_files, validate_index


def _print_issues(items: list[dict], limit: int = 40) -> None:
    for i in items[:limit]:
        print(f"  [{i['level']}] {i['message']}")
    if len(items) > limit:
        print(f"  ... {len(items) - limit} more in intake_report.json")


def cmd_run(a) -> int:
    t0 = time.perf_counter()
    timer = Timer()
    src, mpath = Path(a.source), Path(a.manifest)
    manifest, issues = load_manifest(mpath)
    if issues.count("ERROR"):
        print("Manifest errors:")
        _print_issues(issues.to_list())
        return 2
    proj = manifest["project"]
    work = Path(a.work) if a.work else Path("work") / re.sub(r"[^A-Za-z0-9]+", "_", f"{proj['name']}_lot{proj['lot']}").strip("_").lower()
    work.mkdir(parents=True, exist_ok=True)
    settings = manifest["settings"]
    cache = Cache(work / "cache")
    docs = Docs()
    try:
        found, container, iss = collect_sources(src, work, ignore={mpath})
        issues.extend(iss)
        ignore_rel = set()
        if src.is_dir():
            try:
                ignore_rel.add(mpath.resolve().relative_to(src.resolve()).as_posix().lower())
            except ValueError:
                pass
        sources, iss = match_files(manifest, found, ignore_rel)
        issues.extend(iss)
        for s in sources:
            (cache.hit if s["reused_blob"] else cache.miss)("source_copy")
        timer.lap("intake_hash_copy")

        indexes = {}
        for s in sources:
            if s["sha256"] not in indexes:
                indexes[s["sha256"]] = index_file(s["sha256"], Path(s["blob"]), settings, cache, docs)
        timer.lap("index")
        issues.extend(validate_index(manifest, sources, indexes, cache, settings))
        timer.lap("validate")

        issue_list = issues.to_list()
        review = next((s for s in sources if s["role"] == "set_to_review"), None)
        review_sheets = []
        if review and review["sha256"] in indexes and not indexes[review["sha256"]].get("error"):
            review_sheets = [p["sheet_key"] for p in indexes[review["sha256"]]["pages"] if p["sheet_key"]]
        write_json(work / "state" / "sources.json", {"sources": sources, "container": container, "manifest": str(mpath)})
        write_json(work / "intake_report.json", {"project": proj, "container": container,
                                                 "sources": [{k: v for k, v in s.items() if k not in ("blob", "reused_blob")} for s in sources],
                                                 "issues": issue_list})

        n_err = sum(1 for i in issue_list if i["level"] == "ERROR")
        n_warn = sum(1 for i in issue_list if i["level"] == "WARN")
        print(f"plancheck {TOOL_VERSION}  project={proj['name']} lot={proj['lot']} model={proj['model']} "
              f"elev={proj['elevation']} hand={proj['handedness']} rev={proj['revision']}")
        print(f"Sources: {len(sources)} matched, {len(found)} found in {container.get('kind')}  "
              f"| intake: {n_err} error(s), {n_warn} warning(s)")
        _print_issues([i for i in issue_list if i["level"] != "INFO"])

        packet = None
        if a.sheets:
            if n_err and not a.continue_on_error:
                print("\nNot exporting a review packet because intake has errors. Fix the sources/manifest, "
                      "or re-run with --continue-on-error to export anyway (errors are carried into the packet).")
            elif not review_sheets:
                print("\nNo indexed set_to_review; nothing to export.")
            else:
                sel = ["ALL"] if a.sheets.strip().lower() == "all" else [x.strip() for x in a.sheets.split(",") if x.strip()]
                out = Path(a.packet_out) if a.packet_out else work / "packet"
                packet = build_packet(out=out, manifest=manifest, sources=sources, container=container, indexes=indexes,
                                      issues=issue_list, selected=sel, cache=cache, docs=docs, settings=settings)
                packet["path"] = str(out)
                for pi in packet["packet_issues"]:
                    print(f"  [{pi['level']}] {pi['message']}")
                st = packet["stats"]
                print(f"\nPacket: {out}  sheets={','.join(st['sheets_in_packet'])}  files={st['files']}  "
                      f"images={st['images']}  bytes={st['bytes']:,}  text_chars={st['packet_text_chars']:,} "
                      f"(review set extracted text: {st['review_set_extracted_text_chars']:,} chars)")
        timer.lap("packet")
        cp, note = update_after_run(work, manifest, sources, review_sheets, issue_list, packet, settings)
        if note:
            print(f"\n[WARN] {note}")
        timer.lap("checkpoint")
    finally:
        docs.close()

    total = round(time.perf_counter() - t0, 3)
    report = {"tool_version": TOOL_VERSION, "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
              "runtime_s": total, "phases_s": timer.phases, "cache": cache.summary(),
              "packet": packet["stats"] if packet else None}
    rdir = work / "run_reports"
    write_json(rdir / f"run_{dt.datetime.now():%Y%m%d_%H%M%S_%f}.json", report)
    hits = sum(v["hit"] for v in report["cache"].values())
    misses = sum(v["miss"] for v in report["cache"].values())
    print(f"\nRuntime {total:.2f}s  phases {timer.phases}")
    print(f"Cache: {hits} hit(s), {misses} miss(es)  " + "  ".join(f"{k}={v['hit']}h/{v['miss']}m" for k, v in report["cache"].items()))
    print(f"Checkpoint: {work / 'checkpoint.json'}  not checked: {len(cp['coverage']['not_checked'])} sheet(s)")
    n_err = sum(1 for i in issue_list if i["level"] == "ERROR")
    return 2 if n_err else 0


def cmd_render(a) -> int:
    work = Path(a.work)
    state = read_json(work / "state" / "sources.json")
    src = next((s for s in state["sources"] if s["role"] == a.role), None)
    if not src:
        print(f"No source with role {a.role}.")
        return 1
    cache = Cache(work / "cache")
    idx_files = sorted((cache.root / src["sha256"][:2] / src["sha256"]).glob("index_*.json"),
                       key=lambda p: p.stat().st_mtime)
    if not idx_files:
        print("Not indexed yet; run 'plancheck run' first.")
        return 1
    idx = read_json(idx_files[-1])
    page = next((p for p in idx["pages"] if p["sheet_key"] == sheet_key(a.sheet)), None)
    if not page:
        print(f"Sheet {a.sheet} not found in {src['path']}.")
        return 1
    docs = Docs()
    try:
        img = render(cache, docs, src["sha256"], Path(src["blob"]), page["page"] - 1, a.dpi, color=a.color)
    finally:
        docs.close()
    if a.region:
        img = crop_frac(img, [float(x) for x in a.region.split(",")])
    out = Path(a.out or work / "renders" / f"{a.role}_{sheet_key(a.sheet)}_{a.dpi}dpi{'_crop' if a.region else ''}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"{out}  ({img.size[0]}x{img.size[1]} px) cache={dict(cache.summary())}")
    return 0


def cmd_record(a) -> int:
    errs, cp = record(Path(a.work), Path(a.findings))
    if errs:
        print("Findings not recorded:")
        for e in errs:
            print(f"  - {e}")
        return 1
    cov = cp["coverage"]
    print(f"Recorded. Findings: {len(cp['findings'])}  checked sheets: {', '.join(cov['checked']) or 'none'}  "
          f"not checked: {', '.join(cov['not_checked']) or 'none'}")
    return 0


def cmd_status(a) -> int:
    p = Path(a.work) / "checkpoint.json"
    if not p.exists():
        print("No checkpoint yet.")
        return 1
    cp = read_json(p)
    pr = cp["project"]
    print(f"{pr.get('name')} lot {pr.get('lot')} {pr.get('model')} elev {pr.get('elevation')} {pr.get('handedness')} rev {pr.get('revision')}")
    for s in cp["sources"]:
        print(f"  {s['role']:<20} {s['sha256'][:12]}  rev={s.get('revision') or '?'}  {s['path']}")
    cov = cp["coverage"]
    print(f"Checked: {', '.join(f'{k} ({v['scope']})' for k, v in cov['checked'].items()) or 'none'}")
    print(f"Not checked: {', '.join(cov['not_checked']) or 'none'}")
    print(f"Checklist items not verified: {len(cov['checklist_not_verified'])}")
    reds = sum(1 for f in cp["findings"] if f["type"] == "correction")
    print(f"Findings: {reds} red, {len(cp['findings']) - reds} orange")
    print(f"Unresolved: {len(cp['intake_unresolved'])} intake, {len(cp['reviewer_unresolved'])} reviewer")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="plancheck", description="Drawing-set intake, sheet index and review packets.")
    ap.add_argument("--version", action="version", version=TOOL_VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Validate sources, index sheets, export a review packet.")
    r.add_argument("source", help="Folder or .zip of PDFs (never modified).")
    r.add_argument("--manifest", "-m", required=True, help="Project manifest (.yaml or .json).")
    r.add_argument("--sheets", "-s", help="Comma-separated sheet numbers for the packet, or 'all'. Omit for intake + index only.")
    r.add_argument("--work", "-w", help="Work folder (cache, checkpoint, packet). Default: work/<project>_lot<n>.")
    r.add_argument("--packet-out", help="Packet folder. Default: <work>/packet.")
    r.add_argument("--continue-on-error", action="store_true", help="Export a packet even if intake has errors.")
    r.set_defaults(fn=cmd_run)

    r = sub.add_parser("render", help="Render a sheet (or region) at full resolution.")
    r.add_argument("--work", "-w", required=True)
    r.add_argument("--sheet", required=True)
    r.add_argument("--role", default="set_to_review")
    r.add_argument("--dpi", type=int, default=300)
    r.add_argument("--region", help="left,top,right,bottom as 0-1 fractions of the sheet.")
    r.add_argument("--color", action="store_true", help="Render in colour (e.g. to see red/orange markup).")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_render)

    r = sub.add_parser("record", help="Validate a findings file and merge it into the checkpoint.")
    r.add_argument("--work", "-w", required=True)
    r.add_argument("findings")
    r.set_defaults(fn=cmd_record)

    r = sub.add_parser("status", help="Summarise the checkpoint.")
    r.add_argument("--work", "-w", required=True)
    r.set_defaults(fn=cmd_status)

    a = ap.parse_args(argv)
    return a.fn(a)
