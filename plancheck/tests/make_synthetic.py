"""Generate the synthetic sample project (deterministic bytes).

    python tests/make_synthetic.py            -> samples/synthetic_project.zip
    python tests/make_synthetic.py --folder D -> same files as a folder

All content is invented. Deliberate problems are built in so intake has something to flag:
  * A2.1 in the review set shows ELEV: C, the manifest says B           (conflict)
  * A3.1 in the review set still shows REV: 2, the file is Rev 3        (revision disagreement)
  * cover index lists A4.1, which is not in the review set              (index / sheet-set mismatch)
  * A1.1 references detail 5/A5.2, which does not exist                 (bad reference)
  * buyer selections list 3-CAR GARAGE; manifest lists COVERED PATIO    (option conflicts)
  * old/Ashford_Rev1.pdf is in the ZIP but not in the manifest          (undeclared file)
  * A3.1 pages are stored with /Rotate 90                               (rotation handling)
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from minipdf import Page, write_pdf  # noqa: E402

W, H = 2592, 1728  # ARCH D landscape, points


def title_block(p: Page, sheet, title, rev, date, lot="12", elev="B", hand="LEFT", model="ASHFORD"):
    x0, y0 = 1850, 36
    p.rect(x0, y0, W - 36 - x0, 540, lw=2)
    rows = [
        "PROJECT: SYNTHETIC MEADOWS",
        "COMMUNITY: EXAMPLE RIDGE",
        f"MODEL: {model}",
    ]
    if lot:
        rows.append(f"LOT: {lot}")
    rows += [f"ELEV: {elev}", f"HAND: {hand}", f"TITLE: {title}", f"REV: {rev}", f"DATE: {date}", f"SHEET: {sheet}"]
    y = 540
    for r in rows:
        p.text(x0 + 20, y, r, 16 if not r.startswith("SHEET") else 28)
        y -= 46 if r.startswith("DATE") else 40
    p.line(x0, 120, W - 36, 120)


def border(p: Page):
    p.rect(36, 36, W - 72, H - 72, lw=3)


def cover(p: Page, sheets):
    p.text(120, 1550, "ASHFORD - CONSTRUCTION DRAWINGS", 40)
    y = 1400
    p.text(120, y + 50, "SHEET INDEX", 24)
    for num, name in sheets:
        p.text(120, y, f"{num}  {name}", 20)
        y -= 40


def floor_plan(p: Page, *, morning_room=False, pantry_clear=False, garage_window_wide=False):
    p.rect(300, 400, 1200, 900, lw=4)               # house outline
    p.rect(300, 400, 500, 450, lw=2); p.text(450, 620, "GARAGE", 20)
    p.rect(800, 400, 700, 450, lw=2); p.text(1050, 620, "KITCHEN", 20)
    p.rect(300, 850, 1200, 450, lw=2); p.text(800, 1080, "FAMILY ROOM", 20)
    p.rect(1350, 700, 150, 150, lw=2); p.text(1365, 770, "PANTRY", 14)
    p.line(1350, 720, 1350, 760, lw=6)              # pantry door
    gw = 160 if garage_window_wide else 100
    p.rect(320, 400 - 6, gw, 12, lw=2)              # garage window
    if pantry_clear:
        p.text(1180, 735, "36\" MIN. CLEAR", 12)
        p.line(1290, 740, 1348, 740, lw=1)
    if morning_room:
        p.rect(1500, 850, 400, 450, lw=4); p.text(1580, 1080, "MORNING ROOM", 20)
    p.text(320, 300, "3/A3.1", 14); p.rect(312, 290, 70, 30)
    p.text(1200, 300, "5/A5.2", 14); p.rect(1192, 290, 70, 30)


def elevation(p: Page, *, stone=True):
    p.line(200, 400, 1700, 400, lw=4)                # grade
    p.rect(300, 400, 1300, 600, lw=3)                # wall
    p.line(300, 1000, 950, 1400, lw=3); p.line(950, 1400, 1600, 1000, lw=3)  # gable
    for x in (400, 700, 1100, 1400):
        p.rect(x, 650, 120, 200, lw=2)
    if stone:
        p.line(300, 470, 1600, 470, lw=2)
        p.text(1650, 470, "STONE WATER TABLE 24\" A.F.F.", 14)
    p.text(950, 1450, "8:12", 16)


def sections(p: Page):
    p.rect(400, 400, 1000, 700, lw=3)
    p.line(400, 1100, 900, 1400, lw=3); p.line(900, 1400, 1400, 1100, lw=3)
    p.text(820, 300, "SECTION 3", 24)


def drawing_set(kind: str) -> list[Page]:
    """kind: review (Rev 3, lot 12) | baseline (Rev 2) | master (Rev 4) | rev1"""
    cfg = {
        "review": dict(rev="3", date="2026-10-01", lot="12"),
        "baseline": dict(rev="2", date="2026-03-15", lot=None),
        "master": dict(rev="4", date="2026-11-02", lot=None),
        "rev1": dict(rev="1", date="2025-11-20", lot=None),
    }[kind]
    index = [("A0.0", "COVER SHEET"), ("A1.1", "FIRST FLOOR PLAN"), ("A2.1", "FRONT ELEVATION"),
             ("A3.1", "BUILDING SECTIONS"), ("A4.1", "ROOF PLAN")]
    pages = []
    p = Page(); border(p); cover(p, index); title_block(p, "A0.0", "COVER SHEET", cfg["rev"], cfg["date"], cfg["lot"]); pages.append(p)
    p = Page(); border(p)
    floor_plan(p, morning_room=kind == "review", pantry_clear=kind == "review", garage_window_wide=kind == "master")
    title_block(p, "A1.1", "FIRST FLOOR PLAN", cfg["rev"], cfg["date"], cfg["lot"]); pages.append(p)
    p = Page(); border(p); elevation(p)
    title_block(p, "A2.1", "FRONT ELEVATION", cfg["rev"], cfg["date"], cfg["lot"], elev="C" if kind == "review" else "B"); pages.append(p)
    p = Page(rotate=90); border(p); sections(p)
    title_block(p, "A3.1", "BUILDING SECTIONS", "2" if kind == "review" else cfg["rev"], cfg["date"], cfg["lot"]); pages.append(p)
    if kind != "review":
        p = Page(); border(p); p.rect(400, 400, 1200, 800, lw=3); p.line(400, 400, 1600, 1200); p.line(400, 1200, 1600, 400)
        title_block(p, "A4.1", "ROOF PLAN", cfg["rev"], cfg["date"], cfg["lot"]); pages.append(p)
    return pages


def prior_redlines() -> list[Page]:
    p = Page(); border(p); floor_plan(p)
    title_block(p, "A1.1", "FIRST FLOOR PLAN", "2", "2026-03-15", None)
    p.annot(1170, 690, 200, 100, "ADD 36\" MIN. CLEAR AT PANTRY DOOR", color=(1, 0, 0))
    p.annot(290, 360, 160, 70, "CONFIRM HEADER HEIGHT AT GARAGE WINDOW W/ STRUCTURAL", color=(1, 0.5, 0))
    return [p]


def letter(lines: list[str]) -> list[Page]:
    p = Page(612, 792)
    y = 740
    for ln in lines:
        p.text(54, y, ln, 12)
        y -= 22
    return [p]


def build(dest: Path) -> dict[str, list[Page]]:
    files = {
        "review/Ashford_Lot12_Rev3.pdf": drawing_set("review"),
        "baseline/Ashford_Approved_Rev2.pdf": drawing_set("baseline"),
        "master/Ashford_Master_Rev4.pdf": drawing_set("master"),
        "redlines/Ashford_Rev2_Redlines.pdf": prior_redlines(),
        "old/Ashford_Rev1.pdf": drawing_set("rev1"),
        "docs/Example_Ridge_Checklist.pdf": letter([
            "EXAMPLE RIDGE ARCHITECTURAL CHECKLIST",
            "1. Front elevation shows stone water table at 24\" min. above finished floor.",
            "2. Garage doors are carriage style only.",
            "3. Front gable roof pitch 8:12 minimum.",
        ]),
        "docs/Lot12_Buyer_Selections.pdf": letter([
            "BUYER SELECTIONS", "LOT: 12", "MODEL: ASHFORD", "ELEVATION: B", "HAND: LEFT",
            "OPTION: MORNING ROOM", "OPTION: 3-CAR GARAGE",
        ]),
        "docs/Lot12_Plot_Diagram.pdf": letter(["LOT DIAGRAM", "LOT: 12", "MODEL: ASHFORD", "ELEV: B", "HAND: LEFT",
                                               "FRONT SETBACK 25'-0\""]),
    }
    for rel, pages in files.items():
        write_pdf(dest / rel, pages)
    (dest / "docs" / "notes.txt").write_text("Non-PDF file: should be reported and ignored.\n")
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder")
    ap.add_argument("--zip", default=str(Path(__file__).resolve().parents[1] / "samples" / "synthetic_project.zip"))
    a = ap.parse_args()
    if a.folder:
        build(Path(a.folder))
        print(f"wrote {a.folder}")
        return
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        build(Path(td))
        zp = Path(a.zip)
        zp.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(Path(td).rglob("*")):
                if f.is_file():
                    zi = zipfile.ZipInfo(f.relative_to(td).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(zi, f.read_bytes())
    print(f"wrote {zp}")


if __name__ == "__main__":
    main()
