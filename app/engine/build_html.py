"""Paragraph stream -> chapter XHTML fragments (with anchors, endnotes, images)."""
import html
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .flow import BOLD_C, BOLD_O, ITAL_C, ITAL_O, REF_CLOSE, REF_OPEN, strip_markers

EBOOK_CSS = """\
html { font-size: 100%; }
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
p { margin: 0 0 0.9em 0; text-align: justify; hyphens: auto; -webkit-hyphens: auto; }
h1, h2, h3 { line-height: 1.25; page-break-after: avoid; break-after: avoid; }
h1 { font-size: 1.7em; margin: 1.6em 0 0.8em; }
h2 { font-size: 1.35em; margin: 1.4em 0 0.6em; }
h3 { font-size: 1.15em; margin: 1.2em 0 0.5em; }
ul, ol { margin: 0 0 1em 0; padding-left: 1.8em; }
li { margin-bottom: 0.3em; }
table.data { border-collapse: collapse; margin: 1.2em auto; max-width: 100%; }
table.data th, table.data td { border: 1px solid #999; padding: 0.25em 0.6em; text-align: left; }
table.data th { font-weight: bold; }
dl.glossary { margin: 0 0 1em 0; }
dl.glossary dt { font-weight: bold; margin-top: 0.6em; page-break-after: avoid; break-after: avoid; }
dl.glossary dd { margin: 0.1em 0 0 1.5em; }
img.ebook-img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.5em auto;
  page-break-inside: avoid;
  break-inside: avoid;
}
div.img-block { display: block; clear: both; text-align: center; margin: 1.5em 0; page-break-inside: avoid; break-inside: avoid; }
h1.sc, h2.sc, h3.sc { font-variant: small-caps; letter-spacing: 0.06em; }
sup a, a.fnref { text-decoration: none; }
section.endnotes { margin-top: 2.5em; border-top: 1px solid #999; padding-top: 0.5em; font-size: 0.9em; }
section.endnotes h2 { font-size: 1.1em; margin-top: 0.5em; }
section.endnotes li { margin-bottom: 0.4em; }
nav.inline-toc ul { list-style: none; padding-left: 1.2em; }
nav.inline-toc > ol, nav.inline-toc > ul { padding-left: 0; }
"""

_WS = re.compile(r"\s+")
NUM_OPEN, NUM_CLOSE = "\ue006", "\ue007"          # placeholder for a note's displayed number
NUM_RE = re.compile(NUM_OPEN + r"(\d+)" + NUM_CLOSE)
REF_RE = re.compile(REF_OPEN + r"(\d+):(\d+)" + REF_CLOSE)


@dataclass
class Chapter:
    title: str
    items: list
    anchors: dict = field(default_factory=dict)   # id(item) -> [anchor ids]
    filename: str = ""


@dataclass
class Notebook:
    """Footnote text by (page, n) plus the running global id counter."""
    notes: dict
    counter: int = 0


def _text_html(text, nb, chapter_notes, seen):
    def ref(m):
        key = (int(m.group(1)), int(m.group(2)))
        if key not in nb.notes:
            return ""
        if key in seen:
            return f"<sup>{key[1]}</sup>"
        nb.counter += 1
        c = nb.counter
        seen[key] = c
        chapter_notes.append((c, nb.notes[key], key[1]))
        return f'<a class="fnref" href="#fn{c}" id="ref{c}"><sup>{NUM_OPEN}{c}{NUM_CLOSE}</sup></a>'

    parts, pos = [], 0
    for m in REF_RE.finditer(text):
        parts.append(html.escape(re.sub(r"\s+", " ", text[pos:m.start()])))
        parts.append(ref(m))
        pos = m.end()
    parts.append(html.escape(re.sub(r"\s+", " ", text[pos:])))
    out = "".join(parts).strip()
    for mk, tag in ((ITAL_O, "<i>"), (ITAL_C, "</i>"), (BOLD_O, "<b>"), (BOLD_C, "</b>")):
        out = out.replace(mk, tag)
    return out


