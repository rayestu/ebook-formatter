"""Lines -> paragraphs/headings/images, de-hyphenation, cross-page merging."""
import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

from .extract import ImageItem
from .footnotes import BRACKET_REF_RE, SUPDIG, SUPRUN_RE, find_notes

REF_OPEN, REF_CLOSE = "\ue000", "\ue001"
ITAL_O, ITAL_C, BOLD_O, BOLD_C = "\ue002", "\ue003", "\ue004", "\ue005"   # inline style markers
STYLE_RE = re.compile("[\ue002-\ue005]")
TERMINAL_RE = re.compile(r"[.!?:][\"'”’)\]»*]*$")
LIST_RE = re.compile(
    r"^\s*(?:(?P<bul>[•▪◦‣●\-–])|(?P<num>\d{1,3})[.)]|\((?P<pnum>\d{1,3})\)|(?P<let>[A-Za-z])\)|\((?P<plet>[a-z])\))\s+")


def list_marker(text):
    """-> (ordered, number, style, match_end) for a list-item start, else None."""
    m = LIST_RE.match(text)
    if not m:
        return None
    g = m.groupdict()
    if g["bul"]:
        return (False, None, None, m.end())
    if g["num"] or g["pnum"]:
        return (True, int(g["num"] or g["pnum"]), "1", m.end())
    c = g["let"] or g["plet"]
    return (True, ord(c.lower()) - 96, "a" if c.islower() else "A", m.end())
KEEP_HYPHEN = {"self", "well", "ex", "non", "half", "semi", "all", "cross", "anti"}


@dataclass
class Para:
    kind: str                  # p | h1 | h2 | h3 | li | img
    text: str
    page: int
    y0: float
    y1: float
    size: float = 0.0
    indented: bool = False
    pages: set = field(default_factory=set)
    img: Optional[ImageItem] = None
    marker: Optional[tuple] = None                # list items: (ordered, number, style)
    entries: list = field(default_factory=list)   # kind == 'dl': [[term, definition], ...]
    page_h: float = 1.0


@dataclass
class PageResult:
    index: int
    items: list
    notes: dict                # n -> text
    note_bboxes: list = field(default_factory=list)   # removed footnote lines (for previews)
    figure: bool = False       # whole page emitted as one image (rotated / unreadable text)
    figure_boxes: list = field(default_factory=list)   # vector figures kept as pictures (for previews)


def strip_markers(t: str) -> str:
    return STYLE_RE.sub("", re.sub(REF_OPEN + r"[^" + REF_CLOSE + r"]*" + REF_CLOSE, "", t))


def is_terminal(t: str) -> bool:
    return bool(TERMINAL_RE.search(strip_markers(t).rstrip()))


def join_text(a: str, b: str, dehyphenate: bool = True) -> str:
    a, b = a.rstrip(), b.lstrip()
    for o, c in ((ITAL_O, ITAL_C), (BOLD_O, BOLD_C)):      # fuse a style run that wraps across lines
        if a.endswith(c) and b.startswith(o):
            a, b = a[:-1], b[1:]
    if not a:
        return b
    if not b:
        return a
    if a.endswith("­"):
        return a[:-1] + b
    m = re.search(r"([^\W\d_]+)-$", a)
    if m and not a.endswith(" -"):
        if not dehyphenate:
            return a + b
        if b[0].islower() and m.group(1).lower() not in KEEP_HYPHEN:
            return a[:-1] + b
        return a + b                     # keep genuine compound: "Anglo-Saxon"
    return a + " " + b


DASH_RE = re.compile(r"(?<!-)\s*--\s*(?!-)")


def normalize_dashes(text: str) -> str:
    """Typewriter-convention double hyphens ("word -- word" or "word--word") become a proper
    em dash, the way a typeset book renders them. A run of 3+ hyphens (a section divider) is
    left alone."""
    return DASH_RE.sub("\u2014", text)


