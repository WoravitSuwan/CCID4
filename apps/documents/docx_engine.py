"""Placeholder replacement for .docx templates.

Supports the single-brace Thai placeholders staff already use (``{ชื่อผู้แจ้ง}``)
even when Word split them across several runs, everywhere text can live: body
paragraphs, tables (also nested), text boxes (``w:txbxContent``, both the DrawingML
choice and the VML fallback), headers, footers, footnotes and endnotes.

The replacement text takes the formatting of the run where the placeholder starts.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import nsmap, qn
from lxml import etree

PLACEHOLDER_RE = re.compile(r"\{([^{}\n]+)\}")
LEGACY_APPOINTMENT_TOKEN = "+14"
TEXT_PART_RE = re.compile(r"^/word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$")

_NS = {"w": nsmap["w"]}
_PARAGRAPH_TEXT_XPATH = (
    "./w:r/w:t | ./w:hyperlink/w:r/w:t | ./w:ins/w:r/w:t | ./w:smartTag/w:r/w:t"
    " | ./w:fldSimple/w:r/w:t | ./w:sdt/w:sdtContent/w:r/w:t | ./w:customXml/w:r/w:t"
)
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


@dataclass
class RenderReport:
    used: set = field(default_factory=set)
    unknown: set = field(default_factory=set)
    missing: set = field(default_factory=set)
    legacy_appointment: bool = False
    leftovers: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unknown or self.missing or self.leftovers)

    def problems(self) -> list[str]:
        out = []
        if self.unknown:
            out.append("ตัวแทนที่ระบบไม่รู้จัก: " + ", ".join(sorted(f"{{{n}}}" for n in self.unknown)))
        if self.missing:
            out.append("ตัวแทนที่ไม่มีค่า: " + ", ".join(sorted(f"{{{n}}}" for n in self.missing)))
        if self.leftovers:
            out.append("ยังมีข้อความตัวแทนหลงเหลือในเอกสาร: " + ", ".join(self.leftovers[:10]))
        return out


# --------------------------------------------------------------------------- traversal
def text_parts(doc):
    """XML root elements of every part that can hold visible text."""
    seen = set()
    for part in doc.part.package.iter_parts():
        name = str(part.partname)
        if TEXT_PART_RE.match(name) and hasattr(part, "element") and name not in seen:
            seen.add(name)
            yield part.element


def iter_paragraphs(root):
    yield from root.iter(qn("w:p"))


def paragraph_text_nodes(p):
    return etree._Element.xpath(p, _PARAGRAPH_TEXT_XPATH, namespaces=_NS)


def _set_text(node, text):
    """Set a w:t's text; '\n' in the text becomes a line break (w:br) in the same run."""
    first, *rest = (text or "").split("\n")
    node.text = first
    if first and (first[0].isspace() or first[-1].isspace()):
        node.set(XML_SPACE, "preserve")
    anchor = node
    for line in rest:
        br = node.makeelement(qn("w:br"), {})
        anchor.addnext(br)
        t = node.makeelement(qn("w:t"), {})
        t.text = line
        t.set(XML_SPACE, "preserve")
        br.addnext(t)
        anchor = t


def replace_in_paragraph(p, resolver) -> int:
    """Replace tokens inside one paragraph.

    ``resolver(full_text)`` returns a list of (start, end, replacement) spans.
    Returns the number of replacements done.
    """
    nodes = paragraph_text_nodes(p)
    if not nodes:
        return 0
    full = "".join(n.text or "" for n in nodes)
    spans = resolver(full)
    if not spans:
        return 0
    # drop overlapping spans, apply right-to-left so earlier offsets stay valid
    spans = sorted(spans, key=lambda s: (s[0], -s[1]))
    clean, last_end = [], -1
    for s in spans:
        if s[0] >= last_end:
            clean.append(s)
            last_end = s[1]
    for start, end, repl in reversed(clean):
        texts = [n.text or "" for n in nodes]
        offsets, pos = [], 0
        for t in texts:
            offsets.append(pos)
            pos += len(t)
        i = next(k for k in range(len(nodes)) if offsets[k] <= start < offsets[k] + len(texts[k]))
        j = next(k for k in range(len(nodes)) if offsets[k] < end <= offsets[k] + len(texts[k]))
        head = texts[i][: start - offsets[i]]
        tail = texts[j][end - offsets[j]:]
        if i == j:
            _set_text(nodes[i], head + repl + tail)
        else:
            _set_text(nodes[i], head + repl)
            for k in range(i + 1, j):
                _set_text(nodes[k], "")
            _set_text(nodes[j], tail)
    return len(clean)


