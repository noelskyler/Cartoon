"""PDF access (pypdfium2 for text/rendering, pypdf for annotations) with a hash-keyed cache."""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

Image.MAX_IMAGE_PIXELS = 400_000_000  # large-format sheets at high DPI are legitimate


class Cache:
    """Content-addressed cache: <work>/cache/<sha256>/... . Counts hits/misses by kind."""

    def __init__(self, root: Path):
        self.root = root
        self.stats: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "miss": 0})

    def path(self, sha: str, *parts: str) -> Path:
        p = self.root / sha[:2] / sha
        for part in parts:
            p = p / part
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def hit(self, kind: str) -> None:
        self.stats[kind]["hit"] += 1

    def miss(self, kind: str) -> None:
        self.stats[kind]["miss"] += 1

    def summary(self) -> dict:
        return {k: dict(v) for k, v in sorted(self.stats.items())}


class Docs:
    """Opens each blob once per run."""

    def __init__(self):
        self._docs: dict[str, pdfium.PdfDocument] = {}

    def get(self, sha: str, blob: Path) -> pdfium.PdfDocument:
        if sha not in self._docs:
            self._docs[sha] = pdfium.PdfDocument(str(blob))
        return self._docs[sha]

    def close(self) -> None:
        for d in self._docs.values():
            try:
                d.close()
            except Exception:
                pass
        self._docs.clear()


# ---- coordinate mapping between visible-sheet fractions and PDF user space ----

def page_geometry(page) -> dict:
    l, b, r, t = page.get_mediabox()
    return {"rotation": page.get_rotation() % 360, "W": r - l, "H": t - b, "ox": l, "oy": b,
            "vis_w": page.get_size()[0], "vis_h": page.get_size()[1]}


def visual_frac_to_user(u: float, v: float, g: dict) -> tuple[float, float]:
    """(u, v): fractions of the visible sheet, origin top-left. Returns PDF user-space point."""
    W, H, rot = g["W"], g["H"], g["rotation"]
    if rot == 90:
        x, y = v * W, u * H
    elif rot == 180:
        x, y = W - u * W, v * H
    elif rot == 270:
        x, y = W - v * W, H - u * H
    else:
        x, y = u * W, (1 - v) * H
    return x + g["ox"], y + g["oy"]


def user_to_visual_frac(x: float, y: float, g: dict) -> tuple[float, float]:
    W, H, rot = g["W"], g["H"], g["rotation"]
    x, y = x - g["ox"], y - g["oy"]
    if rot == 90:
        return y / H, x / W
    if rot == 180:
        return (W - x) / W, y / H
    if rot == 270:
        return (H - y) / H, (W - x) / W
    return x / W, 1 - y / H


def region_to_user_rect(region, g) -> tuple[float, float, float, float]:
    (x0, y0), (x1, y1) = visual_frac_to_user(region[0], region[1], g), visual_frac_to_user(region[2], region[3], g)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def page_text(page) -> str:
    tp = page.get_textpage()
    try:
        return tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
    finally:
        tp.close()


def region_text(page, region) -> str:
    g = page_geometry(page)
    left, bottom, right, top = region_to_user_rect(region, g)
    tp = page.get_textpage()
    try:
        # get_text_bounded() drops line breaks for rotated text, so filter characters by box instead
        # and keep pdfium's own line breaks.
        n = tp.count_chars()
        full = tp.get_text_range()
        same = len(full) == n
        out = []
        for i in range(n):
            ch = full[i] if same else tp.get_text_range(i, 1)
            if ch in "\r\n":
                out.append("\n")
                continue
            x0, y0, x1, y1 = tp.get_charbox(i)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            if left <= cx <= right and bottom <= cy <= top:
                out.append(ch)
            elif out and out[-1] != "\n":
                out.append("\n")
        return "\n".join(ln.strip() for ln in "".join(out).split("\n") if ln.strip())
    finally:
        tp.close()


def render(cache: Cache, docs: Docs, sha: str, blob: Path, page_index: int, dpi: int, color: bool = False) -> Image.Image:
    """Render of a whole page (greyscale unless color=True), cached as PNG by (file hash, page, dpi, mode)."""
    p = cache.path(sha, "render", f"p{page_index + 1:04d}_{dpi}dpi{'_rgb' if color else ''}.png")
    if p.exists():
        cache.hit("render")
        return Image.open(p)
    cache.miss("render")
    page = docs.get(sha, blob)[page_index]
    img = page.render(scale=dpi / 72.0, grayscale=not color).to_pil().convert("RGB" if color else "L")
    img.save(p, optimize=False)
    return img


def crop_frac(img: Image.Image, region) -> Image.Image:
    w, h = img.size
    box = (int(region[0] * w), int(region[1] * h), int(region[2] * w + 0.999), int(region[3] * h + 0.999))
    return img.crop(box)


# ---- annotations (prior redlines) ----

def _color_name(c) -> str:
    try:
        r, g, b = (float(x) for x in c[:3])
    except Exception:
        return "unknown"
    if r > 0.8 and g < 0.3 and b < 0.3:
        return "red"
    if r > 0.8 and 0.3 <= g < 0.8 and b < 0.35:
        return "orange"
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


SKIP_ANNOTS = {"/Link", "/Widget", "/Popup"}


def read_annotations(blob: Path, docs: Docs, sha: str) -> list[dict]:
    from pypdf import PdfReader

    out = []
    reader = PdfReader(str(blob))
    pdoc = docs.get(sha, blob)
    for i, pg in enumerate(reader.pages):
        annots = pg.get("/Annots")
        if not annots:
            continue
        g = page_geometry(pdoc[i])
        for a in annots:
            a = a.get_object()
            sub = str(a.get("/Subtype"))
            if sub in SKIP_ANNOTS:
                continue
            contents = str(a.get("/Contents") or "").strip()
            rect = [float(x) for x in a.get("/Rect", [0, 0, 0, 0])]
            (u0, v0), (u1, v1) = user_to_visual_frac(rect[0], rect[1], g), user_to_visual_frac(rect[2], rect[3], g)
            out.append({
                "page": i + 1,
                "subtype": sub.lstrip("/"),
                "contents": contents,
                "color": _color_name(a.get("/C") or []),
                "author": str(a.get("/T") or ""),
                "region": [round(max(0, min(u0, u1)), 4), round(max(0, min(v0, v1)), 4),
                           round(min(1, max(u0, u1)), 4), round(min(1, max(v0, v1)), 4)],
            })
    return out


class Timer:
    def __init__(self):
        self.phases: dict[str, float] = {}
        self._t = time.perf_counter()

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.phases[name] = round(now - self._t, 3)
        self._t = now