def styled(ln, page_index=0, note_nums=(), bold_ok=True, ital_ok=True):
    """Line text with footnote-ref markers and bold/italic markers substituted."""
    base = max(s[4] for s in ln.spans)
    big = max(s[1] for s in ln.spans)
    pieces = []                                   # [text, style]; style None = already-final marker text
    for i, (txt, size, flags, _bb, oy) in enumerate(ln.spans):
        t = txt.strip()
        ref = None
        if note_nums:
            if i > 0 and t.isdigit() and len(t) <= 3 and (flags & 1 or (size < big * 0.85 and oy < base - 0.8)) \
                    and int(t) in note_nums:
                ref = int(t)
            elif t and all(c in "⁰¹²³⁴⁵⁶⁷⁸⁹" for c in t) and int(t.translate(SUPDIG)) in note_nums:
                ref = int(t.translate(SUPDIG))
        if ref is not None:
            pieces.append([f"{REF_OPEN}{page_index}:{ref}{REF_CLOSE}", None])
            continue
        st = (bool(flags & 16) and bold_ok, bool(flags & 2) and ital_ok)
        if not t and pieces and pieces[-1][1] is not None:
            st = pieces[-1][1]                    # blank spans inherit the run they sit in
        if pieces and pieces[-1][1] == st and st is not None:
            pieces[-1][0] += txt
        else:
            pieces.append([txt, st])
    out = []
    for txt, st in pieces:
        core = txt.strip()
        if st is None or not core or not (st[0] or st[1]):
            out.append(txt)
            continue
        lead, trail = txt[:len(txt) - len(txt.lstrip())], txt[len(txt.rstrip()):]
        if st[1]:
            core = ITAL_O + core + ITAL_C
        if st[0]:
            core = BOLD_O + core + BOLD_C
        out.append(lead + core + trail)
    text = "".join(out)
    if note_nums:
        text = SUPRUN_RE.sub(
            lambda m: f"{REF_OPEN}{page_index}:{int(m.group().translate(SUPDIG))}{REF_CLOSE}"
            if int(m.group().translate(SUPDIG)) in note_nums else m.group(), text)
        text = BRACKET_REF_RE.sub(
            lambda m: f"{REF_OPEN}{page_index}:{m.group(1)}{REF_CLOSE}" if int(m.group(1)) in note_nums else m.group(0), text)
    return normalize_dashes(text)


def _style_ok(page):
    """Ignore a style that covers nearly the whole page (it is the body face, not emphasis)."""
    tot = bold = ital = 0
    for ln in page.lines:
        for txt, _sz, fl, *_ in ln.spans:
            n = len(txt.strip())
            tot += n
            bold += n * bool(fl & 16)
            ital += n * bool(fl & 2)
    return not (tot and bold > 0.85 * tot), not (tot and ital > 0.85 * tot)


CAPTION_RE = re.compile(r"^\s*(map|figure|fig\.|chart|plate|diagram|table)\s*\d", re.I)


def drop_list_marker(text: str) -> str:
    """Remove a leading list marker from styled text, keeping the style markers balanced."""
    plain = STYLE_RE.sub("", text)
    m = LIST_RE.match(plain)
    if not m:
        return text
    n, out = m.end(), []
    for ch in text:
        if n > 0 and not ("\ue000" <= ch <= "\ue007"):
            n -= 1
            continue
        out.append(ch)
    res = "".join(out)
    return re.sub("[\ue002\ue004]\\s*[\ue003\ue005]", "", res).lstrip()


def is_garbage(text: str) -> bool:
    """Text PyMuPDF could not decode (U+FFFD placeholders) — usually labels inside figures."""
    t = re.sub(r"\s+", "", text)
    return bool(t) and (t.count("\ufffd") + t.count("\x00")) > 0.5 * len(t)