def chapter_body(chapter: Chapter, nb: Notebook, img_src, mark=None, show_notes=True) -> str:
    """Return the XHTML body fragment. img_src(ImageItem)->url or None.
    mark(item)->extra css class (used by previews)."""
    out, chapter_notes, seen, in_list = [], [], {}, None
    for it in chapter.items:
        if it.kind != "li" and in_list:
            out.append(f"</{in_list}>")
            in_list = None
        for a in chapter.anchors.get(id(it), []):
            out.append(f'<a id="{a}"></a>')
        cls = (mark(it) if mark else "") or ""
        cattr = f' class="{cls}"' if cls else ""
        if "cross" in cls.split():
            cattr += ' title="Paragraph continues across a page break; the two parts are joined"'
        if it.kind == "img":
            src = img_src(it.img)
            if src:
                out.append(f'<div class="img-block"><img class="ebook-img" src="{src}" alt="{html.escape(it.text or "", quote=True)}"/></div>')
        elif it.kind == "dl":
            rows = "".join(f"<dt>{_text_html(t, nb, chapter_notes, seen)}</dt><dd>{_text_html(d, nb, chapter_notes, seen)}</dd>"
                           for t, d in it.entries if t or d)
            out.append(f'<dl class="glossary"{cattr}>{rows}</dl>')
        elif it.kind == "table":
            def row(cells, tag):
                return "<tr>" + "".join(f"<{tag}>{_text_html(c, nb, chapter_notes, seen)}</{tag}>" for c in cells) + "</tr>"
            head = it.entries[0] if it.text == "header" else None
            body_rows = it.entries[1:] if head else it.entries
            out.append(f'<table class="data"{cattr}>' + (f"<thead>{row(head, 'th')}</thead>" if head else "")
                       + "<tbody>" + "".join(row(r, "td") for r in body_rows) + "</tbody></table>")
        elif it.kind == "li":
            ordered = bool(it.marker and it.marker[0])
            typ = "ol" if ordered else "ul"
            if in_list != typ:
                if in_list:
                    out.append(f"</{in_list}>")
                attrs = ""
                if ordered:
                    if it.marker[2] in ("a", "A"):
                        attrs += f' type="{it.marker[2]}"'
                    if it.marker[1] != 1:
                        attrs += f' start="{it.marker[1]}"'
                out.append(f"<{typ}{attrs}>")
                in_list = typ
            out.append(f"<li{cattr}>{_text_html(it.text, nb, chapter_notes, seen)}</li>")
        else:
            tag = it.kind if it.kind in ("h1", "h2", "h3") else "p"
            body = _text_html(it.text, nb, chapter_notes, seen)
            if tag != "p":
                plain = strip_markers(it.text)
                if re.search("[a-z]{2}", plain) and plain == plain.lower():
                    # printed in small caps: text extraction only yields lower case
                    cattr = cattr.replace('class="', 'class="sc ') if 'class="' in cattr else ' class="sc"' + cattr
            if body:
                out.append(f"<{tag}{cattr}>{body}</{tag}>")
    if in_list:
        out.append(f"</{in_list}>")
    # Show the numbers printed in the book when they count upwards through the chapter;
    # if the book restarts at 1 on every page, count 1, 2, 3 ... instead.
    origs = [n for _, _, n in chapter_notes]
    keep = all(b > a for a, b in zip(origs, origs[1:]))
    disp = {c: (n if keep else i) for i, (c, _, n) in enumerate(chapter_notes, 1)}
    if chapter_notes and show_notes:
        lis = "".join(f'<li id="fn{c}" value="{disp[c]}">{html.escape(_WS.sub(" ", t))} <a href="#ref{c}">↩</a></li>'
                      for c, t, _ in chapter_notes)
        out.append(f'<section class="endnotes" role="doc-endnotes"><h2>Endnotes</h2><ol>{lis}</ol></section>')
    return clean(NUM_RE.sub(lambda m: str(disp[int(m.group(1))]), "".join(out)))


def clean(fragment: str) -> str:
    """BeautifulSoup pass: drop empty blocks, normalise to well-formed XHTML."""
    soup = BeautifulSoup(f"<div>{fragment}</div>", "lxml-xml")
    for tag in soup.find_all(["p", "li", "h1", "h2", "h3", "dt", "dd"]):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()
    root = soup.find("div")
    return "".join(str(c) for c in root.contents)
