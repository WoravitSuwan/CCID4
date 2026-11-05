"""Where every case stands: ทั้งหมด → สิ้นสุด (สถานะสิ้นสุด / ไม่ประสงค์ดำเนินคดี) / ยังไม่สิ้นสุด (ประสงค์ดำเนินคดี
→ ออกหมายเรียก / ยังไม่ทราบความประสงค์). Used for the call-list tabs, the Sankey diagram and the statistics page,
so every page shows the same numbers.

Until someone records a call in this system, the register's "บันทึกการติดต่อ" column decides (calls made
before the system). Closed cases count as called.

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

CALLED = SYSTEM_CALL | FILE_LOG | ~STATUS_OPEN
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

# The users' view, one partition of every case (each case is in exactly one of the last four; every
# number shown is the size of the list it opens):
#   ทั้งหมด = สิ้นสุด (สถานะสิ้นสุด + ไม่ประสงค์ดำเนินคดี) + ยังไม่สิ้นสุด (ประสงค์ดำเนินคดี + ยังไม่ทราบความประสงค์)
# A case whose status is final stays under "สถานะสิ้นสุด" whatever the calls say.
FINAL_STATUS = ~STATUS_OPEN
NOT_WANT_OPEN = STATUS_OPEN & NOT_WANT_Q
WANT_OPEN = OPEN_CASE & WANT_Q
UNKNOWN_OPEN = OPEN_CASE & ~WANT_Q
NOT_REACHED_ALL = CALLED & ~REACHED_Q  # for the result filter: called but not reached

# key -> (label, filter); the order is the order of the tabs / nodes
STAGES = {
    "all": ("ทั้งหมด", Q()),
    "final": ("สิ้นสุด", ~OPEN_CASE),
    "final_status": ("สถานะคดีสิ้นสุด", FINAL_STATUS),
    "not_want": ("ไม่ประสงค์ดำเนินคดี", NOT_WANT_OPEN),
    "open": ("ยังไม่สิ้นสุด", OPEN_CASE),
    "want": ("ประสงค์ดำเนินคดี", WANT_OPEN),
    "want_todo": ("ประสงค์ · รอออกหมายเรียก", WANT_OPEN & ~ISSUED),
    "want_issued": ("ประสงค์ · ออกหมายเรียกแล้ว", WANT_OPEN & ISSUED),
    "unknown": ("ยังไม่ทราบความประสงค์", UNKNOWN_OPEN),
    # inside "ยังไม่ทราบความประสงค์" (filters on the call list, not in the diagram)
    "never": ("ยังไม่ได้โทร", UNKNOWN_OPEN & NEVER),
    "not_reached": ("โทรแล้ว ติดต่อไม่ได้", UNKNOWN_OPEN & NOT_REACHED_ALL),
    "undecided": ("ติดต่อได้ ยังไม่ระบุ", UNKNOWN_OPEN & REACHED_Q),
}
TAB_KEYS = ["all", "final", "not_want", "open", "want", "want_todo", "want_issued", "unknown"]
UNKNOWN_PARTS = ["never", "not_reached", "undecided"]

# the diagram: columns of nodes and each node's parent
COLUMNS = [["all"], ["final", "open"], ["final_status", "not_want", "want", "unknown"], ["want_todo", "want_issued"]]
PARENT = {"final": "all", "open": "all", "final_status": "final", "not_want": "final", "want": "open",
          "unknown": "open", "want_todo": "want", "want_issued": "want"}
TONE = {"all": "neutral", "final": "plain", "final_status": "plain", "not_want": "plain", "open": "good",
        "want": "good", "want_todo": "drop", "want_issued": "done", "unknown": "muted"}
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
    return (n["final_status"] + n["not_want"] + n["want"] + n["unknown"] == n["all"]
            and n["final"] == n["final_status"] + n["not_want"] and n["open"] == n["want"] + n["unknown"]
            and n["want"] == n["want_todo"] + n["want_issued"]
            and n["unknown"] == sum(n[k] for k in UNKNOWN_PARTS))


# shorter names inside the diagram (the column already says "ประสงค์ดำเนินคดี")
DIAGRAM_LABEL = {"want_todo": "รอออกหมายเรียก", "want_issued": "ออกหมายเรียกแล้ว"}


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
