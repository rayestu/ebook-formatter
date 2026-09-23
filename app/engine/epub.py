"""EPUB packaging with ebooklib: chapters, nav.xhtml, toc.ncx, inline TOC page."""
import html
import uuid

from ebooklib import epub

from .build_html import EBOOK_CSS


def _nest(entries):
    """entries: [(level, title, href)] -> nested [(node, children)] via a level stack."""
    root = {"children": []}
    stack = [(0, root)]
    for lvl, title, href in entries:
        node = {"title": title, "href": href, "children": []}
        while stack[-1][0] >= lvl:
            stack.pop()
        stack[-1][1]["children"].append(node)
        stack.append((lvl, node))
    return root["children"]


def _to_ebooklib(nodes, counter):
    out = []
    for n in nodes:
        counter[0] += 1
        link = epub.Link(n["href"], n["title"], f"nav{counter[0]}")
        if n["children"]:
            out.append((epub.Section(n["title"], n["href"]), _to_ebooklib(n["children"], counter)))
        else:
            out.append(link)
    return out


def _inline_toc(nodes):
    if not nodes:
        return ""
    lis = "".join(
        f'<li><a href="{html.escape(n["href"])}">{html.escape(n["title"])}</a>{_inline_toc(n["children"])}</li>'
        for n in nodes)
    return f"<ul>{lis}</ul>"


def build_epub(path, title, author, chapters, toc_entries, images, lang="en"):
    """chapters: [(title, filename, body_html)]
    toc_entries: [(level, title, "file.xhtml#chapter_3")]
    images: {filename: bytes}
    """
    book = epub.EpubBook()
    book.set_identifier(f"urn:uuid:{uuid.uuid4()}")
    book.set_title(title)
    book.set_language(lang)
    if author:
        book.add_author(author)

    css = epub.EpubItem(uid="style", file_name="style/ebook.css", media_type="text/css",
                        content=EBOOK_CSS.encode("utf-8"))
    book.add_item(css)

    def page(ctitle, fname, body):
        p = epub.EpubHtml(title=ctitle, file_name=fname, lang=lang)
        p.content = body or "<p></p>"
        p.add_item(css)
        book.add_item(p)
        return p

    if not toc_entries:
        toc_entries = [(1, c[0], c[1]) for c in chapters]
    nodes = _nest(toc_entries)

    spine = ["nav"]
    if len(toc_entries) > 1:
        toc_page = page("Table of Contents", "toc.xhtml",
                        f'<nav class="inline-toc" id="toc"><h1>Table of Contents</h1>{_inline_toc(nodes)}</nav>')
        spine.append(toc_page)
    for ctitle, fname, body in chapters:
        spine.append(page(ctitle, fname, body))

    for name, data in images.items():
        ext = name.rsplit(".", 1)[-1].lower()
        mt = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}.get(ext, "image/png")
        book.add_item(epub.EpubItem(uid="img_" + name.split(".")[0], file_name=f"images/{name}",
                                    media_type=mt, content=data))

    book.toc = _to_ebooklib(nodes, [0])
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    epub.write_epub(str(path), book)
