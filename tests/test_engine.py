import zipfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.engine.flow import join_text
from app.engine.options import Options
from app.engine.pipeline import PdfSession
from tests.make_pdf import make


@pytest.fixture(scope="module")
def pdf(tmp_path_factory):
    p = tmp_path_factory.mktemp("pdf") / "sample.pdf"
    make(str(p))
    return p


@pytest.fixture(scope="module")
def session(pdf):
    return PdfSession(pdf)


def html_of(session, n, **kw):
    return session.preview(n, Options(**kw), lambda x: f"/img/{x}")


def test_normalize_dashes():
    from app.engine.flow import normalize_dashes
    assert normalize_dashes("Chinese -- the industry") == "Chinese—the industry"
    assert normalize_dashes("word--word") == "word—word"
    assert normalize_dashes("a--b--c") == "a—b—c"
    assert normalize_dashes("-----") == "-----"                # a divider, not a dash: left alone
    assert normalize_dashes("range 10-20") == "range 10-20"     # a single hyphen: left alone


def test_double_hyphen_in_pdf_becomes_em_dash(tmp_path):
    import pymupdf
    p = tmp_path / "dash.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "Chinese -- the industry does well of course.", fontsize=11)
    pg.insert_text((50, 300), "A section divider follows.", fontsize=11)
    pg.insert_text((50, 320), "-----", fontsize=11)
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert "Chinese—the industry" in h
    assert "-----" in h                                          # the divider survives untouched


def test_join_text_dehyphenation():
    assert join_text("we con-", "vert it") == "we convert it"
    assert join_text("the Anglo-", "Saxon era") == "the Anglo-Saxon era"
    assert join_text("a self-", "aware being") == "a self-aware being"
    assert join_text("con-", "vert", dehyphenate=False) == "con-vert"
    assert join_text("plain", "join") == "plain join"


def test_header_pagenum_stripped(session):
    r = html_of(session, 0)
    kinds = {x["kind"] for x in r["regions"]}
    assert {"running", "pagenum"} <= kinds
    assert "The Sample Book" not in r["html"]


def test_options_top_for_bottom_for():
    o = Options(top_pct=4, bottom_pct=5, odd_even=False, top_pct_even=9, bottom_pct_even=10)
    for idx in range(6):                                     # odd_even off: every page uses the first set
        assert o.top_for(idx) == 4 and o.bottom_for(idx) == 5
    o2 = Options(top_pct=4, bottom_pct=5, odd_even=True, top_pct_even=9, bottom_pct_even=10)
    assert o2.top_for(0) == 4 and o2.bottom_for(0) == 5          # page 1 (index 0): odd -> first set
    assert o2.top_for(1) == 9 and o2.bottom_for(1) == 10         # page 2 (index 1): even -> second set
    assert o2.top_for(2) == 4 and o2.top_for(3) == 9


def test_options_from_dict_odd_even():
    o = Options.from_dict({"top": 3, "bottom": 4, "odd_even": "true", "top_even": 11, "bottom_even": 12})
    assert o.odd_even is True and o.top_pct == 3 and o.top_pct_even == 11 and o.bottom_pct_even == 12
    # not supplying the even-page fields defaults them to the odd/first set, not 6/6
    o2 = Options.from_dict({"top": 8, "bottom": 2, "odd_even": True})
    assert o2.top_pct_even == 8 and o2.bottom_pct_even == 2
    assert Options.from_dict({}).odd_even is False


