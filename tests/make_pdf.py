"""Build a small synthetic book PDF that exercises every engine rule."""
import pymupdf

W, H = 420, 600
LM, LH, FS = 50, 14, 11


def _lines(page, x, y, lines, size=FS, font="helv"):
    for ln in lines:
        page.insert_text((x, y), ln, fontsize=size, fontname=font)
        y += LH
    return y


def _sup_line(page, x, y, before, sup, after):
    """Line with an inline raised, smaller footnote marker."""
    page.insert_text((x, y), before, fontsize=FS, fontname="helv")
    x1 = x + pymupdf.get_text_length(before, "helv", FS)
    page.insert_text((x1, y - 4), sup, fontsize=7, fontname="helv")
    x2 = x1 + pymupdf.get_text_length(sup, "helv", 7)
    page.insert_text((x2, y), after, fontsize=FS, fontname="helv")


def make(path, with_bookmarks=True, with_image=True):
    doc = pymupdf.open()
    toc = []
    for n in range(1, 7):
        page = doc.new_page(width=W, height=H)
        page.insert_text((LM, 30), "The Sample Book", fontsize=9, fontname="helv")       # running header
        page.insert_text((W / 2 - 4, H - 25), str(n), fontsize=10, fontname="helv")        # page number
        y = 80
        if n in (1, 4):
            title = "Chapter One" if n == 1 else "Chapter Two"
            page.insert_text((LM, y), title, fontsize=22, fontname="hebo")
            toc.append([1, title, n])
            y += 40
        if n == 1:
            y = _lines(page, LM, y, [
                "It was a bright morning and we wanted to con-",
                "vert every scanned page into something better."])
            y += 8
            y = _lines(page, LM, y, ["A second paragraph starts here and ends properly."])
            y += 8
            _sup_line(page, LM, y, "This sentence has a note", "1", " attached to it.")
            y += LH
            if with_image:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
                pix.set_rect(pix.irect, (200, 60, 60))
                page.insert_image(pymupdf.Rect(LM, y + 10, LM + 120, y + 90), pixmap=pix)
                y += 100
            page.insert_text((LM, H - 80), "1 This is the footnote text", fontsize=8, fontname="helv")
        elif n == 2:
            y = _lines(page, LM, y, ["The story continues across pages with a sentence that does not"] * 1)
            page.insert_text((LM, H - 70), "and keeps going without a terminal mark", fontsize=FS, fontname="helv")
        elif n == 3:
            y = _lines(page, LM, y, ["finishes here on the next page. Then a new thought."])
        else:
            y = _lines(page, LM, y, ["Body text of page %d ends nicely." % n])
    if with_bookmarks:
        doc.set_toc(toc)
    doc.save(path)
    doc.close()


if __name__ == "__main__":
    import sys
    make(sys.argv[1] if len(sys.argv) > 1 else "sample.pdf")
