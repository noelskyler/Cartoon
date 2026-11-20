"""End-to-end and unit tests on synthetic data. Run:  python -m unittest discover -s tests -v"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import make_synthetic  # noqa: E402
from plancheck.cli import main  # noqa: E402
from plancheck.common import read_json, sheet_key, norm_value  # noqa: E402
from plancheck.pdfio import user_to_visual_frac, visual_frac_to_user  # noqa: E402

MANIFEST = ROOT / "samples" / "manifest.yaml"


def run(*args) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main([str(a) for a in args])
    return code, buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="plancheck_test_"))
        self.src = self.tmp / "src"
        make_synthetic.build(self.src)
        self.work = self.tmp / "work"

    def tearDown(self):
        for p in self.tmp.rglob("*"):
            with contextlib.suppress(Exception):
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def manifest(self, mutate=None) -> Path:
        import yaml

        data = yaml.safe_load(MANIFEST.read_text())
        if mutate:
            mutate(data)
        p = self.tmp / "manifest.yaml"
        p.write_text(yaml.safe_dump(data))
        return p

    def issues(self) -> list[dict]:
        return read_json(self.work / "intake_report.json")["issues"]

    def codes(self) -> set[str]:
        return {i["code"] for i in self.issues()}


class TestIntake(Base):
    def test_flags_planted_problems_and_blocks_packet(self):
        code, out = run("run", self.src, "-m", MANIFEST, "-s", "A1.1", "-w", self.work)
        self.assertEqual(code, 2)
        self.assertIn("Not exporting a review packet", out)
        self.assertFalse((self.work / "packet").exists())
        msgs = " | ".join(i["message"] for i in self.issues())
        for expected in ["elevation 'C' but manifest says 'B'", "revision '2' but manifest says '3'",
                         "old/Ashford_Rev1.pdf is in the source but not in the manifest",
                         "lists sheets not in the set: A4.1", "A5.2", "'COVERED PATIO'", "'3-CAR GARAGE'"]:
            self.assertIn(expected, msgs)
        self.assertIn("non_pdf_ignored", self.codes())

    def test_originals_untouched_and_copies_read_only(self):
        before = {p: p.read_bytes() for p in self.src.rglob("*") if p.is_file()}
        run("run", self.src, "-m", MANIFEST, "-s", "A1.1", "-w", self.work, "--continue-on-error")
        after = {p: p.read_bytes() for p in self.src.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        for blob in (self.work / "sources").glob("*.pdf"):
            self.assertFalse(blob.stat().st_mode & stat.S_IWUSR)

    def test_missing_file_is_not_substituted(self):
        shutil.move(self.src / "baseline" / "Ashford_Approved_Rev2.pdf", self.src / "Ashford_Approved_Rev2.pdf")
        run("run", self.src, "-m", MANIFEST, "-w", self.work)
        miss = [i for i in self.issues() if i["code"] == "file_missing"]
        self.assertEqual(len(miss), 1)
        self.assertIn("not substituted", miss[0]["message"])
        self.assertIn("role_missing", self.codes())  # no approved_baseline

    def test_hash_mismatch(self):
        def m(d):
            d["files"][0]["sha256"] = "0" * 64
        run("run", self.src, "-m", self.manifest(m), "-w", self.work)
        self.assertIn("hash_mismatch", self.codes())

    def test_ambiguous_role(self):
        def m(d):
            d["files"].append({"path": "old/Ashford_Rev1.pdf", "role": "approved_baseline"})
        run("run", self.src, "-m", self.manifest(m), "-w", self.work)
        self.assertIn("role_ambiguous", self.codes())

    def test_missing_project_field_stops(self):
        def m(d):
            del d["project"]["handedness"]
        code, out = run("run", self.src, "-m", self.manifest(m), "-w", self.work)
        self.assertEqual(code, 2)
        self.assertIn("handedness", out)

    def test_work_inside_source_rejected(self):
        code, _ = run("run", self.src, "-m", MANIFEST, "-w", self.src / "work")
        self.assertIn("work_inside_source", {i["code"] for i in read_json(self.src / "work" / "intake_report.json")["issues"]})

    def test_zip_slip_rejected(self):
        zp = self.tmp / "evil.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("../escape.pdf", b"%PDF-1.4 nope")
            for f in self.src.rglob("*.pdf"):
                zf.write(f, f.relative_to(self.src).as_posix())
        run("run", zp, "-m", MANIFEST, "-w", self.work)
        self.assertIn("zip_unsafe_path", self.codes())
        self.assertFalse((self.tmp / "escape.pdf").exists())

    def test_corrupt_pdf_flagged(self):
        os.chmod(self.src / "docs" / "Lot12_Plot_Diagram.pdf", stat.S_IWRITE | stat.S_IREAD)
        (self.src / "docs" / "Lot12_Plot_Diagram.pdf").write_bytes(b"%PDF-1.4\ngarbage")
        run("run", self.src, "-m", MANIFEST, "-w", self.work)
        self.assertIn("pdf_unreadable", self.codes())


class TestPacketAndCache(Base):
    def test_zip_and_folder_give_same_hashes(self):
        zp = self.tmp / "p.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for f in self.src.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(self.src).as_posix())
        run("run", zp, "-m", MANIFEST, "-w", self.tmp / "wz")
        run("run", self.src, "-m", MANIFEST, "-w", self.tmp / "wf")
        hz = {s["path"]: s["sha256"] for s in read_json(self.tmp / "wz" / "intake_report.json")["sources"]}
        hf = {s["path"]: s["sha256"] for s in read_json(self.tmp / "wf" / "intake_report.json")["sources"]}
        self.assertEqual(hz, hf)

    def test_second_run_all_cache_hits(self):
        args = ("run", self.src, "-m", MANIFEST, "-s", "A1.1,A2.1,A3.1", "-w", self.work, "--continue-on-error")
        run(*args)
        run(*args)
        reports = sorted((self.work / "run_reports").glob("*.json"))
        first, second = read_json(reports[0]), read_json(reports[1])
        self.assertTrue(any(v["miss"] for v in first["cache"].values()))
        self.assertEqual(sum(v["miss"] for v in second["cache"].values()), 0)
        self.assertGreater(sum(v["hit"] for v in second["cache"].values()), 0)

    def test_changed_file_is_reprocessed_and_checkpoint_superseded(self):
        args = ("run", self.src, "-m", MANIFEST, "-s", "A1.1", "-w", self.work, "--continue-on-error")
        run(*args)
        pages = make_synthetic.drawing_set("review")
        pages[1].text(600, 1500, "NEW NOTE ADDED", 20)
        make_synthetic.write_pdf(self.src / "review" / "Ashford_Lot12_Rev3.pdf", pages)
        _, out = run(*args)
        self.assertIn("Sources changed since the last checkpoint", out)
        last = read_json(sorted((self.work / "run_reports").glob("*.json"))[-1])
        self.assertEqual(last["cache"]["index"]["miss"], 1)
        self.assertTrue(list(self.work.glob("checkpoint_superseded_*.json")))

    def test_packet_contents(self):
        code, out = run("run", self.src, "-m", MANIFEST, "-s", "A1.1,A2.1,A3.1,Z9.9", "-w", self.work, "--continue-on-error")
        pk = self.work / "packet"
        for f in ["README_FOR_REVIEWER.md", "source_manifest.json", "sheet_index.json", "intake_issues.json",
                  "checklists.json", "findings_template.json", "packet_stats.json"]:
            self.assertTrue((pk / f).exists(), f)
        self.assertIn("Requested sheet Z9.9 is not in set_to_review", out)
        a11 = read_json(pk / "sheets" / "a1.1" / "compare.json")
        td = a11["comparisons"]["approved_baseline"]["text_diff"]
        self.assertIn("MORNING ROOM", td["added_in_review"])
        self.assertGreaterEqual(len(a11["comparisons"]["approved_baseline"]["image_diff"]["regions"]), 1)
        self.assertTrue((pk / "sheets" / "a1.1" / "change_01_vs_baseline.png").exists())
        # rotated sheet indexed correctly
        idx = read_json(pk / "sheet_index.json")
        self.assertIn(["A3.1", "BUILDING SECTIONS", 4, "2"], idx["set_to_review"])
        cl = read_json(pk / "checklists.json")
        self.assertTrue(all(i["status"] == "Not Verified" for k in ("buyer_options", "prior_redlines", "rules") for i in cl[k]))
        self.assertEqual({r["scope"] for r in cl["rules"]}, {"general", "project", "community"})
        reds = cl["prior_redlines"]
        self.assertEqual(sorted(r["color"] for r in reds), ["orange", "red"])
        self.assertTrue(all((pk / r["crop"]).exists() for r in reds))
        cp = read_json(self.work / "checkpoint.json")
        self.assertEqual(cp["coverage"]["checked"], {})
        self.assertEqual(set(cp["coverage"]["not_checked"]), {"A0.0", "A1.1", "A2.1", "A3.1"})
        self.assertNotIn("passed", json.dumps({k: v for k, v in cp["coverage"].items() if k != "_note"}).lower())


class TestRecord(Base):
    def setUp(self):
        super().setUp()
        run("run", self.src, "-m", MANIFEST, "-s", "A1.1,A2.1", "-w", self.work, "--continue-on-error")
        self.tpl = read_json(self.work / "packet" / "findings_template.json")

    def write(self, data) -> Path:
        p = self.tmp / "findings.json"
        p.write_text(json.dumps(data))
        return p

    def good(self):
        d = json.loads(json.dumps(self.tpl))
        d["reviewer"] = "test"
        d["checked"] = [{"sheet": "A1.1", "scope": "full sheet vs baseline and prior redlines"}]
        d["findings"] = [
            {"type": "question", "scope": "general", "note": "Detail 5/A5.2 does not exist; confirm reference.",
             "target_sheet": "A1.1", "locations": [{"sheet": "A1.1", "region": [0.45, 0.8, 0.5, 0.85]}],
             "sources": [{"role": "set_to_review", "sheet": "A1.1"}]},
            {"type": "correction", "scope": "project", "note": "Elevation must read B per manifest and lot diagram.",
             "target_sheet": "A2.1", "locations": [{"sheet": "A2.1"}, {"sheet": "A1.1"}],
             "sources": [{"role": "lot_diagram"}, {"role": "buyer_selections"}]},
        ]
        red = next(r for r in d["prior_redlines"])
        red.update(status="Picked Up", evidence="A1.1 shows 36in min clear at pantry door; prior redline A1.1")
        return d

    def test_record_valid_and_coverage(self):
        code, out = run("record", "-w", self.work, self.write(self.good()))
        self.assertEqual(code, 0, out)
        cp = read_json(self.work / "checkpoint.json")
        self.assertEqual(list(cp["coverage"]["checked"]), ["A1.1"])
        self.assertIn("A2.1", cp["coverage"]["not_checked"])  # finding on A2.1 does not mark it checked
        self.assertEqual({f["color"] for f in cp["findings"]}, {"red", "orange"})
        code, out = run("status", "-w", self.work)
        self.assertIn("Findings: 1 red, 1 orange", out)

    def test_record_rejects_bad_findings(self):
        d = self.good()
        d["findings"][0]["note"] = "R1: fix this"
        d["findings"][1]["sources"] = []
        d["findings"].append(dict(d["findings"][0]))
        d["checked"].append({"sheet": "A2.1", "scope": ""})
        d["buyer_options"][0]["status"] = "Shown"  # no evidence
        d["prior_redlines"][0]["status"] = "Passed"
        code, out = run("record", "-w", self.work, self.write(d))
        self.assertEqual(code, 1)
        for s in ["R#/O# label", "cite at least one supporting source", "duplicate note", "describe the scope",
                  "needs evidence", "'Passed' must be one of"]:
            self.assertIn(s, out)
        self.assertEqual(read_json(self.work / "checkpoint.json")["findings"], [])

    def test_repeated_issue_merges_locations(self):
        run("record", "-w", self.work, self.write(self.good()))
        d = self.good()
        d["findings"] = [dict(d["findings"][1], locations=[{"sheet": "A2.1", "region": [0.1, 0.1, 0.2, 0.2]}])]
        run("record", "-w", self.work, self.write(d))
        cp = read_json(self.work / "checkpoint.json")
        self.assertEqual(len(cp["findings"]), 2)
        f = next(f for f in cp["findings"] if f["target_sheet"] == "A2.1")
        self.assertEqual(len(f["locations"]), 3)


class TestUnits(unittest.TestCase):
    def test_sheet_key(self):
        self.assertEqual(sheet_key("a-2.1"), "A2.1")
        self.assertEqual(sheet_key("A 2.1"), "A2.1")

    def test_norm(self):
        self.assertEqual(norm_value("handedness", "L"), "LEFT")
        self.assertEqual(norm_value("revision", "Rev 03"), "3")

    def test_rotation_roundtrip(self):
        for rot in (0, 90, 180, 270):
            W, H = (1728, 2592) if rot in (90, 270) else (2592, 1728)
            g = {"rotation": rot, "W": W, "H": H, "ox": 0, "oy": 0}
            for u, v in ((0.1, 0.2), (0.9, 0.75)):
                x, y = visual_frac_to_user(u, v, g)
                uu, vv = user_to_visual_frac(x, y, g)
                self.assertAlmostEqual(u, uu)
                self.assertAlmostEqual(v, vv)


if __name__ == "__main__":
    unittest.main()