def _upright_rotation(dirs):
    """Clockwise degrees that make the dominant (non-horizontal) text direction read left-to-right."""
    from collections import Counter
    (dx, dy), _ = Counter((round(d[0]), round(d[1])) for d in dirs).most_common(1)[0]
    return {(0, -1): 90, (0, 1): 270, (-1, 0): 180}.get((dx, dy), 0)


def _heading_level(size, body):
    r = size / body if body else 1
    if r >= 1.6:
        return 1
    if r >= 1.3:
        return 2
    if r >= 1.15:
        return 3
    return 0


def _glossary(lines, dehyph):
    """Detect a term | definition table. lines: [(idx, Line)].
    Returns (set of line idx consumed, Para-less entries list, y0, y1) or None."""
    rows = []
    for i, ln in sorted(lines, key=lambda t: ((t[1].bbox[1] + t[1].bbox[3]) / 2, t[1].bbox[0])):
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows and abs(rows[-1][0] - cy) < ln.size * 0.5:
            rows[-1][1].append((i, ln))
        else:
            rows.append([cy, [(i, ln)]])
    pairs = []
    for _, r in rows:
        if len(r) == 2 and r[0][1].bbox[2] + 1.5 * r[0][1].size < r[1][1].bbox[0]:
            pairs.append((r[0], r[1]))
    if len(pairs) < 3:
        return None
    from collections import Counter
    bx = Counter(round(p[1][1].bbox[0] / 3) for p in pairs).most_common(1)[0]
    ax = Counter(round(p[0][1].bbox[0] / 3) for p in pairs).most_common(1)[0]
    if bx[1] < 0.6 * len(pairs) or ax[1] < 0.6 * len(pairs):
        return None
    ratios = [(p[0][1].bbox[2] - p[0][1].bbox[0]) / max(1.0, p[1][1].bbox[0] - p[0][1].bbox[0]) for p in pairs]
    if statistics.median(ratios) > 0.5:
        return None                      # left cells are full-width lines: two-column prose, not terms
    isA = lambda ln: abs(round(ln.bbox[0] / 3) - ax[0]) <= 1
    isB = lambda ln: abs(round(ln.bbox[0] / 3) - bx[0]) <= 1
    entries, used, cur = [], set(), None
    y0 = y1 = None
    for _, r in rows:
        if len(r) == 2 and isA(r[0][1]) and isB(r[1][1]):
            cur = [styled(r[0][1]).strip(), styled(r[1][1]).strip()]
            entries.append(cur)
            used |= {r[0][0], r[1][0]}
        elif len(r) == 1 and isB(r[0][1]) and (cur is not None or not entries):
            if cur is None:                      # continuation from the previous page
                cur = ["", ""]
                entries.append(cur)
            cur[1] = join_text(cur[1], styled(r[0][1]), dehyph)
            used.add(r[0][0])
        elif len(r) == 1 and isA(r[0][1]) and not is_terminal(r[0][1].text) \
                and len(r[0][1].text) < 40 and entries:
            cur = [styled(r[0][1]).strip(), ""]     # term with no definition on this row
            entries.append(cur)
            used.add(r[0][0])
        else:
            continue
        ys = [ln.bbox for _, ln in r]
        y0 = min([y0] + [b[1] for b in ys]) if y0 is not None else min(b[1] for b in ys)
        y1 = max([y1 or 0] + [b[3] for b in ys])
    return used, entries, y0, y1


def _rows(lines):
    rows = []
    for i, ln in sorted(lines, key=lambda t: ((t[1].bbox[1] + t[1].bbox[3]) / 2, t[1].bbox[0])):
        cy = (ln.bbox[1] + ln.bbox[3]) / 2
        if rows and abs(rows[-1][0] - cy) < ln.size * 0.5:
            rows[-1][1].append((i, ln))
        else:
            rows.append([cy, [(i, ln)]])
    return [sorted(r[1], key=lambda t: t[1].bbox[0]) for r in rows]


