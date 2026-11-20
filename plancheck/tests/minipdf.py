"""Tiny deterministic PDF writer for synthetic test drawings (text, lines, rectangles, markup annotations)."""
from __future__ import annotations

from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class Page:
    """Coordinates are given on the *visible* sheet (origin bottom-left, w x h).

    rotate=90 stores the page portrait with /Rotate 90, as some CAD exports do,
    so the visible sheet is still landscape.
    """

    def __init__(self, w: float = 2592, h: float = 1728, rotate: int = 0):  # ARCH D 36x24 in
        assert rotate in (0, 90)
        self.w, self.h, self.rotate = w, h, rotate
        self.ops: list[str] = []
        self.annots: list[dict] = []

    def _u(self, x, y):
        return (self.h - y, x) if self.rotate == 90 else (x, y)

    @property
    def mediabox(self):
        return (self.h, self.w) if self.rotate == 90 else (self.w, self.h)

    def text(self, x, y, s, size=12):
        ux, uy = self._u(x, y)
        m = "0 1 -1 0" if self.rotate == 90 else "1 0 0 1"
        self.ops.append(f"BT /F1 {size} Tf {m} {ux:.2f} {uy:.2f} Tm ({_esc(s)}) Tj ET")

    def line(self, x1, y1, x2, y2, lw=1.0):
        (a, b), (c, d) = self._u(x1, y1), self._u(x2, y2)
        self.ops.append(f"{lw} w {a:.2f} {b:.2f} m {c:.2f} {d:.2f} l S")

    def _box(self, x, y, w, h):
        (a, b), (c, d) = self._u(x, y), self._u(x + w, y + h)
        return min(a, c), min(b, d), max(a, c), max(b, d)

    def rect(self, x, y, w, h, lw=1.0):
        x0, y0, x1, y1 = self._box(x, y, w, h)
        self.ops.append(f"{lw} w {x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f} re S")

    def annot(self, x, y, w, h, contents, color=(1, 0, 0), author="Reviewer"):
        self.annots.append({"rect": self._box(x, y, w, h), "contents": contents, "color": color, "author": author})


def write_pdf(path: Path, pages: list[Page]) -> None:
    objs: list[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    add(b"")  # 1 catalog placeholder
    add(b"")  # 2 pages placeholder
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids = []
    for p in pages:
        content = "\n".join(p.ops).encode("latin-1")
        cid = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        annot_ids = []
        for a in p.annots:
            r = " ".join(f"{v:.2f}" for v in a["rect"])
            c = " ".join(str(v) for v in a["color"])
            annot_ids.append(add(
                f"<< /Type /Annot /Subtype /Square /Rect [{r}] /C [{c}] /BS << /W 3 >> "
                f"/Contents ({_esc(a['contents'])}) /T ({_esc(a['author'])}) >>".encode("latin-1")))
        annots = f" /Annots [{' '.join(f'{i} 0 R' for i in annot_ids)}]" if annot_ids else ""
        rot = f" /Rotate {p.rotate}" if p.rotate else ""
        kids.append(add(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {p.mediabox[0]} {p.mediabox[1]}]{rot} /Resources << /Font << /F1 {font} 0 R >> >> "
            f"/Contents {cid} 0 R{annots} >>".encode()))
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
