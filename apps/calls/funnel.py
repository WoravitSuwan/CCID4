"""Where every case stands: ทั้งหมด → โทรเสร็จสิ้น / ยังไม่ได้โทร → ติดต่อได้ / ไม่ได้ → ประสงค์ / ไม่ประสงค์
→ ออกหมายแล้ว. Used for the call-list tabs, the Sankey diagram and the statistics page.

Until someone records a call in this system, the register's "บันทึกการติดต่อ" column decides (calls made
before the system). Closed cases count as called.
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from .models import REACHED, CallOutcome

OPEN_CASE = Q(case__status__isnull=True) | Q(case__status__is_final=False)
NO_SYSTEM_CALL = Q(attempt_count=0)
SYSTEM_CALL = ~NO_SYSTEM_CALL
FILE_LOG = ~Q(case__contact_log="")
FILE_ONLY = NO_SYSTEM_CALL & FILE_LOG
FILE_REACHED = (Q(case__contact_log__contains="ประสง") | Q(case__contact_log__contains="ติดต่อแล้ว")
                | (Q(case__contact_log__contains="ติดต่อได้") & ~Q(case__contact_log__contains="ไม่สามารถติดต่อ")))
FILE_NOT_REACHED = Q(case__contact_log__contains="ไม่สามารถติดต่อ") & ~FILE_REACHED
FILE_NOT_WANT = Q(case__contact_log__contains="ไม่ประสง")
FILE_WANT = Q(case__contact_log__contains="ประสง") & ~FILE_NOT_WANT

CALLED = SYSTEM_CALL | FILE_LOG | ~OPEN_CASE
NEVER = ~CALLED
REACHED_Q = (SYSTEM_CALL & Q(last_outcome__in=REACHED)) | (FILE_ONLY & FILE_REACHED)
NOT_REACHED_Q = (SYSTEM_CALL & ~Q(last_outcome__in=REACHED)) | (FILE_ONLY & FILE_NOT_REACHED)
UNKNOWN_Q = CALLED & ~REACHED_Q & ~NOT_REACHED_Q
WANT_Q = ((SYSTEM_CALL & Q(last_outcome__in=[CallOutcome.APPOINTED, CallOutcome.INFO]))
          | (FILE_ONLY & FILE_WANT))
NOT_WANT_Q = (SYSTEM_CALL & Q(last_outcome=CallOutcome.NOT_PROCEED)) | (FILE_ONLY & FILE_NOT_WANT)
UNDECIDED_Q = REACHED_Q & ~WANT_Q & ~NOT_WANT_Q
ISSUED = Q(has_summons=True)

# key -> (label, filter); the order is the order of the tabs / nodes
STAGES = {
    "all": ("ทั้งหมด", Q()),
    "never": ("ยังไม่ได้โทร", NEVER),
    "called": ("โทรเสร็จสิ้น", CALLED),
    "reached": ("ติดต่อได้", REACHED_Q),
    "not_reached": ("ติดต่อไม่ได้", NOT_REACHED_Q),
    "unknown": ("โทรแล้ว ไม่ทราบผล", UNKNOWN_Q),
    "want": ("ประสงค์ดำเนินคดี", WANT_Q),
    "not_want": ("ไม่ประสงค์ดำเนินคดี", NOT_WANT_Q),
    "undecided": ("ติดต่อได้ ยังไม่ระบุ", UNDECIDED_Q),
    "want_issued": ("ประสงค์ · ออกหมายแล้ว", WANT_Q & ISSUED),
    "want_pending": ("ประสงค์ · ยังไม่ออกหมาย", WANT_Q & ~ISSUED),
    "issued": ("ออกหมายแล้ว", ISSUED),
}
TAB_KEYS = ["all", "never", "called", "reached", "not_reached", "want", "not_want", "issued"]

# the diagram: columns of nodes, each node's parent; drop-offs are orange, the main path blue
COLUMNS = [["all"], ["called", "never"], ["reached", "not_reached", "unknown"],
           ["want", "not_want", "undecided"], ["want_issued", "want_pending"]]
PARENT = {"called": "all", "never": "all", "reached": "called", "not_reached": "called", "unknown": "called",
          "want": "reached", "not_want": "reached", "undecided": "reached",
          "want_issued": "want", "want_pending": "want"}
TONE = {"all": "neutral", "called": "main", "reached": "main", "want": "main", "want_issued": "main",
        "never": "muted", "unknown": "muted", "undecided": "muted", "want_pending": "muted",
        "not_reached": "drop", "not_want": "drop"}
COLORS = {"main": "var(--series-1)", "drop": "var(--series-2)", "muted": "var(--muted-node)",
          "neutral": "var(--text-muted)"}


def with_summons_flag(qs):
    from apps.documents.models import Summons

    return qs.annotate(has_summons=Exists(Summons.objects.filter(case_id=OuterRef("case_id"))))


def stage(qs, key):
    return qs.filter(STAGES[key][1]) if key in STAGES else qs


def counts(qs) -> dict:
    return {key: qs.filter(q).count() for key, (_, q) in STAGES.items()}


# shorter names inside the diagram (the column already says "ประสงค์ดำเนินคดี")
DIAGRAM_LABEL = {"want_issued": "ออกหมายแล้ว", "want_pending": "ยังไม่ออกหมาย", "undecided": "ยังไม่ระบุ",
                 "unknown": "ไม่ทราบผล"}


def sankey(n: dict, width=1040, height=300, node_w=14, gap=16, label_w=190) -> dict:
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
        y = 0.0
        parent = nodes.get(PARENT.get(column[0], ""))
        if parent:  # line children up with their parent's top so the bands stay short and do not cross
            y = parent["y"]
        for key in column:
            if not n.get(key):
                continue
            h = max(n[key] * scale, 2.0)
            nodes[key] = {"key": key, "label": DIAGRAM_LABEL.get(key, STAGES[key][0]), "n": n[key],
                          "full_label": STAGES[key][0], "x": round(ci * step, 1),
                          "y": round(y, 1), "h": round(h, 1), "w": node_w, "color": COLORS[TONE[key]],
                          "pct": round(n[key] / total * 100), "out": 0.0,
                          "lx": round(ci * step + node_w + 6, 1),  # label: right of the node,
                          "ly": round(y + (h / 2 + 5 if h > 26 else 12), 1)}  # centred on tall nodes
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
    bottom = max(nd["y"] + nd["h"] for nd in nodes.values())
    return {"empty": False, "w": width, "h": round(bottom + 4, 1), "nodes": list(nodes.values()), "links": links,
            "node_w": node_w}
