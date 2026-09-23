"""Raw per-page extraction from a PDF using PyMuPDF (no option-dependent logic)."""
import re
from dataclasses import dataclass, field

import pymupdf as fitz

MIN_IMG_SIDE = 40  # points; smaller images are treated as decoration


@dataclass
class Line:
    text: str
    bbox: tuple            # x0, y0, x1, y1 in PDF points
    size: float            # dominant font size
    bold: bool
    italic: bool
    spans: list            # [(text, size, flags, bbox, origin_y)]
    block: int
    dir: tuple = (1.0, 0.0)   # writing direction; not (1,0) for rotated text
    tags: list = field(default_factory=list)   # e.g. ["header"], set by classify
    reason: str = ""


@dataclass
class ImageItem:
    bbox: tuple
    xref: int
    clip: tuple = None        # render only this rectangle of the page (figure regions)
    page: int = None          # set when this 'image' is a render of a whole page (figure page)
    rot: int = 0              # clockwise degrees to make the render upright


@dataclass
class PageData:
    index: int
    width: float
    height: float
    lines: list
    images: list
    has_text: bool
    figures: list = field(default_factory=list)    # vector-graphic regions (infographics, ruled tables)
    rules: list = field(default_factory=list)      # y of long horizontal rules that separate page bands


def span_is_sup(span, line_size, base_y):
    """span = (text, size, flags, bbox, origin_y)."""
    _, size, flags, _, oy = span
    return bool(flags & 1) or (size < line_size * 0.8 and oy < base_y - 0.8)


def _eff_flags(span):
    """PyMuPDF flags, plus bold/italic inferred from the font name when the flag bits are unset."""
    f, name = span["flags"], span["font"].lower()
    if any(k in name for k in ("italic", "oblique")):
        f |= 2
    if any(k in name for k in ("bold", "black", "heavy")):
        f |= 16
    return f


_NUM_ONLY = re.compile(r"^\s*(\[\d{1,3}\]|\d{1,3}[.)]?)\s*$")


def _merge_detached_markers(lines, page_h):
    """Fold lone digits that PyMuPDF split off (raised reference numbers, footnote-number cells)
    back into the line they belong to, flagged as superscript."""
    gone = set()
    for i, L in enumerate(lines):
        if not _NUM_ONLY.match(L.text):
            continue
        cy = (L.bbox[1] + L.bbox[3]) / 2
        for j, H in enumerate(lines):
            if j == i or j in gone or _NUM_ONLY.match(H.text):
                continue
            hcy = (H.bbox[1] + H.bbox[3]) / 2
            raised = (L.size < H.size * 0.85 and H.bbox[1] - 0.4 * H.size <= cy <= H.bbox[3] + 0.2 * H.size
                      and H.bbox[0] - 2 <= L.bbox[0] <= H.bbox[2] + 1.5 * H.size)
            punct = bool(re.search(r"[.)\]]", L.text))       # "1." / "[1]": a list or note marker, not a page number
            cell = ((cy / page_h > 0.5 or punct) and abs(cy - hcy) < 0.6 * H.size and L.size <= H.size * (1.7 if punct else 1.15)
                    and L.bbox[2] <= H.bbox[0] + 1 and H.bbox[0] - L.bbox[2] < 4 * H.size)
            if not (raised or cell):
                continue
            t, sz, fl, bb, oy = L.spans[0]
            sup = (L.text.strip(), sz, fl | 1, L.bbox, oy - 1.0)
            if L.bbox[0] < H.bbox[0]:
                spans = [sup, (" ", H.spans[0][1], 0, H.bbox, H.spans[0][4])] + H.spans
            else:
                spans = H.spans + [sup]
            H.spans = spans
            H.text = "".join(x[0] for x in spans)
            H.bbox = (min(H.bbox[0], L.bbox[0]), min(H.bbox[1], L.bbox[1]),
                      max(H.bbox[2], L.bbox[2]), max(H.bbox[3], L.bbox[3]))
            gone.add(i)
            break
    return [l for k, l in enumerate(lines) if k not in gone]


def _is_rule(d, W):
    r = d["rect"]
    return r.height <= 2.5 and r.width >= 0.4 * W


def _merge_close(rects, H):
    """Rows of one infographic are separate clusters: join those that sit close and overlap horizontally."""
    rects, merged = list(rects), True
    while merged:
        merged = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                gap = max(a.y0, b.y0) - min(a.y1, b.y1)
                xov = min(a.x1, b.x1) - max(a.x0, b.x0)
                if gap < 0.05 * H and xov > 0.4 * min(a.width, b.width):
                    rects[i] = a | b
                    del rects[j]
                    merged = True
                    break
            if merged:
                break
    return rects


