"""Detect footnote blocks at page bottoms and match them to in-text references."""
import re
from dataclasses import dataclass

MARK_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)]?(?=\s))\s*")
SUPDIG = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
SUPMARK_RE = re.compile(r"^\s*([⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3})\s*")
SUPRUN_RE = re.compile("[⁰¹²³⁴⁵⁶⁷⁸⁹]{1,3}")
BRACKET_REF_RE = re.compile(r"\[(\d{1,3})\]")


@dataclass
class Note:
    n: int
    text: str
    line_idx: list


def _marker(line):
    """(number, rest_of_text) if the line starts a footnote, else None."""
    t = line.text
    m = SUPMARK_RE.match(t)
    if m:
        return int(m.group(1).translate(SUPDIG)), t[m.end():]
    sp = line.spans
    if len(sp) > 1 and sp[0][0].strip().isdigit() and (sp[0][2] & 1 or sp[0][1] < max(x[1] for x in sp) * 0.8):
        return int(sp[0][0].strip()), "".join(x[0] for x in sp[1:]).lstrip()
    m = MARK_RE.match(t)
    if m:
        return int(m.group(1) or m.group(2)), t[m.end():]
    return None


def _body_refs(page, skip, body):
    """Set of numbers referenced in body text by superscripts or [n]."""
    refs = set()
    for i, ln in enumerate(page.lines):
        if i in skip:
            continue
        base = max(s[4] for s in ln.spans)
        for s in ln.spans:
            txt = s[0].strip()
            if txt.isdigit() and len(txt) <= 3 and s[1] < body * 0.9 and (s[2] & 1 or s[4] < base - 0.8) and len(ln.spans) > 1:
                refs.add(int(txt))
            elif txt and all(c in "⁰¹²³⁴⁵⁶⁷⁸⁹" for c in txt):
                refs.add(int(txt.translate(SUPDIG)))
        for m in SUPRUN_RE.finditer(ln.text):
            refs.add(int(m.group().translate(SUPDIG)))
        for m in BRACKET_REF_RE.finditer(ln.text):
            refs.add(int(m.group(1)))
    return refs


def find_notes(page, stripped: dict, body: float, join):
    """Return ({line_idx: True} footnote lines to remove, [Note])."""
    cand = [i for i, ln in enumerate(page.lines)
            if i not in stripped and (ln.bbox[1] / page.height) > 0.5 and ln.size <= body - 0.4]
    if not cand:
        return {}, []
    groups, cur = [], None
    for i in cand:
        mk = _marker(page.lines[i])
        if mk:
            cur = [mk[0], [mk[1]], [i]]
            groups.append(cur)
        elif cur is not None:
            cur[1].append(page.lines[i].text)
            cur[2].append(i)
    if not groups:
        return {}, []
    used = {i for g in groups for i in g[2]}
    refs = _body_refs(page, stripped.keys() | used, body)
    notes, removed = [], {}
    for n, parts, idxs in groups:
        if n not in refs:
            continue
        text = ""
        for part in parts:
            text = join(text, part.strip()) if text else part.strip()
        notes.append(Note(n, text, idxs))
        for i in idxs:
            removed[i] = True
    return removed, notes
