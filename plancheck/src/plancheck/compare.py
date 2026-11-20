"""Basic like-for-like comparisons. Output is leads for a reviewer, never a pass/fail."""
from __future__ import annotations

import re
from collections import Counter, deque

from PIL import Image, ImageChops, ImageDraw

from .pdfio import crop_frac


def _norm_lines(text: str, exclude: set[str]) -> list[str]:
    out = []
    for ln in text.splitlines():
        s = re.sub(r"\s+", " ", ln).strip()
        if s and s not in exclude:
            out.append(s.upper())
    return out


def text_diff(review_text: str, other_text: str, review_tb: str, other_tb: str, limit: int = 40) -> dict:
    ex = {re.sub(r"\s+", " ", x).strip() for x in (review_tb + "\n" + other_tb).splitlines()}
    a, b = Counter(_norm_lines(review_text, ex)), Counter(_norm_lines(other_text, ex))
    added = sorted((a - b).elements())
    removed = sorted((b - a).elements())
    return {"added_in_review": added[:limit], "removed_from_review": removed[:limit],
            "added_count": len(added), "removed_count": len(removed),
            "truncated": len(added) > limit or len(removed) > limit}


def title_block_diff(rf: dict, of: dict) -> dict:
    keys = sorted(set(rf) | set(of))
    return {k: {"review": rf.get(k), "other": of.get(k)} for k in keys if (rf.get(k) or "").upper() != (of.get(k) or "").upper()}


def image_diff(review: Image.Image, other: Image.Image, threshold: int, grid: int,
               exclude_region=None, max_regions: int = 6) -> tuple[dict, Image.Image | None]:
    if review.size != other.size:
        return {"comparable": False, "reason": f"render sizes differ {review.size} vs {other.size} (sheet size or rotation differs)"}, None
    w, h = review.size
    lut = [255 if v > threshold else 0 for v in range(256)]
    added = ImageChops.subtract(other, review).point(lut)      # ink in review, not in other
    removed = ImageChops.subtract(review, other).point(lut)    # ink in other, not in review
    mask = ImageChops.lighter(added, removed)
    if exclude_region:
        d = ImageDraw.Draw(mask)
        d.rectangle([int(exclude_region[0] * w), int(exclude_region[1] * h),
                     int(exclude_region[2] * w), int(exclude_region[3] * h)], fill=0)
    changed_px = mask.histogram()[255]
    # cluster changed cells on a coarse grid
    cell = max(4, max(w, h) // grid)
    gw, gh = (w + cell - 1) // cell, (h + cell - 1) // cell
    small = mask.resize((gw, gh), Image.Resampling.BOX)
    px = small.load()
    on = {(x, y) for y in range(gh) for x in range(gw) if px[x, y] > 0}
    regions = []
    seen = set()
    for start in sorted(on, key=lambda c: (c[1], c[0])):
        if start in seen:
            continue
        q, comp = deque([start]), []
        seen.add(start)
        while q:
            cx, cy = q.popleft()
            comp.append((cx, cy))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n in on and n not in seen:
                        seen.add(n)
                        q.append(n)
        xs, ys = [c[0] for c in comp], [c[1] for c in comp]
        x0, y0 = max(0, (min(xs) - 1) * cell), max(0, (min(ys) - 1) * cell)
        x1, y1 = min(w, (max(xs) + 2) * cell), min(h, (max(ys) + 2) * cell)
        regions.append({"region": [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)],
                        "changed_px": mask.crop((x0, y0, x1, y1)).histogram()[255]})
    regions.sort(key=lambda r: -r["changed_px"])
    # overlay: lightened review, blue = only in review, magenta = only in other
    base = review.point(lambda v: 160 + v * 95 // 255).convert("RGB")
    base.paste((0, 90, 255), mask=added)
    base.paste((200, 0, 200), mask=removed)
    tb_changed = None
    if exclude_region:
        tb_changed = ImageChops.lighter(crop_frac(added, exclude_region), crop_frac(removed, exclude_region)).histogram()[255] > 0
    return {
        "comparable": True,
        "changed_pct": round(100 * changed_px / (w * h), 4),
        "title_block_changed": tb_changed,
        "regions": regions[:max_regions],
        "regions_total": len(regions),
    }, base


def side_by_side(left: Image.Image, right: Image.Image, labels: tuple[str, str]) -> Image.Image:
    pad, bar = 8, 22
    w = left.width + right.width + pad * 3
    h = max(left.height, right.height) + bar + pad
    out = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(out)
    d.text((pad, 4), labels[0], fill=(0, 0, 0))
    d.text((left.width + pad * 2, 4), labels[1], fill=(0, 0, 0))
    out.paste(left.convert("RGB"), (pad, bar))
    out.paste(right.convert("RGB"), (left.width + pad * 2, bar))
    d.line([(left.width + pad * 1.5, 0), (left.width + pad * 1.5, h)], fill=(120, 120, 120), width=1)
    return out