def _drawing_info(page, lines):
    """-> (figure rects, rule ys).  Figures are clusters of vector drawings big enough to matter,
    excluding boxes that merely frame prose; rules are long thin horizontal lines between figures."""
    W, H = page.rect.width, page.rect.height
    try:
        drawings = page.get_drawings()
        if not drawings or len(drawings) > 5000:
            return [], []
        clusters = page.cluster_drawings(drawings=drawings)
    except Exception:
        return [], []

    def prose(r):
        inside = [l for l in lines if r.x0 <= (l.bbox[0] + l.bbox[2]) / 2 <= r.x1
                  and r.y0 <= (l.bbox[1] + l.bbox[3]) / 2 <= r.y1]
        return len(inside) >= 5 and sum(len(l.text) for l in inside) / len(inside) > 45

    def big_enough(r):
        return not (r.width < 0.15 * W or r.height < 0.04 * H or r.width * r.height > 0.85 * W * H)

    accepted = []
    for r in _merge_close([fitz.Rect(c) for c in clusters], H):
        if not big_enough(r):
            continue
        if not prose(r):
            accepted.append(r)
            continue
        if r.width > 0.6 * W:
            # page-wide rules glued a figure to the text around it: cluster again without them
            inner = [d for d in drawings if d["rect"].intersects(r) and not _is_rule(d, W)]
            try:
                sub = page.cluster_drawings(drawings=inner) if inner else []
            except Exception:
                sub = []
            accepted += [x for x in _merge_close([fitz.Rect(c) for c in sub], H) if big_enough(x) and not prose(x)]
    rules = []
    for d in drawings:
        if _is_rule(d, W):
            r = d["rect"]
            if not any(f.contains(r) for f in accepted):
                rules.append(round((r.y0 + r.y1) / 2, 1))
    return [(r.x0, r.y0, r.x1, r.y1) for r in accepted], sorted(set(rules))


def extract_page(doc: fitz.Document, index: int) -> PageData:
    page = doc[index]
    d = page.get_text("dict", flags=fitz.TEXT_PRESERVE_LIGATURES)
    lines = []
    for bi, blk in enumerate(d["blocks"]):
        if blk["type"] != 0:
            continue
        for ln in blk["lines"]:
            spans = [s for s in ln["spans"] if s["text"]]
            # split one PDF line into column cells where a wide horizontal gap separates spans
            groups, prev = [], None
            for s in spans:
                if prev is not None and s["text"].strip() and s["bbox"][0] - prev["bbox"][2] > 3 * s["size"]:
                    groups.append([])
                if not groups:
                    groups.append([])
                groups[-1].append(s)
                if s["text"].strip():
                    prev = s
            for g in groups:
                text = "".join(s["text"] for s in g)
                if not text.strip():
                    continue
                dom = max(g, key=lambda s: len(s["text"].strip()))
                bb = (min(s["bbox"][0] for s in g), ln["bbox"][1], max(s["bbox"][2] for s in g), ln["bbox"][3])
                lines.append(Line(
                    text=text, bbox=bb, size=round(dom["size"], 1),
                    bold=bool(dom["flags"] & 16) or "bold" in dom["font"].lower(),
                    italic=bool(dom["flags"] & 2) or "italic" in dom["font"].lower(),
                    spans=[(s["text"], s["size"], _eff_flags(s), tuple(s["bbox"]), s["origin"][1]) for s in g],
                    block=bi, dir=tuple(ln.get("dir", (1.0, 0.0))),
                ))
    lines = _merge_detached_markers(lines, page.rect.height)
    images = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        infos = []
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        if (x1 - x0) < MIN_IMG_SIDE or (y1 - y0) < MIN_IMG_SIDE or not info.get("xref"):
            continue
        images.append(ImageItem(bbox=(x0, y0, x1, y1), xref=info["xref"]))
    figures, rules = _drawing_info(page, lines)
    return PageData(index, page.rect.width, page.rect.height, lines, images, bool(lines), figures, rules)


def extract_all(doc):
    return [extract_page(doc, i) for i in range(len(doc))]


def load_image(doc, xref):
    """Return (bytes, ext) for web-safe embedding, or None if the image can't be read."""
    try:
        info = doc.extract_image(xref)
        if info and info["ext"] in ("png", "jpeg", "jpg", "gif") and not info.get("smask"):
            return info["image"], "jpg" if info["ext"] == "jpeg" else info["ext"]
        pix = fitz.Pixmap(doc, xref)
        if pix.colorspace is None or pix.colorspace.n != 3 or pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.tobytes("png"), "png"
    except Exception:
        return None
