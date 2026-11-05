"""Where every case stands, from the calls only: ทั้งหมด → โทรเสร็จสิ้น / ยังไม่ได้โทร; โทรเสร็จสิ้น → ติดต่อได้
(ประสงค์ / ไม่ประสงค์ดำเนินคดี) / ติดต่อไม่ได้ (→ ออกหมายเรียก). Used for the call-list tabs, the Sankey diagram
and the statistics page, so every page shows the same numbers.

Until someone records a call in this system, the register's "บันทึกการติดต่อ" column decides (calls made
before the system). The case status does not decide whether a case was called.

"สิ้นสุด" = status final, or ไม่ประสงค์ดำเนินคดี (same rule as Case.objects.final()).
"""
from __future__ import annotations

import logging

from django.db.models import Exists, OuterRef, Q

from .models import REACHED, CallOutcome

logger = logging.getLogger(__name__)

STATUS_OPEN = Q(case__status__isnull=True) | Q(case__status__is_final=False)
NO_SYSTEM_CALL = Q(attempt_count=0)
SYSTEM_CALL = ~NO_SYSTEM_CALL
FILE_LOG = ~Q(case__contact_log="")
FILE_ONLY = NO_SYSTEM_CALL & FILE_LOG
FILE_REACHED = (Q(case__contact_log__contains="ประสง") | Q(case__contact_log__contains="ติดต่อแล้ว")
                | (Q(case__contact_log__contains="ติดต่อได้") & ~Q(case__contact_log__contains="ไม่สามารถติดต่อ")))
FILE_NOT_REACHED = Q(case__contact_log__contains="ไม่สามารถติดต่อ") & ~FILE_REACHED
FILE_NOT_WANT = Q(case__contact_log__contains="ไม่ประสง")
FILE_WANT = Q(case__contact_log__contains="ประสง") & ~FILE_NOT_WANT

CALLED = SYSTEM_CALL | FILE_LOG  # โทรเสร็จสิ้น: a call in this system or the register's contact log
NEVER = ~CALLED
REACHED_Q = (SYSTEM_CALL & Q(last_outcome__in=REACHED)) | (FILE_ONLY & FILE_REACHED)
NOT_REACHED_Q = (SYSTEM_CALL & ~Q(last_outcome__in=REACHED)) | (FILE_ONLY & FILE_NOT_REACHED)
UNKNOWN_Q = CALLED & ~REACHED_Q & ~NOT_REACHED_Q
WANT_Q = ((SYSTEM_CALL & Q(last_outcome__in=[CallOutcome.APPOINTED, CallOutcome.INFO]))
          | (FILE_ONLY & FILE_WANT))
NOT_WANT_Q = (SYSTEM_CALL & Q(last_outcome=CallOutcome.NOT_PROCEED)) | (FILE_ONLY & FILE_NOT_WANT)
UNDECIDED_Q = REACHED_Q & ~WANT_Q & ~NOT_WANT_Q
OPEN_CASE = STATUS_OPEN & ~NOT_WANT_Q  # ยังไม่สิ้นสุด
ISSUED = Q(has_summons=True)

# The users' view. Each split adds up exactly, and every number shown is the size of the list it opens:
#   ทั้งหมด = โทรเสร็จสิ้น + ยังไม่ได้โทร
#   โทรเสร็จสิ้น = ติดต่อได้ + ติดต่อไม่ได้ ("ติดต่อไม่ได้" includes calls whose result was never written down)
#   ติดต่อได้ = ประสงค์ + ไม่ประสงค์ + ยังไม่ระบุ
#   ติดต่อไม่ได้ = รอออกหมายเรียก + ออกหมายเรียกแล้ว + คดีสิ้นสุดแล้ว (status final: no summons)
NOT_REACHED_ALL = CALLED & ~REACHED_Q

# key -> (label, filter); the order is the order of the tabs / nodes
STAGES = {
    "all": ("ทั้งหมด", Q()),
    "called": ("โทรเสร็จสิ้น", CALLED),
    "reached": ("ติดต่อได้", REACHED_Q),
    "want": ("ประสงค์ดำเนินคดี", WANT_Q),
    "not_want": ("ไม่ประสงค์ดำเนินคดี", NOT_WANT_Q),
    "undecided": ("ติดต่อได้ · ยังไม่ระบุ", UNDECIDED_Q),
    "not_reached": ("ติดต่อไม่ได้", NOT_REACHED_ALL),
    "to_issue": ("รอออกหมายเรียก", NOT_REACHED_ALL & ~ISSUED & STATUS_OPEN),
    "issued": ("ออกหมายเรียกแล้ว", NOT_REACHED_ALL & ISSUED),
    "closed_nr": ("ติดต่อไม่ได้ · คดีสิ้นสุดแล้ว", NOT_REACHED_ALL & ~ISSUED & ~STATUS_OPEN),
    "never": ("ยังไม่ได้โทร", NEVER),
}
TAB_KEYS = ["all", "called", "reached", "want", "not_want", "not_reached", "to_issue", "issued", "never"]