# --------------------------------------------------------------------------- legacy "+14" form
def _cell_text(tc) -> str:
    return "".join(t.text or "" for t in tc.iter(qn("w:t")))


def _first_run_props(tc):
    r = tc.find(".//" + qn("w:r"))
    if r is not None:
        rpr = r.find(qn("w:rPr"))
        return copy.deepcopy(rpr) if rpr is not None else None
    return None


_TCPR_ORDER = ["cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar",
               "textDirection", "tcFitText", "vAlign", "hideMark", "headers", "cellIns", "cellDel",
               "cellMerge", "tcPrChange"]


def _tcpr_child(tcpr, name):
    """Get or create a w:tcPr child, inserted at its schema position (Word is strict)."""
    el = tcpr.find(qn(f"w:{name}"))
    if el is not None:
        return el
    el = tcpr.makeelement(qn(f"w:{name}"), {})
    later = set(_TCPR_ORDER[_TCPR_ORDER.index(name) + 1:])
    for idx, child in enumerate(tcpr):
        if etree.QName(child).localname in later:
            tcpr.insert(idx, el)
            return el
    tcpr.append(el)
    return el


def _no_wrap(tc):
    """Keep short values (dates, times) on one line in narrow cells."""
    tcpr = tc.find(qn("w:tcPr"))
    if tcpr is None:
        tcpr = tc.makeelement(qn("w:tcPr"), {})
        tc.insert(0, tcpr)
    _tcpr_child(tcpr, "noWrap")
    # zero left/right padding: the day cell of the original form is only ~1 cm wide
    mar = _tcpr_child(tcpr, "tcMar")
    for side in ("left", "right"):  # CT_TcMar order: top, start/left, bottom, end/right
        el = mar.find(qn(f"w:{side}"))
        if el is None:
            el = mar.makeelement(qn(f"w:{side}"), {})
            mar.append(el)
        el.set(qn("w:w"), "0")
        el.set(qn("w:type"), "dxa")
    order = {"top": 0, "left": 1, "start": 1, "bottom": 2, "right": 3, "end": 3}
    children = sorted(mar, key=lambda c: order.get(etree.QName(c).localname, 9))
    for c in children:
        mar.append(c)


def _put_text_in_cell(tc, text, rpr_source):
    p = tc.find(qn("w:p"))
    if p is None:
        p = tc.makeelement(qn("w:p"), {})
        tc.append(p)
    r = p.makeelement(qn("w:r"), {})
    rpr = _first_run_props(rpr_source)
    if rpr is None:
        ppr_rpr = p.find(qn("w:pPr") + "/" + qn("w:rPr"))
        rpr = copy.deepcopy(ppr_rpr) if ppr_rpr is not None else None
    if rpr is not None:
        for hidden in rpr.findall(qn("w:vanish")):
            rpr.remove(hidden)  # labels on the form are hidden text; values must print
        r.append(rpr)
    _no_wrap(tc)
    t = r.makeelement(qn("w:t"), {})
    _set_text(t, text)
    r.append(t)
    p.append(r)


def upgrade_legacy_appointment_row(root) -> bool:
    """Original form: 'ในวันที่ | +14 | เดือน | ___ | พ.ศ. ๒๕ | ___'.

    Put {วันนัด} / {เดือนนัด} / {ปีนัด} into that row so the appointment date is
    complete. Returns True if the legacy row was found.
    """
    found = False
    for tr in root.iter(qn("w:tr")):
        cells = tr.findall(qn("w:tc"))
        texts = [_cell_text(tc).strip() for tc in cells]
        if LEGACY_APPOINTMENT_TOKEN not in texts:
            continue
        found = True
        start = texts.index(LEGACY_APPOINTMENT_TOKEN)
        _no_wrap(cells[start])
        for k in range(start + 1, len(cells) - 1):
            label = texts[k]
            nxt = k + 1
            if texts[nxt]:
                continue
            if label == "เดือน":
                _put_text_in_cell(cells[nxt], "{เดือนนัด}", cells[k])
                texts[nxt] = "{เดือนนัด}"
            elif label.replace(" ", "").startswith("พ.ศ."):
                _put_text_in_cell(cells[nxt], "{ปีนัด}", cells[k])
                texts[nxt] = "{ปีนัด}"
    return found


