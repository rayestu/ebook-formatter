"""Table-of-contents discovery: native bookmarks, visual contents pages, heading fallback."""
import re
from collections import Counter
from dataclasses import dataclass

from .classify import PAGENUM_RE
from .flow import strip_markers

CONTENTS_RE = re.compile(r"^\s*(table\s+of\s+)?contents\s*$", re.I)
LEADER_RE = re.compile(r"^(.*?\S)\s*(?:[.·…•_\-]\s*){3,}\s*(\d{1,4}|[ivxlc]+)\s*$", re.I)
TRAIL_RE = re.compile(r"^(.*?[^\W\d_].*?)\s{2,}(\d{1,4})\s*$")


@dataclass
class TocEntry:
    title: str
    level: int
    page: int          # 0-based PDF page
    y: float = 0.0     # points from page top; 0 -> page start


@dataclass
class TocResult:
    entries: list
    source: str        # bookmarks | visual | none
    skip_pages: frozenset = frozenset()


def native_toc(doc) -> list:
    entries = []
    try:
        raw = doc.get_toc(simple=False)
    except Exception:
        return entries
    for lvl, title, page, dest in raw:
        if page < 1 or page > len(doc):
            continue
        y = 0.0
        to = dest.get("to") if isinstance(dest, dict) else None
        if to is not None:
            try:
                y = max(0.0, float(to.y))
            except Exception:
                y = 0.0
        entries.append(TocEntry(title.strip() or "Untitled", max(1, lvl), page - 1, y))
    return entries


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _rows(page):
    """Group page lines into visual rows (lines whose vertical centres coincide)."""
    rows = []
    for ln in sorted(page.lines, key=lambda l: ((l.bbox[1] + l.bbox[3]) / 2, l.bbox[0])):
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows and abs(rows[-1][0] - cy) < ln.size * 0.5:
            rows[-1][1].append(ln)
        else:
            rows.append([cy, [ln]])
    return [sorted(r[1], key=lambda l: l.bbox[0]) for r in rows]


LOOSE_RE = re.compile(r"^(.*?[^\W\d_].*?)\s+(\d{1,4})$")
ROMAN_LOW = re.compile(r"[ivxlc]{1,7}")
LABEL_ONLY = re.compile(r"(chapter|part|appendix|section|book)$", re.I)


def _parse_row(row):
    """-> (title, printed_page_str, x0, size) or None. Only called for rows on a Contents page."""
    text = " ".join(l.text.strip() for l in row)
    m = LEADER_RE.match(text) or TRAIL_RE.match(text)
    if m:
        return m.group(1).strip(" .·…"), m.group(2), row[0].bbox[0], row[0].size
    if len(row) >= 2:
        last = row[-1].text.strip()
        if re.fullmatch(r"\d{1,4}", last) or ROMAN_LOW.fullmatch(last):
            gap = row[-1].bbox[0] - row[-2].bbox[2]
            title = " ".join(l.text.strip() for l in row[:-1])
            if gap > 2.5 * row[0].size and re.search(r"[^\W\d_]", title):
                return title, last, row[0].bbox[0], row[0].size
    m = LOOSE_RE.match(text)
    if m and not LABEL_ONLY.search(m.group(1).strip()):
        return m.group(1).strip(), m.group(2), row[0].bbox[0], row[0].size
    return None


def visual_toc(doc, pages) -> TocResult:
    limit = min(len(pages), 30)
    start = next((p.index for p in pages[:limit]
                  if any(CONTENTS_RE.match(l.text) for l in p.lines)), None)
    if start is None:
        return TocResult([], "none")
    raw, toc_pages = [], []
    for p in pages[start:start + 6]:
        got = [r for r in (_parse_row(row) for row in _rows(p)) if r]
        if len(got) < 3 and p.index != start:
            break
        if got:
            toc_pages.append(p.index)
            raw.extend(got)
    raw = [r for r in raw if r[1].isdigit() or PAGENUM_RE.match(r[1])]
    if len(raw) < 3:
        return TocResult([], "none")
    after = toc_pages[-1] + 1
    # printed page -> pdf page offset, voted by locating titles as heading-like lines
    votes = Counter()
    hits = {}
    for title, pn, *_ in raw:
        keys = {_norm(title), _norm(re.sub(r"^(?:[ivxlc]+|\d+)\s+", "", title, flags=re.I))}
        keys.discard("")
        for p in pages[after:]:
            for ln in p.lines:
                n = _norm(ln.text)
                if n in keys or any(len(k) > 8 and n.startswith(k) for k in keys):
                    hits[title] = (p.index, ln.bbox[1])
                    if pn.isdigit():
                        votes[p.index - int(pn)] += 1
                    break
            if title in hits:
                break
    offset = votes.most_common(1)[0][0] if votes else after
    xs = sorted({round(r[2]) for r in raw})
    entries = []
    for title, pn, x0, size in raw:
        level = min(3, 1 + sum(1 for x in xs if x < round(x0) - size * 0.8))
        if pn.isdigit() and not (title in hits and hits[title][0] - int(pn) != offset):
            page, y = hits[title] if title in hits else (int(pn) + offset, 0.0)
        elif title in hits:                       # roman-numeral front matter, or an off-offset hit
            page, y = hits[title]
        else:
            continue
        if 0 <= page < len(pages):
            entries.append(TocEntry(title, level, page, y))
    if len(entries) < 3:
        return TocResult([], "none")
    return TocResult(entries, "visual", frozenset(toc_pages))


def discover(doc, pages) -> TocResult:
    entries = native_toc(doc)
    visual = visual_toc(doc, pages)
    if entries:
        return TocResult(entries, "bookmarks", visual.skip_pages)
    return visual


def from_headings(stream) -> list:
    """Last resort: synthesise entries from detected headings (largest level present)."""
    for kind, lvl in (("h1", 1), ("h2", 1), ("h3", 1)):
        hs = [it for it in stream if it.kind == kind]
        if len(hs) >= 2:
            return [TocEntry(re.sub(r"\s+", " ", strip_markers(h.text)).strip(), lvl, h.page, h.y0) for h in hs]
    return []