_NUMERIC = re.compile(r"[\s$€£%+\-−–(),.\d/]+")


def _tables(lines):
    """Runs of >=3 rows with the same >=3 aligned short cells -> [(used, rows, header, y0, y1)]."""
    out, run = [], []

    def flush():
        nonlocal run
        if len(run) >= 3:
            n = len(run[0])
            texts = [[styled(ln).strip() for _, ln in r] for r in run]
            lens = [len(strip_markers(t)) for row in texts for t in row]
            aligned = all(
                max(r[j][1].bbox[0] for r in run) - min(r[j][1].bbox[0] for r in run) <= 8
                or max(r[j][1].bbox[2] for r in run) - min(r[j][1].bbox[2] for r in run) <= 8
                for j in range(n))
            if lens and sum(lens) / len(lens) <= 28 and max(lens) <= 60 and aligned:
                isnum = lambda t: bool(_NUMERIC.fullmatch(strip_markers(t)))
                header = not any(isnum(t) for t in texts[0]) and any(isnum(t) for row in texts[1:] for t in row)
                boxes = [ln.bbox for r in run for _, ln in r]
                out.append(({i for r in run for i, _ in r}, texts, header,
                            min(b[1] for b in boxes), max(b[3] for b in boxes)))
        run = []

    for r in _rows(lines):
        ok = len(r) >= 3 and all(r[k + 1][1].bbox[0] - r[k][1].bbox[2] > 0.8 * r[k][1].size for k in range(len(r) - 1))
        if ok and run and len(r) == len(run[0]):
            run.append(r)
        else:
            flush()
            if ok:
                run.append(r)
    flush()
    return out


