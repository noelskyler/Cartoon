# plancheck

A local, read-only toolkit for **checking** architectural drawing sets (PDF). It does not draw, edit, sign, seal, send, or issue drawings, and it never modifies originals.

It takes a folder or ZIP of PDFs plus a project manifest, then:

1. **Intake.** Hashes every file (SHA-256) and keeps read-only working copies. Matches files to roles from the manifest. Flags missing, undeclared, duplicate, or ambiguous sources and hash mismatches. It never substitutes another file.
2. **Sheet index.** Reads each page's title block (sheet number, title, model, lot, elevation, handedness, revision, date), its detail references, and the cover-sheet index.
3. **Validation.** Flags conflicts between the title blocks, manifest, lot diagram, and buyer selections. Also flags sheet-set and cover-index mismatches, references to missing sheets, and comparisons that are not like for like.
4. **Review packet.** For the sheets you select, writes a small folder for a human or any AI reviewer: source manifest, sheet index, issues, checklists, sheet text, overview images, and diffs against the approved baseline and master set. It also writes detail crops of changed areas and side-by-side crops of each prior redline next to the same area today.
5. **Checkpoint.** Records the exact source versions, which sheets were checked and to what scope, findings, checklist statuses, and unresolved items, so another reviewer can resume. Nothing is ever marked "passed".

Results are cached by file hash and settings. Unchanged files are not processed again.

## Windows: easiest start

Double-click `plancheck.cmd`. A menu offers: 1 = try the sample, 2 = check your own project (it asks for the folder/ZIP and manifest; you can drag them onto the window), 3 = status. The packet folder opens automatically when it's done.

## Windows: one command

Requires Python 3.10+ ([python.org](https://www.python.org/downloads/), tick "Add to PATH"). From this folder:

```bat
plancheck.cmd run samples\synthetic_project.zip --manifest samples\manifest.yaml --sheets A1.1,A2.1,A3.1 --continue-on-error
```

The first run creates `.venv` and installs `pypdfium2`, `pypdf`, `Pillow`, and `PyYAML`. Use your own ZIP or folder and manifest in place of the samples. Leave out `--continue-on-error` on real jobs: by default, no packet is exported while intake has errors. The sample set has planted errors on purpose.

Other platforms: `pip install -r requirements.txt`, then `PYTHONPATH=src python -m plancheck ...`.

## Commands

| Command | What it does |
|---|---|
| `run SOURCE -m MANIFEST [-s A1.1,A2.1 \| all] [-w WORK]` | Intake, index, validate, and (with `-s`) export a packet to `WORK/packet`. |
| `render -w WORK --sheet A2.1 [--role ...] [--dpi 300] [--region l,t,r,b] [--color]` | Full-resolution view of a sheet or region, when the packet images are not enough. |
| `record -w WORK findings.json` | Validates the reviewer's findings and merges them into `WORK/checkpoint.json`. |
| `status -w WORK` | Prints sources, coverage, findings, and unresolved items. |

The default work folder is `work\<project>_lot<n>`. It holds `sources\` (read-only, hash-named copies), `cache\`, `intake_report.json`, `checkpoint.json`, `run_reports\` (runtime and cache hits per run), and `packet\`.

## Manifest

See `samples/manifest.yaml`. Required fields: `project.name`, `lot`, `model`, `elevation`, `handedness`, and `revision`, plus a `files` list with a `path` and `role` for each file. Roles are `set_to_review`, `approved_baseline`, `master_set`, `community_checklist`, `buyer_selections`, `lot_diagram`, and `prior_redlines`. Optional per-file fields are `revision`, `date`, and `sha256`. If you set `sha256`, any other version of that file fails intake. Also optional: `buyer_options` (name, keywords, `expect_on` sheets), `rules.project`, `rules.community`, and `settings`.

**Title blocks differ by firm.** Set `settings.title_block_region` (fractions of the visible sheet: left, top, right, bottom) and, if needed, `settings.field_patterns` (regexes, one capture group each; see `DEFAULT_FIELD_PATTERNS` in `src/plancheck/index.py`). If sheet numbers come back as "not found", the region is the first thing to check.

## Review conventions built in

- Buyer options: **Shown / Missing / Conflicting / Not Verified**. Prior redlines: **Picked Up / Missed / Changed Beyond Scope / Not Verified**. Every item starts as Not Verified, and any other status requires evidence.
- Findings: `correction` is red (confirmed), `question` is orange (question or unresolved conflict). Each finding needs a target sheet and at least one supporting source. Notes must be short (200 characters or fewer) with no R1/O2-style labels. A repeated issue is one finding with several locations; recording the same note again merges the locations.
- Rules stay separate: `general` (built in), `project` (manifest), and `community` (manifest plus lines from the community checklist PDF).
- Text and image diffs are reported as **leads**, never as a pass. In diff overlays, blue is ink only in the review set and magenta is ink only in the comparison set.

## Tests

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```

`tests/make_synthetic.py` regenerates `samples/synthetic_project.zip` (deterministic). All data is invented and includes planted problems: an elevation conflict, a stale sheet revision, a cover index entry for a missing sheet, a bad detail reference, buyer-option conflicts, an undeclared file, and rotated pages.

## Not built yet

- Stage-3 assembly (combining approved and master sheets, rolling out lots). It is deliberately not automated until intake and review are proven reliable on real sets.
- Export verification (reopen the assembled PDF and check order, linework, and clipped tags).
- OCR for scanned sheets. Pages with no text layer show up as "sheet number not found".
