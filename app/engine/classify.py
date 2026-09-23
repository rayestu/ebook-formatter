"""Decide which lines are headers / footers / page numbers / running heads."""
import re
from collections import Counter

from .options import Options

BAND = 0.15  # pages' outer 15% is where page numbers / running heads are searched

_ROMAN = r"(?=[ivxlcdm]+$)m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})"
PAGENUM_RE = re.compile(
    r"^\s*(?:[-–—•·]\s*)?(?:page\s+)?(?:\d{1,5}|" + _ROMAN + r")(?:\s+(?:of|/)\s+\d{1,5})?(?:\s*[-–—•·])?\s*$",
    re.I,
)


def norm_key(text: str) -> str:
    t = re.sub(r"\d+", "#", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def body_size(pages) -> float:
    c = Counter()
    for p in pages:
        for ln in p.lines:
            c[ln.size] += len(ln.text.strip())
    return c.most_common(1)[0][0] if c else 11.0


def _band(ln, page):
    cy = (ln.bbox[1] + ln.bbox[3]) / 2 / page.height
    if cy < BAND:
        return "top"
    if cy > 1 - BAND:
        return "bottom"
    return None


class RunningKeys:
    """Texts that repeat in the top/bottom bands at the same height.

    Book-wide heads repeat on >=30% of pages; chapter-specific heads ("I  Introduction")
    only on the pages of one chapter, so 3 sightings anywhere, or 2 within a few pages, is enough.
    """

    def __init__(self, pages):
        self.n = len(pages)
        self.seen = {}                               # (band, key, ybucket) -> [page indexes]
        for p in pages:
            for l in p.lines:
                band = _band(l, p)
                if band and len(l.text.strip()) > 1:
                    key = norm_key(l.text)
                    if re.search(r"[^\W\d_]{3}", key):
                        yb = round((l.bbox[1] + l.bbox[3]) / 2 / p.height * 100 / 2)
                        self.seen.setdefault((band, key, yb), []).append(p.index)
        self.need = max(3, int(0.3 * self.n + 0.5)) if self.n >= 3 else 99

    def hit(self, band, key, yb):
        idx = sorted({i for d in (-1, 0, 1) for i in self.seen.get((band, key, yb + d), [])})
        if len(idx) >= 3:
            return True
        return len(idx) == 2 and idx[1] - idx[0] <= 2 and len(key) >= 4


def running_keys(pages):
    return RunningKeys(pages)


def _is_heading_sized(ln, body):
    """A line distinctly larger than body text (the same threshold flow.py uses for headings):
    a real, one-off heading, not page furniture, so the margin sliders leave it alone."""
    return bool(body) and ln.size >= 1.15 * body


def _flush_neighbor(page, anchor, direction):
    """The line immediately touching `anchor` on its `direction` side (below if +1, above if -1):
    same size, small gap, clearly overlapping horizontally -- i.e. the next/previous line of the
    same paragraph. None if there isn't one."""
    for o in page.lines:
        if o is anchor or abs(o.size - anchor.size) > 0.6:
            continue
        gap = (o.bbox[1] - anchor.bbox[3]) if direction > 0 else (anchor.bbox[1] - o.bbox[3])
        overlap = min(o.bbox[2], anchor.bbox[2]) - max(o.bbox[0], anchor.bbox[0])
        if -0.3 * anchor.size <= gap < 0.45 * anchor.size \
                and overlap > 0.5 * min(o.bbox[2] - o.bbox[0], anchor.bbox[2] - anchor.bbox[0]) \
                and abs(o.bbox[0] - anchor.bbox[0]) < 3 * anchor.size:
            return o
    return None


def _continues_body(page, ln, body, direction, zone_edge):
    """True if `ln` is the first/last line of a real paragraph that mostly lives outside the strip
    zone, so the margin sliders should leave it alone. That takes two things: a flush neighbor
    that is itself past `zone_edge` (the slider boundary -- two stacked header lines of the same
    size must not protect each other), and that neighbor having a flush neighbor of its own, so a
    single stray junk line just outside the zone can't falsely protect the line before it either."""
    if abs(ln.size - body) > 0.6 or len(ln.text.strip()) < 25:
        return False
    o1 = _flush_neighbor(page, ln, direction)
    if o1 is None:
        return False
    o1_cy = (o1.bbox[1] + o1.bbox[3]) / 2 / page.height * 100
    if (direction > 0 and o1_cy < zone_edge) or (direction < 0 and o1_cy > zone_edge):
        return False                            # neighbor is still inside the zone itself
    return _flush_neighbor(page, o1, direction) is not None


def classify(pages, opts: Options, rkeys=None, body=None) -> dict:
    """Return {page_index: {line_idx: kind}}."""
    rkeys = running_keys(pages) if rkeys is None else rkeys
    body = body_size(pages) if body is None else body
    out = {}
    for p in pages:
        m = {}
        for i, ln in enumerate(p.lines):
            cy = (ln.bbox[1] + ln.bbox[3]) / 2 / p.height * 100
            band = _band(ln, p)
            if band and PAGENUM_RE.match(ln.text):
                m[i] = "pagenum"
            elif band and rkeys.hit(band, norm_key(ln.text), round(cy / 2)):
                m[i] = "running"
            elif cy < opts.top_for(p.index):
                # the slider is a deliberate "cut this zone" instruction: strip whatever is in it,
                # except a distinctly larger heading or a paragraph's first line poking into the zone
                if not _is_heading_sized(ln, body) and not _continues_body(p, ln, body, +1, opts.top_for(p.index)):
                    m[i] = "header"
            elif cy > 100 - opts.bottom_for(p.index):
                if not _is_heading_sized(ln, body) and not _continues_body(p, ln, body, -1, 100 - opts.bottom_for(p.index)):
                    m[i] = "footer"
        pn_rows = [(p.lines[i].bbox[1] + p.lines[i].bbox[3]) / 2 for i, k in m.items() if k == "pagenum"]
        for i, ln in enumerate(p.lines):
            if i in m or not _band(ln, p) or len(ln.text) > 100:
                continue
            cy = (ln.bbox[1] + ln.bbox[3]) / 2
            if any(abs(cy - r) < 0.6 * ln.size for r in pn_rows):
                m[i] = "running"
        out[p.index] = m
    return out
