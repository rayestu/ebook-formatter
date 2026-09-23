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
        self.pages_of = {}                           # (band, key) -> {page indexes}
        for p in pages:
            for l in p.lines:
                band = _band(l, p)
                if band and len(l.text.strip()) > 1:
                    key = norm_key(l.text)
                    self.pages_of.setdefault((band, key), set()).add(p.index)
                    if re.search(r"[^\W\d_]{3}", key):
                        yb = round((l.bbox[1] + l.bbox[3]) / 2 / p.height * 100 / 2)
                        self.seen.setdefault((band, key, yb), []).append(p.index)
        self.need = max(3, int(0.3 * self.n + 0.5)) if self.n >= 3 else 99

    def repeats(self, band, key):
        """Does this exact (digit-masked) text also occur in the same band on another page?"""
        return len(self.pages_of.get((band, key), ())) >= 2

    def hit(self, band, key, yb):
        idx = sorted({i for d in (-1, 0, 1) for i in self.seen.get((band, key, yb + d), [])})
        if len(idx) >= 3:
            return True
        return len(idx) == 2 and idx[1] - idx[0] <= 2 and len(key) >= 4


def running_keys(pages):
    return RunningKeys(pages)


def _continues_body(page, ln, body, direction):
    """True if `ln` is a normal-size text line sitting flush against body text on its inner side
    (next line below for a header-zone line, previous line above for a footer-zone line):
    then it is the first/last line of the body, not a header."""
    if abs(ln.size - body) > 0.6 or len(ln.text.strip()) < 25:
        return False
    for o in page.lines:
        if o is ln or abs(o.size - ln.size) > 0.6:
            continue
        gap = (o.bbox[1] - ln.bbox[3]) if direction > 0 else (ln.bbox[1] - o.bbox[3])
        overlap = min(o.bbox[2], ln.bbox[2]) - max(o.bbox[0], ln.bbox[0])
        if -0.3 * ln.size <= gap < 0.45 * ln.size and overlap > 0.5 * min(o.bbox[2] - o.bbox[0], ln.bbox[2] - ln.bbox[0]) \
                and abs(o.bbox[0] - ln.bbox[0]) < 3 * ln.size:
            return True
    return False


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
            elif cy < opts.top_pct:
                # text seen only once is a heading / body line, not a running header: keep it
                if rkeys.repeats("top", norm_key(ln.text)) and not _continues_body(p, ln, body, +1):
                    m[i] = "header"
            elif cy > 100 - opts.bottom_pct:
                if rkeys.repeats("bottom", norm_key(ln.text)) and not _continues_body(p, ln, body, -1):
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