def test_odd_even_margins_strip_different_zones(tmp_path):
    import pymupdf
    p = tmp_path / "oe.pdf"
    doc = pymupdf.open()
    words = ["Alpha", "Bravo", "Charlie", "Delta"]             # different wording each page: not a repeating
    for n in range(4):                                        # running header, so only the slider zone matters
        pg = doc.new_page(width=420, height=600)
        pg.insert_text((150, 40), f"{words[n]} outer margin note", fontsize=9)     # ~7% down: a 10%+ zone reaches it
        pg.insert_text((50, 110), f"Body text on page {n} continues normally here and here.", fontsize=11)
        pg.insert_text((50, 124), "and this is the second line of the same ordinary paragraph.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    for n in range(4):                                        # odd_even off: 4% misses the note on every page
        assert f"{words[n]} outer margin" in html_of(s, n, top_pct=4)["html"]

    split = Options(top_pct=4, bottom_pct=4, odd_even=True, top_pct_even=10, bottom_pct_even=4)
    r0 = s.preview(0, split, lambda x: "")                    # page 1 (odd): still uses 4% -> kept
    r1 = s.preview(1, split, lambda x: "")                    # page 2 (even): uses 10% -> stripped
    assert "Alpha outer margin" in r0["html"]
    assert "Bravo outer margin" not in r1["html"]
    assert "Body text on page 1" in r1["html"]                 # body itself is untouched


def test_slider_zone_keeps_a_real_heading(session):
    # "Chapter One" is set much larger than body text -> a real heading, kept even inside the zone
    r = html_of(session, 0, top_pct=15)
    assert "Chapter One" in r["html"] and not any("Chapter One" in x["text"] for x in r["regions"])


def test_slider_zone_strips_a_one_off_body_sized_title_block(tmp_path):
    """A title/letterhead block that appears on page 1 only (no repeats, e.g. a single-document PDF
    with no other pages to compare against) and is set in the same size as body text must still be
    stripped once the slider is dragged over it -- the slider is a direct "cut this zone" instruction,
    not gated on repetition."""
    import pymupdf
    p = tmp_path / "letterhead.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((90, 40), "SPEECH BY THE PRIME MINISTER,", fontsize=10.5)
    pg.insert_text((90, 54), "DURING THE DEBATE ON 27TH MAY.", fontsize=10.5)
    pg.insert_text((50, 120), "Mr Speaker, Sir, with the formal opening of this session", fontsize=10.5)
    pg.insert_text((50, 134), "we open a new chapter and continue in the ordinary way.", fontsize=10.5)
    doc.save(p)
    s = PdfSession(p)
    assert "SPEECH BY" in html_of(s, 0, top_pct=0)["html"]
    r = html_of(s, 0, top_pct=15)
    assert "SPEECH BY" not in r["html"] and "DURING THE DEBATE" not in r["html"]
    assert "Mr Speaker" in r["html"]                                  # body text below is untouched
    assert any(x["kind"] == "header" for x in r["regions"])


def test_slider_zone_strips_text_seen_on_other_pages(tmp_path):
    import pymupdf
    p = tmp_path / "zr.pdf"
    doc = pymupdf.open()
    for n in range(12):
        pg = doc.new_page(width=420, height=600)
        if n in (0, 9):                                    # same text twice, far apart: too sparse for the running-head rule
            pg.insert_text((150, 70), "Occasional banner text", fontsize=9)
        pg.insert_text((50, 120), f"Unique body sentence number {'abcdefghijkl'[n]} here.", fontsize=11)
        pg.insert_text((50, 134), "Some more filler prose to be the body text here.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    assert "Occasional banner" in html_of(s, 0, top_pct=0)["html"]
    assert "Occasional banner" not in html_of(s, 0, top_pct=15)["html"]


def test_dehyphenate_toggle(session):
    assert "convert every scanned" in html_of(session, 0)["html"]
    assert "con-vert" in html_of(session, 0, dehyphenate=False)["html"]


def test_cross_page_merge(session):
    a, b = html_of(session, 1), html_of(session, 2)
    assert a["merged"]["to_next"] and b["merged"]["from_prev"]
    assert "terminal mark finishes here" in b["html"]


def test_footnote_linking(session):
    h = html_of(session, 0)["html"]
    assert '<a class="fnref" href="#fn1" id="ref1"><sup>1</sup></a>' in h
    assert "This is the footnote text" not in h and "Endnotes" not in h    # moved off the page
    assert "This is the footnote text" in html_of(session, 0, footnotes=False)["html"]


def test_image_block(session):
    h = html_of(session, 0)["html"]
    assert 'class="img-block"' in h and 'class="ebook-img"' in h


def test_epub_output(session, tmp_path):
    out = tmp_path / "o.epub"
    session.convert(Options(), out)
    z = zipfile.ZipFile(out)
    names = z.namelist()
    assert z.read("mimetype") == b"application/epub+zip"
    assert "EPUB/nav.xhtml" in names and "EPUB/toc.ncx" in names and "EPUB/toc.xhtml" in names
    assert any(n.startswith("EPUB/images/") for n in names)
    nav = z.read("EPUB/nav.xhtml").decode()
    assert "chapter_0" in nav and "chapter_1" in nav and "Chapter Two" in nav
    ch = [n for n in names if n.endswith(".xhtml") and "chapter_" in n.rsplit("/", 1)[-1]]
    assert len(ch) == 2
    for n in ch:
        BeautifulSoup(z.read(n), "lxml-xml")                  # well-formed
    body = " ".join(z.read(n).decode() for n in ch)
    assert 'id="chapter_0"' in body and 'id="chapter_1"' in body
    assert "img.ebook-img" in z.read("EPUB/style/ebook.css").decode()
    assert "Sample Book" not in body                          # running header gone


def test_single_chapter_when_disabled(session, tmp_path):
    out = tmp_path / "o2.epub"
    session.convert(Options(chapters=False), out)
    names = zipfile.ZipFile(out).namelist()
    assert len([n for n in names if "chapter_" in n.rsplit("/", 1)[-1] and n.endswith(".xhtml")]) == 1


def test_visual_toc_fallback(tmp_path):
    import pymupdf
    p = tmp_path / "v.pdf"
    doc = pymupdf.open()
    toc = doc.new_page(width=420, height=600)
    toc.insert_text((50, 60), "Contents", fontsize=18)
    for i, (t, pg) in enumerate([("Alpha Beginnings", 3), ("Beta Middles", 4), ("Gamma Endings", 5)]):
        toc.insert_text((50, 110 + i * 22), t + " " + "." * 20, fontsize=11)
        toc.insert_text((340, 110 + i * 22), str(pg), fontsize=11)
    doc.new_page(width=420, height=600).insert_text((50, 100), "Preface text.", fontsize=11)
    for t in ("Alpha Beginnings", "Beta Middles", "Gamma Endings"):
        pg = doc.new_page(width=420, height=600)
        pg.insert_text((50, 80), t, fontsize=20, fontname="hebo")
        pg.insert_text((50, 130), "Some body text for this part.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    assert s.toc.source == "visual" and [e.page for e in s.toc.entries] == [2, 3, 4]


def test_api_flow(pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2EPUB_OUT_DIR", str(tmp_path))
    monkeypatch.setenv("PDF2EPUB_NO_REVEAL", "1")
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/upload", files={"file": ("sample.pdf", pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200, r.text
    i = r.json()["doc_id"]
    assert c.get(f"/api/{i}/page/0/image").headers["content-type"] == "image/png"
    p = c.get(f"/api/{i}/page/0/preview", params={"top": 8, "bottom": 8}).json()
    assert p["regions"] and "Chapter One" in p["html"]
    assert c.post("/api/upload", files={"file": ("x.pdf", b"nope", "application/pdf")}).status_code == 400
    c.post(f"/api/{i}/convert", json={"top": 6, "bottom": 6})
    import time
    for _ in range(100):
        s = c.get(f"/api/{i}/status").json()
        if s["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["state"] == "done", s
    for _ in range(50):                                   # delivery to the output folder follows conversion
        s = c.get(f"/api/{i}/status").json()
        if s["saved"]:
            break
        time.sleep(0.1)
    assert s["saved"] and s["saved"].startswith(str(tmp_path)) and Path(s["saved"]).read_bytes()[:2] == b"PK"
    assert c.post(f"/api/{i}/reveal").status_code == 200
    d = c.get(f"/api/{i}/download")
    assert d.status_code == 200 and d.content[:2] == b"PK"


def test_glossary_becomes_definition_list(tmp_path):
    import pymupdf
    p = tmp_path / "g.pdf"
    doc = pymupdf.open()
    rows = [("ABDA", ["Wavell's American-British-Dutch", "Command"]), ("AFL", ["American Federation of Labor"]),
            ("ANETA", ["Netherlands news agency"]), ("ANRI", ["Indonesian archive", "in Jakarta"]),
            ("AP", ["Associated Press"]), ("ARA", ["Dutch National Archives", "in The Hague"])]
    for ci, chunk in enumerate((rows[:3], rows[3:])):
        pg = doc.new_page(width=420, height=600)
        if ci == 0:
            pg.insert_text((50, 60), "Abbreviations and Glossary", fontsize=16, fontname="hebo")
        y = 120
        if ci == 1:
            pg.insert_text((150, y), "of Jakarta (continued)", fontsize=11)   # continuation at page top
            y += 14
        for term, defn in chunk:
            pg.insert_text((50, y), term, fontsize=11)
            for ln in defn:
                pg.insert_text((150, y), ln, fontsize=11)
                y += 14
    doc.save(p)
    s = PdfSession(p)
    h1 = html_of(s, 0)["html"]
    assert h1.count("<dt>") == 3 and "<dt>ABDA</dt>" in h1
    assert "<dd>Wavell's American-British-Dutch Command</dd>" in h1
    h2 = html_of(s, 1)["html"]
    assert "<dt>AP</dt>" in h2 and "Jakarta (continued)" in h1 + h2


def test_visual_toc_with_roman_and_subentries(tmp_path):
    import pymupdf
    p = tmp_path / "c.pdf"
    doc = pymupdf.open()
    toc = doc.new_page(width=420, height=600)
    toc.insert_text((150, 60), "Contents", fontsize=18)
    spec = [(50, "Preface", "vii"), (50, "Introduction", "1"), (80, "Key concepts", "2"),
            (80, "Historiography", "4"), (50, "Economy", "5")]
    for i, (x, t, pg) in enumerate(spec):
        y = 110 + i * 20
        toc.insert_text((x, y), t, fontsize=11)
        toc.insert_text((340, y) if x == 50 else (x + 90, y), pg, fontsize=11)
    doc.new_page(width=420, height=600).insert_text((50, 100), "Preface", fontsize=20, fontname="hebo")   # pdf p2 = "vii"
    for t in ("Introduction", "Key concepts", "Historiography", "Economy"):
        pg = doc.new_page(width=420, height=600)
        pg.insert_text((50, 80), t, fontsize=20, fontname="hebo")
        pg.insert_text((50, 130), "Body text.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    assert s.toc.source == "visual" and 0 in s.toc.skip_pages
    assert [e.title for e in s.toc.entries] == ["Preface", "Introduction", "Key concepts", "Historiography", "Economy"]
    assert [e.level for e in s.toc.entries] == [1, 1, 2, 2, 1]
    assert "Contents page" in html_of(s, 0)["html"]


def test_numbered_list_with_hanging_indent(tmp_path):
    import pymupdf
    p = tmp_path / "l.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    y = 100
    for ln in ["Some intro text that ends the paragraph properly."]:
        pg.insert_text((50, y), ln, fontsize=11); y += 14
    y += 10
    items = [("1.", ["establishment of new enterprises in", "sectors closed to govern-", "ment (Chapter IV);"]),
             ("2.", ["transfer of colonial enterprises;"]),
             ("3.", ["increased control (Chapter", "VI);"])]
    for num, lines in items:
        pg.insert_text((50, y), f"{num} {lines[0]}", fontsize=11); y += 14
        for ln in lines[1:]:
            pg.insert_text((64, y), ln, fontsize=11); y += 14      # hanging indent
    y += 10
    pg.insert_text((50, y), "A closing paragraph follows the list.", fontsize=11)
    pg2 = doc.new_page(width=420, height=600)
    y = 100
    for num, ln in [("7.", "seventh item;"), ("8.", "eighth item.")]:
        pg2.insert_text((50, y), f"{num} {ln}", fontsize=11); y += 14
    pg2.insert_text((50, y + 10), "- a bullet", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    h = html_of(s, 0)["html"]
    assert h.count("<li>") == 3 and h.startswith("<p>Some intro") and "<ol>" in h
    assert "establishment of new enterprises in sectors closed to government (Chapter IV);" in h
    assert "control (Chapter VI);</li>" in h and h.rstrip().endswith("A closing paragraph follows the list.</p>")
    h2 = html_of(s, 1)["html"]
    assert '<ol start="7">' in h2 and "<ul><li>a bullet</li></ul>" in h2


def test_bold_italic_preserved(tmp_path):
    import pymupdf
    p = tmp_path / "s.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    x = 50
    for txt, font in [("A normal start with ", "helv"), ("italic words", "heit"), (" and ", "helv"), ("bold words", "hebo"),
                      (" then plain end.", "helv")]:
        pg.insert_text((x, 100), txt, fontsize=11, fontname=font)
        x += pymupdf.get_text_length(txt, font, 11)
    pg.insert_text((50, 120), "Second plain line of the paragraph here.", fontsize=11)
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert "<i>italic words</i>" in h and "<b>bold words</b>" in h
    assert h.startswith("<p>A normal start with <i>")


def test_footnote_with_detached_number_cell(tmp_path):
    """Footnote number in its own text object (gap before the text) + raised ref as its own line."""
    import pymupdf
    p = tmp_path / "f.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "National historiography is not impartial, as argued", fontsize=11)
    pg.insert_text((50 + pymupdf.get_text_length("National historiography is not impartial, as argued", "helv", 11), 95),
                   "1", fontsize=7)                                    # raised ref, separate text object
    pg.insert_text((50, 114), "and this sentence carries on and on and legiti-", fontsize=11)
    pg.insert_text((50, 500), "1", fontsize=8)                        # footnote number cell
    pg.insert_text((85, 500), "Bertocchi and Canova 2002. I am grateful to Daan Marks.", fontsize=8)
    pg.insert_text((50, 512), "University of Utrecht, for drawing my attention.", fontsize=8)
    pg2 = doc.new_page(width=420, height=600)
    pg2.insert_text((50, 100), "mate claims. The end.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    r = html_of(s, 0)
    assert "Bertocchi" not in r["html"] and "Endnotes" not in r["html"]      # not shown on the page
    assert 'href="#fn1"' in r["html"] and r["footnotes"] == 1
    assert "legitimate claims" in r["html"]                                   # note no longer blocks the page-break merge
    out = tmp_path / "f.epub"
    s.convert(Options(), out)
    body = zipfile.ZipFile(out).read("EPUB/chapter_1.xhtml").decode()
    assert "Endnotes" in body and "Bertocchi and Canova 2002" in body and 'href="#ref1"' in body


def test_list_marker_in_separate_cell(tmp_path):
    """Real PDFs often place '1.' and its text as separate text objects on one row."""
    import pymupdf
    p = tmp_path / "lc.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "Some intro text that ends the paragraph properly.", fontsize=11)
    y = 130
    for num, lines in [("1.", ["establishment of new enterprises in", "sectors closed to govern-", "ment (Chapter IV);"]),
                       ("2.", ["transfer of colonial enterprises;"]), ("3.", ["increased control (Chapter", "VI)."])]:
        pg.insert_text((50, y), num, fontsize=11)
        for ln in lines:
            pg.insert_text((80, y), ln, fontsize=11)
            y += 14
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert h.count("<li>") == 3 and "<ol>" in h
    assert "closed to government (Chapter IV);</li>" in h


def test_lowercase_heading_gets_small_caps(tmp_path):
    import pymupdf
    p = tmp_path / "sc.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "chapter i", fontsize=18, fontname="hebo")
    pg.insert_text((50, 150), "Body text follows here and it is ordinary.", fontsize=11)
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert 'class="sc"' in h and ">chapter i<" in h


def test_endnotes_piled_at_chapter_end(tmp_path):
    import pymupdf
    p = tmp_path / "en.pdf"
    doc = pymupdf.open()
    for k in (1, 2, 3):
        pg = doc.new_page(width=420, height=600)
        if k == 1:
            pg.insert_text((50, 80), "Chapter One", fontsize=22, fontname="hebo")
        y0 = 130
        pg.insert_text((50, y0), f"Body sentence number {k} carries a note", fontsize=11)
        x = 50 + pymupdf.get_text_length(f"Body sentence number {k} carries a note", "helv", 11)
        pg.insert_text((x, y0 - 4), "1", fontsize=7)
        pg.insert_text((x + 6, y0), " here.", fontsize=11)
        pg.insert_text((50, 500), f"1 Note text for page {k}.", fontsize=8)
    doc.set_toc([[1, "Chapter One", 1]])
    doc.save(p)
    s = PdfSession(p)
    out = tmp_path / "en.epub"
    s.convert(Options(), out)
    body = zipfile.ZipFile(out).read("EPUB/chapter_1.xhtml").decode()
    assert body.count("Endnotes") == 1                        # one pile, at the chapter end
    assert body.index("Endnotes") > body.index("Body sentence number 3")
    for k in (1, 2, 3):
        assert f"Note text for page {k}." in body
    assert body.count('class="fnref"') == 3 and body.count("↩") == 3
    assert 'value="1"' in body and 'value="2"' in body and 'value="3"' in body      # per-page 1,1,1 -> counted 1,2,3
    for c in (1, 2, 3):
        assert f'href="#fn{c}"' in body and f'id="fn{c}"' in body and f'href="#ref{c}"' in body


def test_chapter_specific_running_head(tmp_path):
    """Header below the slider zone, present on only 4 of 12 pages, must still be stripped."""
    import pymupdf
    p = tmp_path / "rh.pdf"
    doc = pymupdf.open()
    for n in range(1, 13):
        pg = doc.new_page(width=420, height=600)
        y = 60                                            # 10% down: outside the 6% slider zone
        if 3 <= n <= 6:
            pg.insert_text((150, y), "I  Introduction", fontsize=9, fontname="heit")
        if n == 9:
            pg.insert_text((150, y), "A one-off page head", fontsize=9, fontname="heit")
        pg.insert_text((330, y), str(n), fontsize=9)
        pg.insert_text((50, 110), f"First body line of page {n} starts here.", fontsize=11)
        pg.insert_text((50, 124), "More body text follows on the second line.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    for n in (2, 3, 4, 8):                                # 0-based: pages 3,4,5 and the one-off page 9
        h = html_of(s, n, top_pct=6)["html"]
        assert "Introduction" not in h and "one-off" not in h
        assert "First body line" in h                     # the real first line is kept


def _map_pdf(path):
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page(width=300, height=500)
    pg.draw_rect(pymupdf.Rect(0, 0, 40, 40), color=None, fill=(1, 0, 0))            # marks the page's top-left
    for k, lab in enumerate(["SUMATRA", "JAVA", "Medan", "Padang", "Map 1. The archipelago in 1945-1947."]):
        pg.insert_text((150, 450 - k * 60), lab, rotate=90, fontsize=12)
    doc.new_page(width=300, height=500).insert_text((50, 100), "Ordinary page after the map.", fontsize=11)
    doc.save(path)


def test_rotated_figure_page_becomes_upright_image(tmp_path):
    import pymupdf
    p = tmp_path / "m.pdf"
    _map_pdf(p)
    s = PdfSession(p)
    r = html_of(s, 0)
    assert r["html"].count("<img") == 1 and "SUMATRA" not in r["html"] and "Medan" not in r["html"]
    assert 'alt="Map 1. The archipelago in 1945-1947."' in r["html"] and "Figure page" in r["warning"]
    png = s.render_figure(0, 90)
    pix = pymupdf.Pixmap(png)
    r_, g_, b_ = pix.pixel(pix.width - 5, 5)[:3]
    assert r_ > 200 and g_ < 60                          # red top-left marker is now top-right: text reads upright
    out = tmp_path / "m.epub"
    s.convert(Options(), out)
    z = zipfile.ZipFile(out)
    assert any("page_1_r90" in n for n in z.namelist())
    assert "Ordinary page after the map." in html_of(s, 1)["html"]


def test_garbage_text_detected():
    from app.engine.flow import is_garbage
    assert is_garbage("\ufffd\ufffd\ufffd \ufffd\ufffd") and not is_garbage("Sumatra \ufffd")


def test_footnote_numbers_follow_the_book(tmp_path):
    """Printed numbers 6 and 7 must stay 6 and 7 (not restart at 1) when they count upwards."""
    import pymupdf
    p = tmp_path / "num.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "Chapter One", fontsize=22, fontname="hebo")
    y = 160
    for n in (6, 7):
        txt = f"Sentence with note {n}"
        pg.insert_text((50, y), txt, fontsize=11)
        x = 50 + pymupdf.get_text_length(txt, "helv", 11)
        pg.insert_text((x, y - 4), str(n), fontsize=7)
        pg.insert_text((x + 6, y), " here.", fontsize=11)
        y += 30
    pg.insert_text((50, 480), "6 First note text.", fontsize=8)
    pg.insert_text((50, 492), "7 Second note text.", fontsize=8)
    doc.set_toc([[1, "Chapter One", 1]])
    doc.save(p)
    s = PdfSession(p)
    assert "<sup>6</sup></a>" in html_of(s, 0)["html"] and "<sup>7</sup></a>" in html_of(s, 0)["html"]
    out = tmp_path / "num.epub"
    s.convert(Options(), out)
    body = zipfile.ZipFile(out).read("EPUB/chapter_1.xhtml").decode()
    assert 'value="6"' in body and 'value="7"' in body and "<sup>6</sup>" in body


def test_inline_unicode_superscript_ref(tmp_path):
    import pymupdf
    p = tmp_path / "us.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 100), "A sentence with an inline mark\u00b2 in the middle of it.", fontsize=11)
    pg.insert_text((50, 500), "\u00b2 The unicode note.", fontsize=8)
    doc.save(p)
    r = html_of(PdfSession(p), 0)
    assert 'href="#fn1"' in r["html"] and "The unicode note" not in r["html"] and r["footnotes"] == 1


def test_api_robustness(pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2EPUB_OUT_DIR", str(tmp_path))
    monkeypatch.setenv("PDF2EPUB_NO_REVEAL", "1")
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/upload", files={"file": ("junk.pdf", b"\n\n" + pdf.read_bytes(), "application/pdf")})
    assert r.status_code == 200                                  # junk before %PDF is legal
    i = r.json()["doc_id"]
    for body in ([], "str", 5, None):
        assert c.post(f"/api/{i}/convert", json=body).status_code == 200
        import time
        for _ in range(100):
            if c.get(f"/api/{i}/status").json()["state"] in ("done", "error"):
                break
            time.sleep(0.05)


def test_session_eviction(pdf):
    import app.main as m
    from fastapi.testclient import TestClient as TC
    c = TC(m.app)
    ids = [c.post("/api/upload", files={"file": ("a.pdf", pdf.read_bytes(), "application/pdf")}).json()["doc_id"]
           for _ in range(m.MAX_SESSIONS + 2)]
    assert len(m.SESSIONS) == m.MAX_SESSIONS and ids[0] not in m.SESSIONS and ids[-1] in m.SESSIONS
    assert c.get(f"/api/{ids[0]}/status").status_code == 404


def test_delete_document_frees_it_and_is_idempotent(pdf, tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2EPUB_OUT_DIR", str(tmp_path))
    monkeypatch.setenv("PDF2EPUB_NO_REVEAL", "1")
    from app.main import app
    c = TestClient(app)
    i = c.post("/api/upload", files={"file": ("a.pdf", pdf.read_bytes(), "application/pdf")}).json()["doc_id"]
    assert c.get(f"/api/{i}/status").status_code == 200
    assert c.delete(f"/api/{i}").status_code == 200
    assert c.get(f"/api/{i}/status").status_code == 404           # gone
    assert c.delete(f"/api/{i}").status_code == 200                # deleting again, or an unknown id, is a no-op
    assert c.delete("/api/unknown12345").status_code == 200

    j = c.post("/api/upload", files={"file": ("b.pdf", pdf.read_bytes(), "application/pdf")}).json()["doc_id"]
    c.post(f"/api/{j}/convert", json={})
    assert c.delete(f"/api/{j}").status_code == 200                # never deletes mid-conversion
    import time
    for _ in range(50):
        if c.get(f"/api/{j}/status").json()["state"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert c.get(f"/api/{j}/status").status_code == 200            # still present: convert wasn't interrupted


def _two_col_pdf(path, top_line_y=None):
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page(width=800, height=800)
    pg.insert_text((50, 60), "A Full Width Headline Above Both Columns", fontsize=18, fontname="hebo")
    L = ["Left column first line of running prose text goes here now,",
         "and it carries on for a second full width line of the text,",
         "before the paragraph ends with a proper full stop here. ",
         "Left column second paragraph starts on this line of text,",
         "continues with another ordinary line of prose text here,",
         "and then again another line of ordinary prose text to fill,",
         "one more line so the column is tall enough for detection,",
         "and finally the left column ends mid sentence and continues"]
    R = ["on the right column top line which finishes the sentence.",
         "Right column second paragraph starts on this line of text,",
         "continues with another ordinary line of prose text here too,",
         "and then again another line of ordinary prose text to fill,",
         "one more line so the column is tall enough for detection,",
         "and one more ordinary line of the right column prose text,",
         "plus yet another line of ordinary prose in this right column,",
         "and finally the right column ends properly with a full stop."]
    y = 100
    for a in L:
        pg.insert_text((50, y), a, fontsize=10.5); y += 14
    y = 100
    for b in R:
        pg.insert_text((420, y), b, fontsize=10.5); y += 14
    doc.save(path)


def test_two_column_reading_order_and_no_glossary(tmp_path):
    p = tmp_path / "c2.pdf"
    _two_col_pdf(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert "<dl" not in h and "<dt>" not in h                       # not mistaken for term | definition
    assert h.index("Left column first") < h.index("Left column second") < h.index("Right column second")
    assert "ends mid sentence and continues on the right column top line" in h     # sentence rejoined across columns
    assert h.index("Headline") < h.index("Left column first")


def test_first_body_line_inside_slider_zone_is_kept(tmp_path):
    import pymupdf
    p = tmp_path / "z.pdf"
    doc = pymupdf.open()
    for n in range(2):
        pg = doc.new_page(width=420, height=600)
        pg.insert_text((150, 30), f"Short Header {n}", fontsize=9)                  # a real header
        words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
        for k in range(8):                                                            # body starts at ~9%
            pg.insert_text((50, 54 + k * 13), f"Body line {words[k]} of page {'xy'[n]} is an ordinary full width line here.", fontsize=10.5)
    doc.save(p)
    r = html_of(PdfSession(p), 0, top_pct=10)
    assert "Short Header" not in r["html"]                       # stripped
    assert "Body line alpha of page x" in r["html"]                  # first body line survives although inside the zone


def test_vector_figure_becomes_picture(tmp_path):
    import pymupdf
    p = tmp_path / "fig.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 80), "Ordinary paragraph text before the figure sits here on the page.", fontsize=11)
    pg.insert_text((50, 300), "The Times Reaches A Vast Audience", fontsize=16, fontname="hebo")
    for k in range(3):
        y = 320 + k * 70
        pg.draw_line((50, y), (370, y), color=(0, .5, .7), width=1.5)
        for j in range(4):
            x = 50 + j * 80
            pg.draw_line((x, y), (x, y + 60), color=(0, .5, .7), width=1)
            pg.insert_text((x + 6, y + 25), f"{30 + j}M", fontsize=20, fontname="hebo")
            pg.insert_text((x + 6, y + 45), "readers", fontsize=8)
    pg.insert_text((50, 560), "Text after the figure continues here on the page.", fontsize=11)
    doc.save(p)
    s = PdfSession(p)
    r = html_of(s, 0)
    assert r["html"].count("<img") == 1 and "30M" not in r["html"] and "readers" not in r["html"]
    assert 'alt="The Times Reaches A Vast Audience"' in r["html"]
    assert "Ordinary paragraph text" in r["html"] and "Text after the figure" in r["html"]
    assert any(x["kind"] == "figure" for x in r["regions"])
    assert r["html"].index("Ordinary paragraph") < r["html"].index("<img") < r["html"].index("Text after")
    out = tmp_path / "fig.epub"
    s.convert(Options(), out)
    z = zipfile.ZipFile(out)
    assert any(n.startswith("EPUB/images/fig_1_") for n in z.namelist())


def test_text_table_becomes_html_table(tmp_path):
    import pymupdf
    p = tmp_path / "t.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 80), "Intro paragraph before the table, ending properly.", fontsize=11)
    rows = [("Name", "Qty", "Price"), ("Apple", "3", "1.20"), ("Pear", "5", "2.30"), ("Fig", "7", "9.99")]
    for k, r in enumerate(rows):
        for j, c in enumerate(r):
            pg.insert_text((50 + j * 100, 140 + k * 16), c, fontsize=11)
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert "<table" in h and "<th>Name</th><th>Qty</th><th>Price</th>" in h
    assert "<td>Apple</td><td>3</td><td>1.20</td>" in h and "<td>Fig</td><td>7</td><td>9.99</td>" in h


def test_long_title_still_saves(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.main as m
    monkeypatch.setenv("PDF2EPUB_OUT_DIR", str(tmp_path))
    src = tmp_path / "src.epub"
    src.write_bytes(b"PK")
    dest = m._deliver(SimpleNamespace(title="T" * 300), src)
    assert dest.exists() and len(dest.name) <= 130


def test_pull_quote_becomes_picture(tmp_path):
    import pymupdf
    p = tmp_path / "pq.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=800, height=800)
    for k in range(18):
        pg.insert_text((50, 100 + k * 14), f"Left column ordinary body text line number {k} runs here", fontsize=10.5) if not 5 <= k <= 9 else \
            pg.insert_text((50, 100 + k * 14), f"Left narrow line {k} of text", fontsize=10.5)
        pg.insert_text((520, 100 + k * 14), f"Right column ordinary body text line {k} runs on", fontsize=10.5) if not 5 <= k <= 9 else \
            pg.insert_text((560, 100 + k * 14), f"Right narrow {k} text", fontsize=10.5)
    pg.insert_text((250, 190), "A relentless run", fontsize=24)
    pg.insert_text((250, 220), "of headlines from", fontsize=24)
    pg.insert_text((250, 250), "new and old media", fontsize=24)
    doc.save(p)
    r = html_of(PdfSession(p), 0)
    assert r["html"].count("<img") == 1 and "relentless" not in r["html"].split("alt=")[0]
    assert 'alt="A relentless run of headlines from new and old media"' in r["html"]
    assert "Left narrow line 6" in r["html"] and "Right narrow 6" in r["html"]


def test_isolated_big_heading_is_not_a_pull_quote(tmp_path):
    import pymupdf
    p = tmp_path / "hd.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    pg.insert_text((50, 60), "Our Proposals, In Brief", fontsize=26, fontname="hebo")
    pg.insert_text((50, 120), "Ordinary paragraph text follows the big heading here.", fontsize=11)
    doc.save(p)
    h = html_of(PdfSession(p), 0, top_pct=15)["html"]
    assert "<img" not in h and "Our Proposals, In Brief" in h


def test_rules_split_bands_and_read_left_then_right(tmp_path):
    import pymupdf
    p = tmp_path / "bands.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=600, height=800)
    for r, y0 in enumerate((100, 300, 500)):
        pg.draw_line((50, y0 - 20), (550, y0 - 20), color=(.6, .6, .6), width=1)                 # full-width rule
        for k in range(4):
            pg.insert_text((50, y0 + k * 14), f"Row {r} text line {k} on the left side of page", fontsize=10.5)
        pg.draw_rect(pymupdf.Rect(330, y0 - 5, 550, y0 + 120), color=(0, .3, .7), fill=(.85, .9, .97), width=2)   # diagram
        pg.draw_line((330, y0 + 40), (550, y0 + 90), color=(0, .3, .7), width=3)
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    order = [h.index(f"Row {r} text line 0") for r in range(3)]
    assert order == sorted(order) and h.count("<img") == 3
    # per row: the text comes first, then that row's picture, and only then the next row's text
    pos = [m.start() for m in __import__("re").finditer("<img", h)]
    assert order[0] < pos[0] < order[1] < pos[1] < order[2] < pos[2]


def test_bold_list_number_is_not_duplicated(tmp_path):
    import pymupdf
    p = tmp_path / "bl.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=420, height=600)
    y = 100
    for n in (1, 2, 3):
        pg.insert_text((50, y), f"{n}.", fontsize=13, fontname="hebo")
        pg.insert_text((72, y), f"Item number {n} treats innovation as a series of", fontsize=11); y += 14
        pg.insert_text((72, y), "incremental improvements and steps.", fontsize=11); y += 22
    doc.save(p)
    h = html_of(PdfSession(p), 0)["html"]
    assert h.count("<li>") == 3 and "<li>1." not in h and "1. Item" not in h and "<b>" not in h.split("<li>")[1][:5]


def test_diagram_touching_page_wide_rules_keeps_side_text(tmp_path):
    import pymupdf
    p = tmp_path / "glue.pdf"
    doc = pymupdf.open()
    pg = doc.new_page(width=600, height=800)
    for y0 in (100, 300):
        pg.draw_line((50, y0 - 20), (550, y0 - 20), color=(.6, .6, .6), width=1)
        pg.draw_line((50, y0 + 160), (550, y0 + 160), color=(.6, .6, .6), width=1)
        for k in range(6):
            pg.insert_text((50, y0 + k * 14), f"Numbered row text for {y0} line {k} is an ordinary long prose line", fontsize=10.5)
    # one boxed diagram whose top and bottom edges touch the page-wide rules
    pg.draw_rect(pymupdf.Rect(360, 80, 550, 460), color=(0, .3, .7), fill=(.85, .9, .97), width=2)
    pg.draw_line((360, 180), (550, 240), color=(0, .3, .7), width=3)
    pg.draw_line((360, 400), (550, 340), color=(.8, 0, 0), width=3)
    doc.save(p)
    r = html_of(PdfSession(p), 0)
    assert r["html"].count("<img") == 1
    assert "Numbered row text for 100 line 0" in r["html"] and "Numbered row text for 300 line 5" in r["html"]
    fig = [x for x in r["regions"] if x["kind"] == "figure"][0]["bbox"]
    assert fig[0] > 0.5                                   # the picture is only the diagram (right side), not the text
