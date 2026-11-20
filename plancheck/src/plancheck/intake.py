"""Source intake: ZIP or folder -> content-addressed, read-only copies.

Originals are only ever opened for reading. Every file is hashed (SHA-256)
and copied once into <work>/sources/<sha256>.pdf, which is marked read-only.
All later processing reads those copies, so findings are tied to exact bytes.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .common import Issues, make_read_only, sha256_file


@dataclass
class SourceFile:
    relpath: str          # path inside the folder / ZIP, forward slashes
    sha256: str
    size: int
    is_pdf: bool
    blob: Path | None     # read-only working copy (PDFs only)
    reused_blob: bool     # True if the blob already existed (unchanged file)


def _safe_member(name: str) -> str | None:
    """Reject absolute paths, drive letters and '..' (zip-slip)."""
    n = name.replace("\\", "/")
    p = PurePosixPath(n)
    if n.startswith("/") or (len(n) > 1 and n[1] == ":") or ".." in p.parts:
        return None
    return str(p)


def _store_blob(blobs: Path, sha: str, src_reader) -> tuple[Path, bool]:
    dest = blobs / f"{sha}.pdf"
    if dest.exists():
        return dest, True
    tmp = blobs / f".{sha}.tmp"
    with open(tmp, "wb") as out:
        src_reader(out)
    os.replace(tmp, dest)
    make_read_only(dest)
    return dest, False


def collect_sources(src: Path, work: Path, ignore: set[Path] | None = None) -> tuple[list[SourceFile], dict, Issues]:
    issues = Issues()
    blobs = work / "sources"
    blobs.mkdir(parents=True, exist_ok=True)
    ignore = {p.resolve() for p in (ignore or set())}
    files: list[SourceFile] = []
    container: dict = {"path": str(src)}

    if src.is_file() and zipfile.is_zipfile(src):
        container.update(kind="zip", sha256=sha256_file(src), size=src.stat().st_size)
        with zipfile.ZipFile(src) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = _safe_member(info.filename)
                if name is None:
                    issues.add("ERROR", "zip_unsafe_path", f"ZIP member '{info.filename}' has an unsafe path; skipped.")
                    continue
                if PurePosixPath(name).name.startswith(".") or "__MACOSX" in name:
                    continue
                if info.flag_bits & 0x1:
                    issues.add("ERROR", "zip_encrypted", f"ZIP member '{name}' is encrypted; skipped.", file=name)
                    continue
                h = hashlib.sha256()
                with zf.open(info) as fh:
                    head = fh.read(5)
                    h.update(head)
                    while chunk := fh.read(1 << 20):
                        h.update(chunk)
                sha = h.hexdigest()
                is_pdf = head.startswith(b"%PDF-")
                blob, reused = None, False
                if is_pdf:
                    def reader(out, info=info):
                        with zf.open(info) as fh2:
                            shutil.copyfileobj(fh2, out)
                    blob, reused = _store_blob(blobs, sha, reader)
                files.append(SourceFile(name, sha, info.file_size, is_pdf, blob, reused))
    elif src.is_dir():
        container.update(kind="folder")
        srcr = src.resolve()
        try:
            work.resolve().relative_to(srcr)
            issues.add("ERROR", "work_inside_source",
                       "The work folder is inside the source folder. Choose a work folder elsewhere so originals stay untouched.")
            return [], container, issues
        except ValueError:
            pass
        for root, dirs, names in os.walk(srcr):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for n in sorted(names):
                p = Path(root) / n
                if n.startswith(".") or p.resolve() in ignore:
                    continue
                rel = p.relative_to(srcr).as_posix()
                with open(p, "rb") as fh:
                    head = fh.read(5)
                is_pdf = head.startswith(b"%PDF-")
                sha = sha256_file(p)
                blob, reused = None, False
                if is_pdf:
                    def reader(out, p=p):
                        with open(p, "rb") as fh2:
                            shutil.copyfileobj(fh2, out)
                    blob, reused = _store_blob(blobs, sha, reader)
                    if sha256_file(blob) != sha:
                        issues.add("ERROR", "source_changed_during_read",
                                   f"{rel} changed while being read; re-run intake.", file=rel)
                files.append(SourceFile(rel, sha, p.stat().st_size, is_pdf, blob, reused))
    else:
        issues.add("ERROR", "source_not_found", f"{src} is not a folder or ZIP file.")
    return files, container, issues