# the diagram: columns of nodes and each node's parent
COLUMNS = [["all"], ["called", "never"], ["reached", "not_reached"],
           ["want", "not_want", "undecided", "to_issue", "issued", "closed_nr"]]
PARENT = {"called": "all", "never": "all", "reached": "called", "not_reached": "called", "want": "reached",
          "not_want": "reached", "undecided": "reached", "to_issue": "not_reached", "issued": "not_reached",
          "closed_nr": "not_reached"}
TONE = {"all": "neutral", "called": "neutral", "never": "muted", "reached": "good", "want": "good",
        "not_want": "plain", "undecided": "muted", "not_reached": "drop", "to_issue": "drop", "issued": "done",
        "closed_nr": "muted"}
COLORS = {"good": "var(--series-1)", "drop": "var(--series-2)", "done": "var(--series-3)",
          "plain": "var(--plain-node)", "muted": "var(--muted-node)", "neutral": "var(--text-muted)"}


def with_summons_flag(qs):
    from apps.documents.models import Summons

    return qs.annotate(has_summons=Exists(Summons.objects.filter(case_id=OuterRef("case_id"))))


def stage(qs, key):
    return qs.filter(STAGES[key][1]) if key in STAGES else qs


def counts(qs) -> dict:
    n = {key: qs.filter(q).count() for key, (_, q) in STAGES.items()}
    if not adds_up(n):  # a rule changed and the numbers would disagree with each other: make it visible
        logger.error("funnel counts do not add up: %s", n)
    return n


def adds_up(n: dict) -> bool:
    return (n["all"] == n["called"] + n["never"] and n["called"] == n["reached"] + n["not_reached"]
            and n["reached"] == n["want"] + n["not_want"] + n["undecided"]
            and n["not_reached"] == n["to_issue"] + n["issued"] + n["closed_nr"])


# shorter names inside the diagram (the column already says "ประสงค์ดำเนินคดี")
DIAGRAM_LABEL = {"undecided": "ยังไม่ระบุ", "closed_nr": "คดีสิ้นสุดแล้ว"}


def sankey(n: dict, width=1040, height=320, node_w=16, gap=22, label_w=230, label_gap=40) -> dict:
    """Node rectangles and link bands for the funnel (all in SVG units)."""
    total = n.get("all", 0)
    if not total:
        return {"empty": True}
    cols = len(COLUMNS)
    step = (width - label_w - node_w) / (cols - 1)
    usable = height - gap * (max(len(c) for c in COLUMNS) - 1)
    scale = usable / total
    nodes = {}
    for ci, column in enumerate(COLUMNS):
        y, last_label = 0.0, -1e9
        for key in column:
            if not n.get(key):
                continue
            parent = nodes.get(PARENT.get(key, ""))
            if parent:  # each group of children starts level with its parent: short bands, no crossings
                y = max(y, parent["y"])
            h = max(n[key] * scale, 3.0)
            ly = max(y + (h / 2 - 3 if h > 40 else 11), last_label + label_gap)  # labels never overlap
            last_label = ly
            nodes[key] = {"key": key, "label": DIAGRAM_LABEL.get(key, STAGES[key][0]), "n": n[key],
                          "full_label": STAGES[key][0], "x": round(ci * step, 1),
                          "y": round(y, 1), "h": round(h, 1), "w": node_w, "color": COLORS[TONE[key]],
                          "pct": round(n[key] / total * 100), "out": 0.0,
                          "lx": round(ci * step + node_w + 8, 1), "ly": round(ly, 1)}
            y += h + gap
    links = []
    for key, node in nodes.items():
        parent = nodes.get(PARENT.get(key, ""))
        if not parent:
            continue
        h = node["h"]
        y0 = parent["y"] + parent["out"]
        parent["out"] += h
        x0, x1 = parent["x"] + node_w, node["x"]
        xm = (x0 + x1) / 2
        y1 = node["y"]
        links.append({"key": key, "color": node["color"], "label": f"{parent['label']} → {node['label']}",
                      "n": node["n"],
                      "d": (f"M{x0:.1f},{y0:.1f} C{xm:.1f},{y0:.1f} {xm:.1f},{y1:.1f} {x1:.1f},{y1:.1f} "
                            f"L{x1:.1f},{y1 + h:.1f} "
                            f"C{xm:.1f},{y1 + h:.1f} {xm:.1f},{y0 + h:.1f} {x0:.1f},{y0 + h:.1f} Z")})
    bottom = max(max(nd["y"] + nd["h"], nd["ly"] + 20) for nd in nodes.values())
    return {"empty": False, "w": width, "h": round(bottom + 4, 1), "nodes": list(nodes.values()), "links": links,
            "node_w": node_w}
