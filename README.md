# ebook-formatter

Turn a PDF book into a clean, reflowable EPUB that actually reads well on an e-reader
(Kindle, Kobo, Apple Books, and so on) — instead of the tiny, unzoomable, page-locked
mess you get from just renaming a PDF to `.epub`.

Point it at a PDF, watch a live side-by-side preview of what will change, and download
an EPUB with running headers/footers and page numbers stripped, hyphenation and line
breaks cleaned up, footnotes turned into linked chapter endnotes, and a proper table of
contents. Everything runs locally — no file ever leaves your machine.

## Why

Most "PDF to EPUB" converters either shell out to Calibre and keep every page-layout
artifact (headers, footers, running titles, hyphenated line breaks, mid-sentence page
splits), or they OCR the whole thing and lose the structure. Neither is pleasant to
actually read on a 6" screen. This tool does layout-aware extraction instead: it looks
at where text actually sits on the page and reconstructs it as prose, the way an EPUB
is supposed to work.

## What it does

- **Strips headers, footers, and page numbers** — using adjustable margin sliders,
  plus automatic detection of repeating running heads (so it works even when a header
  only shows up on a handful of chapter pages, not every page).
- **Cleans up line breaks** — de-hyphenates words split across lines, merges wrapped
  lines into real paragraphs, and joins a paragraph that gets cut off mid-sentence by
  a page break.
- **Two-column layouts** — reads left column then right column, not left-line,
  right-line, left-line.
- **Footnotes → endnotes** — detects footnote markers (`¹`, `[1]`, `1.`) and their
  matching note at the bottom of the page, removes the note from the body, links the
  reference to a linked, backlinked "Endnotes" section at the end of each chapter.
- **Images, figures, and tables** — inline images become isolated, non-cropped image
  blocks; infographics, diagrams, and pull quotes (detected from the page's vector
  drawings and page geometry) are rendered as pictures in place; small text tables
  become real HTML tables; two-column glossaries become definition lists.
- **Rotated / scanned pages** — a sideways map or a page with no real text layer is
  kept as an upright image instead of turning into garbage text.
- **Table of contents** — built from the PDF's native bookmarks, or parsed from a
  visual "Contents" page if there are none, and wired up as a real EPUB navigation
  document (`nav.xhtml` / `toc.ncx`) plus an inline TOC page.
- **Live preview** — step through the PDF page by page, see exactly what will be
  stripped (highlighted in red) and what the cleaned-up HTML will look like, before
  you convert anything.

## Screenshot

*(Run it locally and drop a PDF in — the left panel has the controls, the right panel
shows the original page next to the cleaned preview.)*

## Requirements

- Python 3.10+
- No external services, no API keys, no network access needed at runtime

## Quickstart

```bash
git clone https://github.com/rayestu/ebook-formatter.git
cd ebook-formatter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

This starts a local server on `http://127.0.0.1:8000` (or the next free port) and
opens it in your browser automatically.

Upload a PDF, adjust the margin sliders and toggles while watching the preview, hit
**Convert to EPUB**, and the finished file is saved straight to your Downloads folder
(with a fallback "Download a copy" button if that fails).

## How it works

```
app/
  main.py              FastAPI app: upload, preview, convert, download
  engine/
    extract.py         Per-page text/image/vector-drawing extraction (PyMuPDF)
    classify.py         Header/footer/page-number/running-head detection
    flow.py             Paragraph merging, de-hyphenation, columns, lists,
                         tables, figures, pull quotes, cross-page joins
    footnotes.py         Footnote marker & note detection
    toc.py               Table of contents (bookmarks + visual fallback)
    build_html.py        Paragraph stream -> chapter XHTML
    epub.py               EPUB packaging (ebooklib)
    pipeline.py           Orchestrates it all; used by both the preview and
                          the final conversion
  static/               Vanilla HTML/CSS/JS frontend, no build step
```

Built with [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF structure extraction,
[BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) for HTML cleanup,
[ebooklib](https://github.com/aerkalov/ebooklib) for EPUB packaging, and
[FastAPI](https://fastapi.tiangolo.com/) for the backend.

## Testing

```bash
.venv/bin/python -m pytest -q tests
```

The test suite generates synthetic PDFs covering headers/footers, de-hyphenation,
cross-page merges, footnotes, glossaries, lists, two-column layouts, tables, rotated
figures, and table-of-contents extraction, then validates the resulting EPUBs
(structure, manifest, internal links) end to end.

## License

MIT