def _find_gutter(entries):
    """x of the gutter between two text columns, or None for a single-column page."""
    lines = [e for e in entries if e[1] == "line"]
    if len(lines) < 10:
        return None
    x0 = min(e[0][0] for e in lines)
    w = max(e[0][2] for e in lines) - x0
    if w < 150:
        return None
    scored = []
    for k in range(31):
        g = x0 + w * (0.35 + 0.30 * k / 30)
        scored.append((sum(1 for e in lines if e[0][0] < g - 2 and e[0][2] > g + 2), g))
    best = min(sc for sc, _ in scored)
    cands = [g for sc, g in scored if sc == best]
    g = cands[len(cands) // 2]
    L = [e for e in lines if e[0][2] <= g + 2]
    R = [e for e in lines if e[0][0] >= g - 2]
    if len(L) < 8 or len(R) < 8 or best > 0.35 * len(lines):
        return None
    avg = lambda es: sum(e[0][2] - e[0][0] for e in es) / len(es)
    if avg(L) < 0.3 * w or avg(R) < 0.3 * w:
        return None                                # short cells: a table / glossary, not columns
    ly = (min(e[0][1] for e in L), max(e[0][3] for e in L))
    ry = (min(e[0][1] for e in R), max(e[0][3] for e in R))
    if min(ly[1], ry[1]) - max(ly[0], ry[0]) < 0.5 * min(ly[1] - ly[0], ry[1] - ry[0]):
        return None
    return g


def _order_band(band, g, page_w):
    """Order the entries of one horizontal band: the whole left side first, then the right side."""
    if len(band) < 2:
        return band
    key = lambda e: (e[0][1], e[0][0])
    if g is not None:
        def side(e):
            b = e[0]
            if b[2] <= g + 2:
                return 0
            if b[0] >= g - 2:
                return 1
            return 0 if g - b[0] >= b[2] - g else 1     # narrow item across the gutter: larger half wins
        L = [e for e in band if side(e) == 0]
        R = [e for e in band if side(e) == 1]
        return sorted(L, key=key) + sorted(R, key=key)
    spans = sorted((e[0][0], e[0][2]) for e in band)      # no page-wide gutter: look for one inside this band
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a > merged[-1][1]:
            merged.append([a, b])
        else:
            merged[-1][1] = max(merged[-1][1], b)
    gaps = [(merged[k + 1][0] - merged[k][1], (merged[k + 1][0] + merged[k][1]) / 2) for k in range(len(merged) - 1)]
    if not gaps:
        return band
    gap, gx = max(gaps)
    if gap < 0.03 * page_w:
        return band
    L = [e for e in band if e[0][2] <= gx]
    R = [e for e in band if e[0][0] >= gx]
    if not L or not R:
        return band
    ly = (min(e[0][1] for e in L), max(e[0][3] for e in L))
    ry = (min(e[0][1] for e in R), max(e[0][3] for e in R))
    if min(ly[1], ry[1]) - max(ly[0], ry[0]) < 0.3 * min(ly[1] - ly[0], ry[1] - ry[0]):
        return band                                      # side by side in name only
    return sorted(L, key=key) + sorted(R, key=key)


def _reading_order(entries, rules=(), page_w=600.0):
    """entries: [(bbox, tag, obj)].  Bands are separated by full-width items (headings, pictures) and by
    horizontal rules; inside a band the left side is read before the right side, then we move down.
    Returns (ordered entries, gutter_x | None)."""
    g = _find_gutter(entries)
    full = []
    if g is not None:
        lines = [e for e in entries if e[1] == "line"]
        w = max(e[0][2] for e in lines) - min(e[0][0] for e in lines)
        full = [e for e in entries if e[0][0] < g - 2 and e[0][2] > g + 2
                and (e[1] == "line" or e[0][2] - e[0][0] >= 0.6 * w)]
    fullset = {id(e) for e in full}
    rest = [e for e in entries if id(e) not in fullset]
    seps = sorted([(y, None) for y in rules] + [(e[0][1], e) for e in full], key=lambda t: t[0])
    pos = lambda e: e[0][1] if e[1] == "node" else (e[0][1] + e[0][3]) / 2
    out, cursor = [], -1e9
    for y, e in seps + [(1e9, None)]:
        out += _order_band([x for x in rest if cursor <= pos(x) < y], g, page_w)
        if e is not None:
            out.append(e)
        cursor = y
    return out, g


def _pull_quotes(lines, body):
    """Very large text with body text on both of its sides (a magazine pull quote) -> [(rect, text, {idx})]."""
    big = sorted(((i, ln) for i, ln in lines if ln.size >= 1.4 * body), key=lambda t: (t[1].bbox[1], t[1].bbox[0]))
    groups = []
    for i, ln in big:
        for g in groups:
            gb = g["bbox"]
            xov = min(gb[2], ln.bbox[2]) - max(gb[0], ln.bbox[0])
            if -0.5 * ln.size < ln.bbox[1] - gb[3] < 1.0 * ln.size and xov > 0.3 * min(gb[2] - gb[0], ln.bbox[2] - ln.bbox[0]):
                g["lines"].append((i, ln))
                g["bbox"] = (min(gb[0], ln.bbox[0]), min(gb[1], ln.bbox[1]), max(gb[2], ln.bbox[2]), max(gb[3], ln.bbox[3]))
                break
        else:
            groups.append({"lines": [(i, ln)], "bbox": ln.bbox})
    out = []
    for g in groups:
        text = " ".join(ln.text.strip() for _, ln in g["lines"])
        if len(text) < 15:
            continue                                   # a drop cap, not a quote
        b, used = g["bbox"], {i for i, _ in g["lines"]}
        left = right = 0
        for i, ln in lines:
            if i in used or abs(ln.size - body) > 0.8:
                continue
            oy = min(ln.bbox[3], b[3]) - max(ln.bbox[1], b[1])
            if oy < 0.5 * (ln.bbox[3] - ln.bbox[1]):
                continue
            if ln.bbox[2] <= b[0] + 2:
                left += 1
            elif ln.bbox[0] >= b[2] - 2:
                right += 1
        if left and right:
            out.append(((b[0] - 4, b[1] - 4, b[2] + 4, b[3] + 4), text, used))
    return out


def _grow_figure(rect, page, body):
    """Extend a drawing cluster upwards over its headline; return (rect, headline_text)."""
    x0, y0, x1, y1 = rect
    heading = ""
    for ln in page.lines:
        b = ln.bbox
        if b[3] <= y0 + 2 and y0 - b[3] < 2.5 * ln.size and ln.size >= 1.15 * body \
                and min(x1, b[2]) - max(x0, b[0]) > 0.4 * (b[2] - b[0]):
            y0, heading = min(y0, b[1]), ln.text.strip()
    for ln in page.lines:                          # lines that spill slightly out of the cluster
        b = ln.bbox
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            x0, y0, x1, y1 = min(x0, b[0]), min(y0, b[1]), max(x1, b[2]), max(y1, b[3])
    return (x0, y0, x1, y1), heading


def build_page(page, kinds: dict, opts, body: float, skip_images=False) -> PageResult:
    removed = set(kinds)
    notes, note_boxes = {}, []
    note_nums = set()
    if opts.footnotes:
        fn_removed, found = find_notes(page, kinds, body, lambda a, b: join_text(a, b, opts.dehyphenate))
        removed |= set(fn_removed)
        notes = {n.n: n.text for n in found}
        note_boxes = [page.lines[i].bbox for i in fn_removed]
        note_nums = set(notes)

    live = [(i, ln) for i, ln in enumerate(page.lines) if i not in removed]
    bad = [(i, ln) for i, ln in live if is_garbage(ln.text) or ln.dir[0] < 0.9]
    if len(live) >= 3 and len(bad) >= 0.5 * len(live):
        # a map / rotated figure: emit the page itself as an upright picture
        rot_lines = [ln for _, ln in live if ln.dir[0] < 0.9 and not is_garbage(ln.text)]
        rot = _upright_rotation([ln.dir for ln in rot_lines]) if rot_lines else 0
        caps = sorted((ln for ln in rot_lines if CAPTION_RE.match(ln.text)), key=lambda l: l.bbox[0])
        alt = " ".join(ln.text.strip() for ln in caps) or "Figure"
        pic = ImageItem(bbox=(0, 0, page.width, page.height), xref=0, page=page.index, rot=rot)
        node = Para("img", alt, page.index, 0, page.height, pages={page.index}, img=pic, page_h=page.height)
        return PageResult(page.index, [node], {}, [], figure=True)
    removed |= {i for i, ln in bad}          # stray unreadable lines on an ordinary page

    # ---- vector figures (infographics, ruled tables) become pictures, labels included
    inline, fig_boxes = [], []
    for fr in page.figures:
        rect, heading = _grow_figure(fr, page, body)
        removed |= {i for i, ln in enumerate(page.lines)
                    if rect[0] <= (ln.bbox[0] + ln.bbox[2]) / 2 <= rect[2] and rect[1] <= (ln.bbox[1] + ln.bbox[3]) / 2 <= rect[3]}
        pic = ImageItem(bbox=rect, xref=0, page=page.index, clip=rect)
        inline.append((rect, Para("img", heading or "Figure", page.index, rect[1], rect[3],
                                  pages={page.index}, img=pic, page_h=page.height)))
        fig_boxes.append(rect)

    if not skip_images:
        page_area = page.width * page.height
        for im in page.images:
            x0, y0, x1, y1 = im.bbox
            if (x1 - x0) * (y1 - y0) > 0.8 * page_area and page.has_text:
                continue                        # full-page scan background under OCR text
            cy = (y0 + y1) / 2 / page.height * 100
            if cy < opts.top_for(page.index) or cy > 100 - opts.bottom_for(page.index):
                continue
            if any(f[0] <= (x0 + x1) / 2 <= f[2] and f[1] <= (y0 + y1) / 2 <= f[3] for f in fig_boxes):
                continue                        # already part of a figure picture
            inline.append((im.bbox, Para("img", "", page.index, y0, y1, pages={page.index}, img=im, page_h=page.height)))

    live_lines = [(i, ln) for i, ln in enumerate(page.lines) if i not in removed]
    for rect, qtext, used in _pull_quotes(live_lines, body):          # pull quotes are treated as pictures
        removed |= used
        pic = ImageItem(bbox=rect, xref=0, page=page.index, clip=rect)
        inline.append((rect, Para("img", qtext, page.index, rect[1], rect[3], pages={page.index}, img=pic,
                                  page_h=page.height)))
        fig_boxes.append(rect)
    lines = [(i, ln) for i, ln in enumerate(page.lines) if i not in removed]
    items, extras = [], []
    bold_ok, ital_ok = _style_ok(page)
    gloss = _glossary(lines, opts.dehyphenate) if lines else None
    if gloss:
        used, g_entries, gy0, gy1 = gloss
        lines = [(i, ln) for i, ln in lines if i not in used]
        extras.append(Para("dl", "", page.index, gy0, gy1, pages={page.index}, entries=g_entries, page_h=page.height))
    for used, rows, header, ty0, ty1 in (_tables(lines) if lines else []):
        lines = [(i, ln) for i, ln in lines if i not in used]
        extras.append(Para("table", "header" if header else "", page.index, ty0, ty1, pages={page.index},
                           entries=rows, page_h=page.height))

    if lines or inline:
        entries = [(ln.bbox, "line", (i, ln)) for i, ln in lines]
        for b, n in sorted(inline, key=lambda t: t[0][1]):        # pictures go where they sit on the page
            idx = next((k for k, e in enumerate(entries) if e[1] == "line" and e[0][1] > b[1] + 1), len(entries))
            entries.insert(idx, (b, "node", n))
        ordered, gutter = _reading_order(entries, page.rules, page.width)
        body_lines = [(ln.bbox) for _, ln in lines if abs(ln.size - body) < 0.6] or [ln.bbox for _, ln in lines]

        def col_metrics(sel):
            xs = [b[0] for b in sel]
            return (statistics.median(xs), max(b[2] for b in sel)) if xs else (0.0, page.width)

        if gutter is None:
            single = col_metrics(body_lines)
            metrics = lambda ln: single
        else:
            lm = col_metrics([b for b in body_lines if b[2] <= gutter + 2]) if any(b[2] <= gutter + 2 for b in body_lines) else col_metrics(body_lines)
            rm = col_metrics([b for b in body_lines if b[0] >= gutter - 2]) if any(b[0] >= gutter - 2 for b in body_lines) else col_metrics(body_lines)
            metrics = lambda ln: lm if (ln.bbox[0] + ln.bbox[2]) / 2 < gutter else rm
        cur, prev = None, None

        def hkey(ln):
            return _heading_level(ln.size, body)

        for _, tag, obj in ordered:
            if tag == "node":
                items.append({"node": obj})
                cur = prev = None
                continue
            _, ln = obj
            left, right = metrics(ln)
            text = styled(ln, page.index, note_nums, bold_ok, ital_ok)
            lvl = hkey(ln)
            mk = None if lvl else list_marker(ln.text)
            bullet = bool(mk)
            new = prev is None
            if prev is not None:
                a = prev
                gap = ln.bbox[1] - a.bbox[3]
                a_left = metrics(a)[0]
                jump = ln.bbox[1] < a.bbox[1] - 0.5 * a.size            # next column / band
                col_cont = (jump and gutter is not None
                            and (a.bbox[0] + a.bbox[2]) / 2 < gutter < (ln.bbox[0] + ln.bbox[2]) / 2 and lvl == 0
                            and not bullet and not is_terminal(a.text) and hkey(a) == 0)
                indent = ln.bbox[0] - left > 0.9 * ln.size and a.bbox[0] - a_left <= 0.9 * ln.size
                in_item = bool(cur["mk"])
                hanging = in_item and ln.bbox[0] - left > 0.9 * ln.size       # wrapped list line
                if col_cont:
                    new = False
                else:
                    if in_item and not hanging and not bullet and gap > -0.5 * a.size and lvl == 0 \
                            and ln.bbox[0] - left <= 0.9 * ln.size and (is_terminal(a.text) or gap > 0.15 * a.size):
                        new = True                  # back at the margin: list has ended
                    if (bullet or hkey(a) != lvl or (lvl and abs(a.size - ln.size) > 0.5)
                            or gap > 0.4 * a.size or jump
                            or (indent and lvl == 0 and not in_item)
                            or (a.block != ln.block and is_terminal(a.text))
                            or (is_terminal(a.text) and a.bbox[2] < metrics(a)[1] - 4 * a.size and lvl == 0)):
                        new = True
            if new:
                cur = {"lines": [ln], "text": text, "lvl": lvl, "bullet": bullet, "mk": mk,
                       "indented": ln.bbox[0] - left > 0.9 * ln.size}
                items.append(cur)
            else:
                cur["lines"].append(ln)
                cur["text"] = join_text(cur["text"], text, opts.dehyphenate)
            prev = ln

        paras = []
        for c in items:
            if "node" in c:
                paras.append(c["node"])
                continue
            ls = c["lines"]
            lvl, text = c["lvl"], c["text"].strip()
            kind = "p"
            if lvl and len(ls) <= 4 and len(strip_markers(text)) <= 200:
                kind = f"h{lvl}"
            elif (len(ls) == 1 and ls[0].bold and len(strip_markers(text)) <= 80 and ls[0].size >= body * 0.95
                  and not is_terminal(text) and not c["mk"]):
                kind = "h3"
            elif c["mk"]:
                kind = "li"
                text = drop_list_marker(text)
            if kind.startswith("h"):
                text = text.replace(BOLD_O, "").replace(BOLD_C, "")
            paras.append(Para(kind, text, page.index, ls[0].bbox[1], ls[-1].bbox[3], ls[0].size,
                              c["indented"], {page.index}, page_h=page.height,
                              marker=c["mk"][:3] if c["mk"] and kind == "li" else None))
        items = paras
    for node in extras:                       # glossary / table blocks slot in by vertical position
        idx = next((i for i, it in enumerate(items) if it.y0 > node.y0), len(items))
        items.insert(idx, node)
    return PageResult(page.index, items, notes, note_boxes, figure_boxes=fig_boxes)


def stitch(results, opts, no_merge_pages=frozenset()):
    """Concatenate page results, merging paragraphs split by a page break."""
    stream = []
    for pr in results:
        for k, it in enumerate(pr.items):
            prev = stream[-1] if stream else None
            if k == 0 and it.kind == "dl" and prev and prev.kind == "dl" and max(prev.pages) == it.page - 1:
                if it.entries and not it.entries[0][0] and prev.entries:     # definition wrapped onto this page
                    prev.entries[-1][1] = join_text(prev.entries[-1][1], it.entries[0][1], opts.dehyphenate)
                    it.entries = it.entries[1:]
                    if not it.entries:
                        continue
            if (k == 0 and prev and it.kind == "p" and prev.kind in ("p", "li")
                    and (not it.indented or prev.kind == "li")
                    and it.page not in no_merge_pages and max(prev.pages) == it.page - 1
                    and (prev.y1 / prev.page_h > 0.55 or re.search(r"[^\W\d_]-$", strip_markers(prev.text)))
                    and not is_terminal(prev.text)):
                prev.text = join_text(prev.text, it.text, opts.dehyphenate)
                prev.pages |= it.pages
                prev.y1, prev.page_h = it.y1, it.page_h
                continue
            stream.append(it)
    return stream