# --------------------------------------------------------------------------- public API
def unhide_text(root) -> int:
    """Remove 'hidden text' (w:vanish) so labels print on blank paper."""
    count = 0
    for el in list(root.iter(qn("w:vanish"))):
        el.getparent().remove(el)
        count += 1
    return count


def render(template_path, values: dict, out_path, literals: dict | None = None,
           show_hidden: bool = False, optional=frozenset()) -> RenderReport:
    """Fill ``template_path`` with ``values`` ({name: text}) and save to ``out_path``.

    ``literals`` maps exact strings to replacements (used for '+14' and for
    building template versions). Unknown placeholders are left in place and
    reported; known ones with an empty value are reported as missing.
    """
    doc = Document(str(template_path))
    report = RenderReport()
    literals = dict(literals or {})
    roots = list(text_parts(doc))
    for root in roots:
        if show_hidden:
            unhide_text(root)
        if upgrade_legacy_appointment_row(root):
            report.legacy_appointment = True
    if report.legacy_appointment:
        literals.setdefault(LEGACY_APPOINTMENT_TOKEN, "{วันนัด}")

    def resolver(text):
        spans = []
        for literal, repl in literals.items():
            for m in re.finditer(re.escape(literal), text):
                spans.append((m.start(), m.end(), repl))
        return spans

    def placeholder_resolver(text):
        spans = []
        for m in PLACEHOLDER_RE.finditer(text):
            name = m.group(1).strip()
            if name not in values:
                report.unknown.add(name)
                continue
            value = values[name]
            report.used.add(name)
            if (value is None or str(value).strip() == "") and name not in optional:
                report.missing.add(name)
                value = ""
            spans.append((m.start(), m.end(), str(value)))
        return spans

    for root in roots:
        for p in iter_paragraphs(root):
            if literals:
                replace_in_paragraph(p, resolver)
            replace_in_paragraph(p, placeholder_resolver)

    for root in roots:
        for p in iter_paragraphs(root):
            text = "".join(n.text or "" for n in paragraph_text_nodes(p))
            for m in re.finditer(r"\{[^{}]*\}|[{}]", text):
                report.leftovers.append(m.group(0))
            if LEGACY_APPOINTMENT_TOKEN in text:
                report.leftovers.append(LEGACY_APPOINTMENT_TOKEN)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return report


def scan_placeholders(template_path) -> dict:
    """Placeholders used by a template (after merging split runs)."""
    doc = Document(str(template_path))
    names, legacy = [], False
    for root in text_parts(doc):
        for tr in root.iter(qn("w:tr")):
            if LEGACY_APPOINTMENT_TOKEN in [_cell_text(tc).strip() for tc in tr.findall(qn("w:tc"))]:
                legacy = True
        for p in iter_paragraphs(root):
            text = "".join(n.text or "" for n in paragraph_text_nodes(p))
            for m in PLACEHOLDER_RE.finditer(text):
                name = m.group(1).strip()
                if name not in names:
                    names.append(name)
    if legacy:
        for extra in ("วันนัด", "เดือนนัด", "ปีนัด"):
            if extra not in names:
                names.append(extra)
    return {"placeholders": names, "legacy_appointment": legacy}


def document_text(path) -> str:
    """All visible text of a .docx (body, tables, text boxes, headers/footers)."""
    doc = Document(str(path))
    lines = []
    for root in text_parts(doc):
        for p in iter_paragraphs(root):
            lines.append("".join(n.text or "" for n in paragraph_text_nodes(p)))
    return "\n".join(lines)
