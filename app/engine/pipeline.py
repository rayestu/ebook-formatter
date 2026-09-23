"""Orchestration: one PdfSession per uploaded file; preview and full conversion."""
import re
import threading
from pathlib import Path

import pymupdf

from . import toc as tocmod
from .build_html import Chapter, Notebook, chapter_body
from .classify import body_size, classify, running_keys
from .epub import build_epub
from .extract import extract_all, load_image
from .flow import Para, build_page, stitch
from .options import Options


def _slug_title(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip() or "Untitled"


class PdfSession:
    def __init__(self, path: Path, name: str = ""):
        self.path = Path(path)
        self.lock = threading.Lock()          # PyMuPDF documents are not thread-safe
        self.doc = pymupdf.open(str(self.path))
        self.pages = extract_all(self.doc)
        self.body = body_size(self.pages)
        self.rkeys = running_keys(self.pages)
        self.toc = tocmod.discover(self.doc, self.pages)
        meta = self.doc.metadata or {}
        self.title = (meta.get("title") or "").strip() or _slug_title(Path(name or self.path))
        self.author = (meta.get("author") or "").strip()
        self._png_cache = {}
        self.job = {"state": "idle", "progress": 0.0, "log": [], "file": None}

    # ---- rendering -------------------------------------------------------
    def page_png(self, n: int, zoom: float = 1.6) -> bytes:
        key = (n, zoom)
        if key not in self._png_cache:
            with self.lock:
                pix = self.doc[n].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                if len(self._png_cache) > 24:
                    self._png_cache.pop(next(iter(self._png_cache)))
                self._png_cache[key] = pix.tobytes("png")
        return self._png_cache[key]

    def render_figure(self, n: int, rot: int, clip=None, zoom: float = None) -> bytes:
        zoom = zoom or (2.5 if clip else 2.0)
        with self.lock:
            m = pymupdf.Matrix(zoom, zoom).prerotate(rot)
            rect = pymupdf.Rect(*clip) if clip else None
            return self.doc[n].get_pixmap(matrix=m, clip=rect).tobytes("png")

    def image_bytes(self, xref: int):
        with self.lock:
            return load_image(self.doc, xref)

    # ---- helpers ---------------------------------------------------------
    def _no_merge_pages(self, entries):
        """Pages where a top-level chapter starts near the top: never merge into the previous page."""
        if not entries:
            return set()
        top = min(e.level for e in entries)
        return {e.page for e in entries if e.level == top and e.y < self.pages[e.page].height * 0.3}

    def info(self):
        return {"pages": len(self.pages), "title": self.title, "toc_source": self.toc.source,
                "toc_entries": len(self.toc.entries),
                "text_pages": sum(p.has_text for p in self.pages),
                "repaired": bool(getattr(self.doc, "is_repaired", False))}

    # ---- preview ---------------------------------------------------------
    def preview(self, n: int, opts: Options, img_url):
        n = max(0, min(n, len(self.pages) - 1))
        page = self.pages[n]
        skipped = n in self.toc.skip_pages
        window = [self.pages[i] for i in range(max(0, n - 1), min(len(self.pages), n + 2))]
        kinds = classify(window, opts, self.rkeys, self.body)
        regions = [{"bbox": [b / d for b, d in zip(page.lines[i].bbox, (page.width, page.height) * 2)],
                    "kind": kind, "text": page.lines[i].text[:60]}
                   for i, kind in kinds[n].items()]
        results = [build_page(p, kinds[p.index], opts, self.body) for p in window
                   if p.index not in self.toc.skip_pages]
        mine = next((r for r in results if r.index == n), None)
        if mine:
            regions += [{"bbox": [b / d for b, d in zip(bb, (page.width, page.height) * 2)],
                         "kind": "footnote", "text": ""} for bb in mine.note_bboxes]
            regions += [{"bbox": [b / d for b, d in zip(bb, (page.width, page.height) * 2)],
                         "kind": "figure", "text": ""} for bb in mine.figure_boxes]
        if skipped:
            return {"page": n, "pages": len(self.pages), "regions": [],
                    "html": '<p class="muted">Contents page: replaced by the generated table of contents.</p>',
                    "merged": {"from_prev": False, "to_next": False}, "warning": ""}
        stream = stitch(results, opts, self._no_merge_pages(self.toc.entries))
        items = [it for it in stream if n in it.pages]
        nb = Notebook({(r.index, k): v for r in results for k, v in r.notes.items()})
        merged = {"from_prev": any(min(it.pages) < n for it in items),
                  "to_next": any(max(it.pages) > n for it in items)}

        def mark(it):
            if len(it.pages) < 2:
                return ""
            return "cross " + ("cross-next" if max(it.pages) > n else "cross-prev")

        html = chapter_body(Chapter("", items), nb, img_url, mark, show_notes=False)
        warn = "" if page.has_text else "No extractable text on this page (scanned image?)."
        if mine and mine.figure:
            warn = "Figure page (rotated or unreadable text): kept as an upright image."
        return {"page": n, "pages": len(self.pages), "regions": regions, "html": html,
                "merged": merged, "warning": warn,
                "footnotes": len(mine.notes) if mine else 0}

    # ---- conversion ------------------------------------------------------
    def _assign_chapters(self, stream, entries, opts):
        pos = []
        for i, e in enumerate(entries):
            k = next((k for k, it in enumerate(stream) if (it.page, it.y0) >= (e.page, e.y - 8)), None)
            if k is not None:
                pos.append((i, k))
        pos.sort(key=lambda t: t[1])            # nav follows document order, not bookmark order
        chapters = []
        if opts.chapters and pos:
            top = min(entries[i].level for i, _ in pos)
            starts = {}
            for i, k in pos:
                if entries[i].level == top:
                    starts.setdefault(k, i)
            cuts = sorted(starts)
            bounds = ([0] if cuts[0] > 0 else []) + cuts
            for b, start in enumerate(bounds):
                end = bounds[b + 1] if b + 1 < len(bounds) else len(stream)
                title = entries[starts[start]].title if start in starts else "Front Matter"
                chapters.append(Chapter(title, stream[start:end]))
        else:
            chapters.append(Chapter(self.title, list(stream)))
        # anchors + visible heading where the chapter's first item is not a heading
        toc_rows = []
        for ch_no, ch in enumerate(chapters, 1):
            ch.filename = f"chapter_{ch_no}.xhtml"
            if ch.items and ch.title != "Front Matter" and ch.title != self.title and \
                    not ch.items[0].kind.startswith("h") and self.toc.source != "headings":
                first = ch.items[0]
                ch.items.insert(0, Para("h1", ch.title, first.page, first.y0, first.y0, pages={first.page}))
        where = {id(it): ch for ch in chapters for it in ch.items}
        for i, k in pos:
            it = stream[k]
            ch = where.get(id(it))
            if not ch:
                continue
            target = ch.items[0] if (ch.items[0] is not it and ch.items[0].kind == "h1" and
                                     ch.items[0].page == it.page and ch.items[0].y0 == it.y0) else it
            ch.anchors.setdefault(id(target), []).append(f"chapter_{i}")
            toc_rows.append((entries[i].level, entries[i].title, f"{ch.filename}#chapter_{i}"))
        return chapters, toc_rows

    def convert(self, opts: Options, out_path: Path):
        job = self.job
        log = lambda m: job["log"].append(m)
        job.update(state="running", progress=0.0, log=[], file=None)
        try:
            log(f"Converting {len(self.pages)} pages ...")
            if not any(p.has_text for p in self.pages):
                log("Warning: no extractable text found (scanned PDF?). The EPUB will contain no text.")
            kinds = classify(self.pages, opts, self.rkeys, self.body)
            stripped = sum(len(v) for v in kinds.values())
            log(f"Stripped {stripped} header/footer/page-number lines.")
            results = []
            for k, p in enumerate(self.pages):
                if p.index not in self.toc.skip_pages:
                    if not p.has_text and not p.images:
                        log(f"Page {p.index + 1}: no text (scanned?) — skipped.")
                    results.append(build_page(p, kinds[p.index], opts, self.body))
                job["progress"] = 0.7 * (k + 1) / len(self.pages)
            entries = list(self.toc.entries)
            if self.toc.skip_pages:
                log(f"Excluded contents page(s) {sorted(x + 1 for x in self.toc.skip_pages)}.")
            stream = stitch(results, opts, self._no_merge_pages(entries))
            merges = sum(1 for it in stream if len(it.pages) > 1)
            log(f"Merged {merges} paragraph(s) across page breaks.")
            if not entries and opts.chapters:
                entries = tocmod.from_headings(stream)
                self.toc.source = "headings" if entries else "none"
            log(f"TOC source: {self.toc.source} ({len(entries)} entries).")
            chapters, toc_rows = self._assign_chapters(stream, entries, opts)
            nb = Notebook({(r.index, k): v for r in results for k, v in r.notes.items()})
            images = {}

            def img_src(im):
                if im.page is not None:
                    name = (f"fig_{im.page + 1}_{int(im.clip[1])}_{int(im.clip[0])}.png" if im.clip
                            else f"page_{im.page + 1}_r{im.rot}.png")
                    if name not in images:
                        images[name] = self.render_figure(im.page, im.rot, im.clip)
                    return f"images/{name}"
                name = f"img_{im.xref}"
                for ext in ("jpg", "png", "gif"):
                    if f"{name}.{ext}" in images:
                        return f"images/{name}.{ext}"
                got = self.image_bytes(im.xref)
                if not got:
                    return None
                images[f"{name}.{got[1]}"] = got[0]
                return f"images/{name}.{got[1]}"

            bodies = []
            for k, ch in enumerate(chapters):
                bodies.append((ch.title, ch.filename, chapter_body(ch, nb, img_src)))
                job["progress"] = 0.7 + 0.25 * (k + 1) / len(chapters)
            log(f"{len(chapters)} chapter(s), {len(images)} image(s), {nb.counter} footnote(s).")
            build_epub(out_path, self.title, self.author, bodies, toc_rows, images)
            job.update(state="done", progress=1.0, file=str(out_path))
            log("Done.")
        except Exception as exc:  # surface to UI
            job.update(state="error")
            log(f"Error: {exc!r}")
            raise
