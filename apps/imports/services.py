"""Excel import pipeline: read -> clean -> diff against DB -> commit (upsert)."""
from __future__ import annotations

import datetime as dt
import logging
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import openpyxl
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.thai_format import normalize_year_to_ce
from apps.registry.address import clean_text
from apps.registry.defaults import FINAL_BY_DEFAULT
from apps.registry.models import (
    Case, Complainant, InvestigatorAlias, Status, Unit, is_own_unit, link_register_names, other_unit_cases, unit_key,
    normalize_person_key, normalize_receipt_no,
)

from .models import BatchStatus, ColumnMapping, ImportBatch, ImportIssue, ImportKind, IssueType
from .postcode import lookup_postcode
from .schema import COLUMNS, DEFAULT_SHEET, auto_map, field_labels

logger = logging.getLogger(__name__)

RECEIPT_RE = re.compile(r"^[LWTC]\d{5,12}$")
CHUNK = 1000

COMPLAINANT_FIELDS = [f for f, _, _ in COLUMNS[ImportKind.COMPLAINANTS] if f != "receipt_no"] + ["postcode_auto"]
CASE_TEXT_FIELDS = [
    "seq", "criminal_case_no", "victim_name", "gender", "age", "case_type", "case_type_l1",
    "case_type_l2", "case_type_l3", "case_group", "detail", "bureau", "division", "unit_raw",
    "investigator_raw", "detective", "victim_phone", "bank_ref", "contact_log", "daily_log", "remark",
]
CASE_COMPARE_FIELDS = CASE_TEXT_FIELDS + ["report_date", "damage_amount", "status_name", "source_sheet"]


# --------------------------------------------------------------------------- reading
def workbook_info(path) -> list[dict]:
    """Sheet names with their header row (first row)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        info = []
        for ws in wb.worksheets:
            headers = []
            for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                headers = [clean_text(c) for c in row if clean_text(c)]
            info.append({"name": ws.title, "headers": headers})
        return info
    finally:
        wb.close()


def cell_text(value) -> str:
    """Excel cell -> clean text. Numbers keep their digits (no '.0', no sci notation)."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, dt.datetime):
        if value.hour or value.minute:
            return value.strftime("%d/%m/%Y %H:%M")
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dt.date):
        return value.strftime("%d/%m/%Y")
    return clean_text(value)


def read_sheet(path, sheet: str) -> tuple[list[str], list[tuple[int, dict]]]:
    """Return (headers, [(excel_row_number, {header: raw_value})]).

    Read with openpyxl directly (streaming, values only): cells keep their Excel
    type (int / float / str / datetime) so leading zeros of text cells survive.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet]
        values = ws.iter_rows(values_only=True)
        header_row = next(values, ()) or ()
        headers = []
        for idx, cell in enumerate(header_row):
            name = clean_text(cell) or f"Unnamed: {idx}"
            while name in headers:  # duplicate header names stay distinct
                name += "_"
            headers.append(name)
        rows = []
        for idx, record in enumerate(values):
            if record is None or all(v is None or (isinstance(v, str) and not v.strip()) for v in record):
                continue
            rows.append((idx + 2, dict(zip(headers, record))))
        return headers, rows
    finally:
        wb.close()


# --------------------------------------------------------------------------- parsing helpers
def parse_int(value):
    text = cell_text(value).replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_decimal(value):
    if value is None:
        return None, True
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        return Decimal(str(value)).quantize(Decimal("0.01")), True
    text = cell_text(value).replace(",", "").replace("บาท", "").strip()
    if text in {"", "-"}:
        return None, True
    try:
        return Decimal(text).quantize(Decimal("0.01")), True
    except InvalidOperation:
        return None, False


def parse_log_datetime(value):
    """'dd/mm/yyyy HH:MM' (ค.ศ., or พ.ศ. if year > 2400) -> aware datetime."""
    if isinstance(value, dt.datetime):
        return timezone.make_aware(value.replace(tzinfo=None))
    text = cell_text(value)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2})[:.](\d{2}))?", text)
    if not m:
        return None
    day, month, year = int(m[1]), int(m[2]), normalize_year_to_ce(int(m[3]))
    hour, minute = int(m[4] or 0), int(m[5] or 0)
    try:
        return timezone.make_aware(dt.datetime(year, month, day, hour, minute))
    except ValueError:
        return None


def clean_prefix(value) -> str:
    return clean_text(value).lstrip("-–").strip()


# --------------------------------------------------------------------------- data classes
@dataclass
class ParsedRow:
    sheet: str
    row_number: int
    key: str
    values: dict


@dataclass
class Analysis:
    kind: str
    rows: "OrderedDict[str, ParsedRow]" = field(default_factory=OrderedDict)
    issues: list = field(default_factory=list)
    total_rows: int = 0
    skipped_blank: int = 0
    new_keys: list = field(default_factory=list)
    updated_keys: list = field(default_factory=list)
    unchanged: int = 0
    change_counts: Counter = field(default_factory=Counter)
    unmatched_keys: list = field(default_factory=list)
    new_statuses: list = field(default_factory=list)
    new_aliases: list = field(default_factory=list)
    unlinked_aliases: list = field(default_factory=list)
    new_units: list = field(default_factory=list)
    manual_status_kept: set = field(default_factory=set)
    manual_status_released: set = field(default_factory=set)
    per_sheet: Counter = field(default_factory=Counter)
    other_units: Counter = field(default_factory=Counter)  # register rows of another unit: not imported
    other_unit_keys: list = field(default_factory=list)
    # weekly update from the big system
    present: list = field(default_factory=list)  # fields the file has (others are never touched)
    changes_by_key: dict = field(default_factory=dict)  # key -> {field: new value}
    status_changes: Counter = field(default_factory=Counter)  # (old, new) -> n
    looks_like_update: bool = False  # a big-system file picked as "ทะเบียนคดี"
    ranks: dict = field(default_factory=dict)  # investigator key -> full rank written by the big system
    remove_count: int = 0  # cases of another unit already in the system, removed on import
    remove_calls: int = 0
    kept_with_summons: int = 0

    def add_issue(self, row: ParsedRow | None, issue_type, message="", sheet="", row_number=None, key=""):
        self.issues.append({
            "sheet": row.sheet if row else sheet,
            "row_number": row.row_number if row else row_number,
            "receipt_no": row.key if row else key,
            "issue_type": issue_type,
            "message": message[:500],
        })

    def issue_rows(self) -> int:
        return len({(i["sheet"], i["row_number"]) for i in self.issues
                    if i["issue_type"] not in {IssueType.UNMATCHED, IssueType.NEW_STATUS,
                                               IssueType.NEW_INVESTIGATOR}})


# --------------------------------------------------------------------------- parse per kind
def _get(raw: dict, mapping: dict, fieldname: str):
    header = mapping.get(fieldname)
    return raw.get(header) if header else None


def parse_complainant_row(raw: dict, mapping: dict) -> dict:
    values = {}
    for fieldname in COMPLAINANT_FIELDS:
        if fieldname == "postcode_auto":
            continue
        values[fieldname] = cell_text(_get(raw, mapping, fieldname))
    values["prefix"] = clean_prefix(values["prefix"])
    if not values["postcode"]:
        values["postcode_auto"] = lookup_postcode(values["subdistrict"], values["district"], values["province"])
    else:
        values["postcode_auto"] = ""
    return values


def parse_register_row(raw: dict, mapping: dict, sheet: str, analysis: Analysis, row: ParsedRow) -> dict:
    values = {name: cell_text(_get(raw, mapping, name)) for name in CASE_TEXT_FIELDS}
    values["status_name"] = cell_text(_get(raw, mapping, "status_name"))
    values["source_sheet"] = sheet
    day = parse_int(_get(raw, mapping, "report_day"))
    month = parse_int(_get(raw, mapping, "report_month"))
    year = parse_int(_get(raw, mapping, "report_year"))
    report_date = None
    raw_day = _get(raw, mapping, "report_day")
    one_column = re.fullmatch(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*", str(raw_day or ""))
    if isinstance(raw_day, dt.datetime):
        report_date = raw_day.date()
    elif isinstance(raw_day, dt.date):
        report_date = raw_day
    elif one_column:  # the big system's export: 01/03/2022 (or a Buddhist-era year)
        d, m, y = (int(x) for x in one_column.groups())
        try:
            report_date = dt.date(normalize_year_to_ce(y), m, d)
        except ValueError:
            report_date = None
    elif day and month and year:
        try:
            report_date = dt.date(normalize_year_to_ce(year), month, day)
        except ValueError:
            report_date = None
    if report_date is None and analysis.kind != ImportKind.UPDATE:  # update: only checked for new cases
        analysis.add_issue(row, IssueType.BAD_DATE, f"วัน/เดือน/ปี = {day}/{month}/{year}")
    values["report_date"] = report_date
    amount, ok = parse_decimal(_get(raw, mapping, "damage_amount"))
    if not ok:
        analysis.add_issue(row, IssueType.BAD_AMOUNT, cell_text(_get(raw, mapping, "damage_amount")))
    values["damage_amount"] = amount
    values["daily_log_at"] = parse_log_datetime(_get(raw, mapping, "daily_log"))
    return values


def complainant_issues(analysis: Analysis, row: ParsedRow):
    tmp = Complainant(**{k: v for k, v in row.values.items()})
    tmp.refresh_flags()
    if tmp.flag_missing_house:
        analysis.add_issue(row, IssueType.MISSING_HOUSE)
    if tmp.flag_missing_area:
        missing = [lbl for name, lbl in (("subdistrict", "ตำบล"), ("district", "อำเภอ"), ("province", "จังหวัด"))
                   if not row.values.get(name)]
        analysis.add_issue(row, IssueType.MISSING_AREA, "ไม่มี " + ", ".join(missing))
    if tmp.flag_bad_id:
        digits = re.sub(r"\D", "", row.values["national_id"])
        analysis.add_issue(row, IssueType.BAD_ID, f"มี {len(digits)} หลัก")


# --------------------------------------------------------------------------- analysis
def analyze(batch: ImportBatch, progress=None) -> Analysis:
    kind = batch.kind
    analysis = Analysis(kind=kind)
    if kind == ImportKind.UPDATE:
        analysis.present = [f for f, _h, _r in COLUMNS[kind] if batch.mapping.get(f)]
        known = set(Case.objects.values_list("receipt_no", flat=True))
    sheets = batch.sheets or [DEFAULT_SHEET.get(kind)] or []
    path = batch.file.path
    for s_index, sheet in enumerate(sheets):
        _headers, rows = read_sheet(path, sheet)
        for row_number, raw in rows:
            key = normalize_receipt_no(_get(raw, batch.mapping, "receipt_no"))
            if not key:
                analysis.skipped_blank += 1
                continue
            analysis.total_rows += 1
            prow = ParsedRow(sheet=sheet, row_number=row_number, key=key, values={})
            if kind == ImportKind.COMPLAINANTS:
                prow.values = parse_complainant_row(raw, batch.mapping)
            elif kind == ImportKind.UPDATE:
                prow.values = parse_register_row(raw, batch.mapping, sheet, analysis, prow)
                ranked = prow.values["investigator_raw"]
                prow.values["investigator_raw"] = register_style_name(ranked)
                rank = _rank_of(ranked)
                if rank:
                    analysis.ranks[normalize_person_key(prow.values["investigator_raw"])] = rank
                unit = prow.values["unit_raw"]
                # a case already here is ours whatever the export's unit column says (it is often empty);
                # a case not here yet is added only when the file names our unit
                if key not in known and not (unit.strip() and is_own_unit(unit)):
                    analysis.total_rows -= 1
                    analysis.other_units[unit.strip() or "(ไม่ระบุหน่วย)"] += 1
                    continue
            else:
                prow.values = parse_register_row(raw, batch.mapping, sheet, analysis, prow)
                if not is_own_unit(prow.values["unit_raw"]):  # this system is for one unit only
                    analysis.total_rows -= 1  # counted apart, in other_units
                    analysis.other_units[prow.values["unit_raw"]] += 1
                    analysis.other_unit_keys.append(key)
                    continue
            if not RECEIPT_RE.match(key):
                analysis.add_issue(prow, IssueType.BAD_KEY, "ควรเป็น L, W, T หรือ C ตามด้วยตัวเลข")
            if key in analysis.rows:
                first = analysis.rows[key]
                analysis.add_issue(prow, IssueType.DUPLICATE_KEY,
                                   f"ซ้ำกับชีต {first.sheet} แถว {first.row_number} (ใช้แถวหลังสุด)")
            analysis.rows[key] = prow
            analysis.rows.move_to_end(key)
        if progress:
            progress(int((s_index + 1) * 60 / max(1, len(sheets))))

    for prow in analysis.rows.values():
        analysis.per_sheet[prow.sheet] += 1
        if kind == ImportKind.COMPLAINANTS:
            complainant_issues(analysis, prow)
    if kind == ImportKind.REGISTER:
        _count_other_unit_cases(analysis)
        # a big-system export picked as "ทะเบียนคดี": no contact log, one date column, mostly no unit
        analysis.looks_like_update = (not batch.mapping.get("contact_log") and not batch.mapping.get("report_month")
                                      and sum(analysis.other_units.values()) + len(analysis.rows) > 0
                                      and sum(1 for p in analysis.rows.values() if not p.values["unit_raw"])
                                      > len(analysis.rows) / 2)
    if kind == ImportKind.UPDATE:
        _diff_update(analysis)
    else:
        _diff(analysis)
    if progress:
        progress(100)
    return analysis


def _other_unit_ids(analysis: Analysis) -> list:
    """Cases in the system that are not this unit's: saved under another unit, or listed under another unit
    in this file. A case this file lists under our unit is never one of them."""
    own_keys = analysis.rows.keys()
    found = dict(other_unit_cases().values_list("pk", "receipt_no"))
    for chunk in _chunks([k for k in analysis.other_unit_keys if k not in own_keys]):
        found.update(Case.objects.filter(receipt_no__in=chunk).values_list("pk", "receipt_no"))
    return [pk for pk, receipt in found.items() if receipt not in own_keys]


def _removable(ids) -> list:
    """A summons is never deleted, so a case that has one stays."""
    from apps.documents.models import Summons

    with_summons = set()
    for chunk in _chunks(ids):
        with_summons.update(Summons.objects.filter(case_id__in=chunk).values_list("case_id", flat=True))
    return [pk for pk in ids if pk not in with_summons]


def _count_other_unit_cases(analysis: Analysis):
    from apps.calls.models import CallAttempt

    ids = _other_unit_ids(analysis)
    removable = _removable(ids)
    analysis.remove_count = len(removable)
    analysis.remove_calls = sum(CallAttempt.objects.filter(case_id__in=chunk).count() for chunk in _chunks(removable))
    analysis.kept_with_summons = len(ids) - len(removable)


def _remove_other_unit_cases(analysis: Analysis) -> int:
    ids = _removable(_other_unit_ids(analysis))
    for chunk in _chunks(ids):
        Case.objects.filter(pk__in=chunk).delete()
    return len(ids)


def _chunks(items, size=CHUNK):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _existing(model, keys, select_related=()):
    out = {}
    for chunk in _chunks(keys):
        qs = model.objects.filter(receipt_no__in=chunk)
        if select_related:
            qs = qs.select_related(*select_related)
        out.update({obj.receipt_no: obj for obj in qs})
    return out


def _case_current(case: Case) -> dict:
    values = {name: getattr(case, name) for name in CASE_TEXT_FIELDS}
    values["report_date"] = case.report_date
    values["damage_amount"] = case.damage_amount
    values["status_name"] = case.status.name if case.status_id else ""
    values["source_sheet"] = case.source_sheet
    return values


def _diff(analysis: Analysis):
    keys = list(analysis.rows)
    if analysis.kind == ImportKind.COMPLAINANTS:
        existing = _existing(Complainant, keys)
        compare = COMPLAINANT_FIELDS
        current = lambda obj: {f: getattr(obj, f) for f in compare}  # noqa: E731
        case_keys = set()
        for chunk in _chunks(keys):
            case_keys.update(Case.objects.filter(receipt_no__in=chunk).values_list("receipt_no", flat=True))
        analysis.unmatched_keys = [k for k in keys if k not in case_keys]
    else:
        existing = _existing(Case, keys, select_related=("status",))
        compare = CASE_COMPARE_FIELDS
        current = _case_current
        comp_keys = set()
        for chunk in _chunks(keys):
            comp_keys.update(Complainant.objects.filter(receipt_no__in=chunk).values_list("receipt_no", flat=True))
        analysis.unmatched_keys = [k for k in keys if k not in comp_keys]
        for key in analysis.unmatched_keys:
            analysis.add_issue(analysis.rows[key], IssueType.UNMATCHED, "ไม่พบเลขรับแจ้งนี้ในไฟล์ผู้แจ้ง")

        statuses = set(Status.objects.values_list("name", flat=True))
        units = set(Unit.objects.values_list("name", flat=True))
        aliases = dict(InvestigatorAlias.objects.values_list("key", "investigator_id"))
        seen_status, seen_alias, seen_unit = set(), set(), set()
        for prow in analysis.rows.values():
            name = prow.values["status_name"]
            if name and name not in statuses and name not in seen_status:
                seen_status.add(name)
                analysis.new_statuses.append(name)
                analysis.add_issue(prow, IssueType.NEW_STATUS, f"สถานะใหม่ '{name}' (ระบบจะสร้างเป็น 'ยังไม่สิ้นสุด')")
            unit = prow.values["unit_raw"]
            if unit and unit not in units and unit not in seen_unit:
                seen_unit.add(unit)
                analysis.new_units.append(unit)
            raw_inv = prow.values["investigator_raw"]
            inv_key = normalize_person_key(raw_inv)
            if inv_key and inv_key not in seen_alias:
                seen_alias.add(inv_key)
                if inv_key not in aliases:
                    analysis.new_aliases.append(raw_inv)
                    analysis.add_issue(prow, IssueType.NEW_INVESTIGATOR,
                                       f"ชื่อใหม่ '{raw_inv}' ระบบจะสร้างผู้ลงนามให้ "
                                       "(กรอกยศ ตำแหน่ง โทรศัพท์ครั้งแรกในหน้าออกหมาย)")
                elif aliases[inv_key] is None:
                    analysis.unlinked_aliases.append(raw_inv)

    labels = field_labels(analysis.kind)
    labels.update({"postcode_auto": "รหัสไปรษณีย์ (ระบบเติม)", "report_date": "วันที่รับแจ้ง",
                   "status_name": "สถานะ", "source_sheet": "ชีตต้นทาง"})
    for key, prow in analysis.rows.items():
        obj = existing.get(key)
        if obj is None:
            analysis.new_keys.append(key)
            continue
        if analysis.kind == ImportKind.REGISTER and obj.status_manual_at:
            if prow.values["status_name"] == obj.status_file_value:
                # register unchanged since staff edited the status: keep their status
                prow.values["status_name"] = obj.status.name if obj.status_id else ""
                analysis.manual_status_kept.add(key)
            else:
                analysis.manual_status_released.add(key)
        cur = current(obj)
        changed = [f for f in compare if _norm_cmp(cur.get(f)) != _norm_cmp(prow.values.get(f))]
        if changed:
            analysis.updated_keys.append(key)
            for f in changed:
                analysis.change_counts[labels.get(f, f)] += 1
        else:
            analysis.unchanged += 1


UPDATE_TEXT_FIELDS = [f for f in CASE_TEXT_FIELDS if f not in ("seq", "contact_log", "daily_log", "remark")]


def register_style_name(raw) -> str:
    """The big system writes 'พ.ต.ท.หญิงนิติยา เอกวงษา'; the register writes the name only, with หญิง in front
    for female officers. Same person, same signer."""
    from apps.registry.signer_import import split_rank

    rank, name = split_rank(clean_text(raw))
    if not rank:
        return clean_text(raw)
    return ("หญิง" if rank.endswith("หญิง") else "") + name


def _rank_of(raw) -> str:
    from apps.registry.signer_import import split_rank

    return split_rank(clean_text(raw))[0]


def _same_person(a, b) -> bool:
    from apps.registry.models import signer_name_from_register

    return normalize_person_key(signer_name_from_register(a)) == normalize_person_key(signer_name_from_register(b))


def _diff_update(analysis: Analysis):
    """Weekly file: what changes for each case already here (only fields the file has, only non-empty values,
    never the unit or the contact log), and which new cases of our unit are added."""
    existing = _existing(Case, analysis.rows.keys(), select_related=("status",))
    present = set(analysis.present)
    labels = field_labels(ImportKind.REGISTER)
    labels.update({"report_date": "วันที่รับแจ้ง", "status_name": "สถานะ"})
    statuses = set(Status.objects.values_list("name", flat=True))
    aliases = dict(InvestigatorAlias.objects.values_list("key", "investigator_id"))
    seen_status, seen_alias = set(), set()
    comp_keys = set()
    for chunk in _chunks(list(analysis.rows)):
        comp_keys.update(Complainant.objects.filter(receipt_no__in=chunk).values_list("receipt_no", flat=True))
    for key, prow in analysis.rows.items():
        v = prow.values
        name = v["status_name"]
        if name and name not in statuses and name not in seen_status:
            seen_status.add(name)
            analysis.new_statuses.append(name)
            analysis.add_issue(prow, IssueType.NEW_STATUS, f"สถานะใหม่ '{name}' (ระบบจะสร้างเป็น 'ยังไม่สิ้นสุด')")
        inv_key = normalize_person_key(v["investigator_raw"])
        if inv_key and inv_key not in seen_alias:
            seen_alias.add(inv_key)
            if inv_key not in aliases:
                analysis.new_aliases.append(v["investigator_raw"])
        obj = existing.get(key)
        if obj is None:  # a new case of our unit
            analysis.new_keys.append(key)
            if v["report_date"] is None:
                analysis.add_issue(prow, IssueType.BAD_DATE, "ไม่มีวันที่รับแจ้ง")
            if key not in comp_keys:
                analysis.unmatched_keys.append(key)
            continue
        changes = {}
        for f in UPDATE_TEXT_FIELDS:
            if f in present and f != "unit_raw" and v[f] and _norm_cmp(v[f]) != _norm_cmp(getattr(obj, f)):
                if f == "investigator_raw" and _same_person(v[f], obj.investigator_raw):
                    continue  # only the rank differs
                changes[f] = v[f]
        if "report_day" in present and v["report_date"] and v["report_date"] != obj.report_date:
            changes["report_date"] = v["report_date"]
        if "damage_amount" in present and v["damage_amount"] is not None \
                and _norm_cmp(v["damage_amount"]) != _norm_cmp(obj.damage_amount):
            changes["damage_amount"] = v["damage_amount"]
        current = obj.status.name if obj.status_id else ""
        if name and obj.status_manual_at and name == obj.status_file_value:
            analysis.manual_status_kept.add(key)  # staff changed it here; the big system still says the old one
        elif name and (name != current or obj.status_manual_at):
            changes["status_name"] = name
            if obj.status_manual_at:
                analysis.manual_status_released.add(key)
            if name != current:
                analysis.status_changes[(current or "-", name)] += 1
        if changes:
            analysis.updated_keys.append(key)
            analysis.changes_by_key[key] = changes
            for f in changes:
                analysis.change_counts[labels.get(f, f)] += 1
        else:
            analysis.unchanged += 1


def _norm_cmp(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def summary_dict(analysis: Analysis) -> dict:
    unit = "ราย" if analysis.kind == ImportKind.COMPLAINANTS else "คดี"
    changes = [f"{label}เปลี่ยน {count:,} {unit}" for label, count in analysis.change_counts.most_common()]
    issue_counts = Counter(i["issue_type"] for i in analysis.issues)
    return {
        "total_rows": analysis.total_rows,
        "skipped_blank": analysis.skipped_blank,
        "new": len(analysis.new_keys),
        "updated": len(analysis.updated_keys),
        "unchanged": analysis.unchanged,
        "issue_rows": analysis.issue_rows(),
        "unmatched": len(analysis.unmatched_keys),
        "unmatched_sample": analysis.unmatched_keys[:20],
        "changes": changes,
        "issue_counts": {IssueType(k).label: v for k, v in issue_counts.items()},
        "new_statuses": analysis.new_statuses,
        "new_aliases": analysis.new_aliases,
        "unlinked_aliases": analysis.unlinked_aliases,
        "new_units": analysis.new_units,
        "manual_status_kept": len(analysis.manual_status_kept),
        "per_sheet": dict(analysis.per_sheet),
        "own_unit": settings.OWN_UNIT,
        "other_units": dict(analysis.other_units),
        "other_unit_rows": sum(analysis.other_units.values()),
        "remove_count": analysis.remove_count,
        "remove_calls": analysis.remove_calls,
        "kept_with_summons": analysis.kept_with_summons,
        "looks_like_update": analysis.looks_like_update,
        "matched": len(analysis.rows) - len(analysis.new_keys) if analysis.kind == ImportKind.UPDATE else None,
        "status_changes": [f"{old} → {new}: {n:,} คดี" for (old, new), n in analysis.status_changes.most_common()],
        "to_final": sum(n for (old, new), n in analysis.status_changes.items() if new in FINAL_BY_DEFAULT),
    }


def store_analysis(batch: ImportBatch, analysis: Analysis):
    batch.issues.all().delete()
    ImportIssue.objects.bulk_create(
        [ImportIssue(batch=batch, **issue) for issue in analysis.issues], batch_size=CHUNK
    )
    summary = summary_dict(analysis)
    batch.summary = summary
    batch.total_rows = summary["total_rows"]
    batch.new_count = summary["new"]
    batch.updated_count = summary["updated"]
    batch.unchanged_count = summary["unchanged"]
    batch.issue_count = summary["issue_rows"]
    batch.unmatched_count = summary["unmatched"]


# --------------------------------------------------------------------------- commit
def _apply_complainants(batch, analysis: Analysis):
    existing = _existing(Complainant, analysis.rows.keys())
    to_create, to_update = [], []
    updated = set(analysis.updated_keys)
    for key, prow in analysis.rows.items():
        obj = existing.get(key)
        if obj is None:
            obj = Complainant(receipt_no=key, **prow.values)
            obj.last_batch = batch
            obj.refresh_flags()
            to_create.append(obj)
        elif key in updated:
            for name, value in prow.values.items():
                setattr(obj, name, value)
            obj.last_batch = batch
            obj.refresh_flags()
            to_update.append(obj)
    Complainant.objects.bulk_create(to_create, batch_size=CHUNK)
    update_fields = COMPLAINANT_FIELDS + [
        "flag_missing_house", "flag_missing_area", "flag_missing_province", "flag_bad_id",
        "address_incomplete", "last_batch", "updated_at",
    ]
    now = timezone.now()
    for obj in to_update:
        obj.updated_at = now
    Complainant.objects.bulk_update(to_update, update_fields, batch_size=CHUNK)


def _apply_register(batch, analysis: Analysis):
    statuses = {s.name: s for s in Status.objects.all()}
    for name in analysis.new_statuses:
        final = name in FINAL_BY_DEFAULT
        statuses[name] = Status.objects.create(name=name, is_final=final, needs_review=not final)
    units = {u.name: u for u in Unit.objects.all()}
    for name in analysis.new_units:
        units[name] = Unit.objects.create(name=name)
    aliases = {a.key: a for a in InvestigatorAlias.objects.all()}
    for raw in analysis.new_aliases:
        alias = InvestigatorAlias(raw_name=raw)
        alias.save()
        aliases[alias.key] = alias
    # every register name gets a signer record (same person = same name ignoring spaces / leading หญิง)
    link_register_names()

    existing = _existing(Case, analysis.rows.keys(), select_related=("status",))
    updated = set(analysis.updated_keys)
    to_create, to_update = [], []
    now = timezone.now()
    for key, prow in analysis.rows.items():
        values = dict(prow.values)
        status_name = values.pop("status_name")
        attrs = {
            **values,
            "report_year": values["report_date"].year if values["report_date"] else None,
            "status": statuses.get(status_name) if status_name else None,
            "unit": units.get(values["unit_raw"]) if values["unit_raw"] else None,
            "investigator_alias": aliases.get(normalize_person_key(values["investigator_raw"])),
            "last_batch": batch,
        }
        obj = existing.get(key)
        if obj is None:
            to_create.append(Case(receipt_no=key, **attrs))
        elif key in updated:
            for name, value in attrs.items():
                setattr(obj, name, value)
            if key in analysis.manual_status_released:
                obj.status_manual_at = None
                obj.status_manual_by = None
                obj.status_file_value = ""
            obj.updated_at = now
            to_update.append(obj)
        elif obj.investigator_alias_id is None and attrs["investigator_alias"] is not None:
            obj.investigator_alias = attrs["investigator_alias"]
            to_update.append(obj)
    Case.objects.bulk_create(to_create, batch_size=CHUNK)
    update_fields = CASE_TEXT_FIELDS + [
        "report_date", "report_year", "damage_amount", "daily_log_at", "source_sheet", "status",
        "unit", "investigator_alias", "last_batch", "updated_at",
        "status_manual_at", "status_manual_by", "status_file_value",
    ]
    Case.objects.bulk_update(to_update, update_fields, batch_size=CHUNK)


def _apply_update(batch, analysis: Analysis):
    """Weekly file from the big system: update cases already here, add new cases of our unit, delete nothing."""
    statuses = {s.name: s for s in Status.objects.all()}
    for name in analysis.new_statuses:
        final = name in FINAL_BY_DEFAULT
        statuses[name] = Status.objects.create(name=name, is_final=final, needs_review=not final)
    aliases = {a.key: a for a in InvestigatorAlias.objects.all()}
    for raw in analysis.new_aliases:
        alias = InvestigatorAlias(raw_name=raw)
        alias.save()
        aliases[alias.key] = alias
    link_register_names()
    aliases = {a.key: a for a in InvestigatorAlias.objects.select_related("investigator")}  # now linked
    # a signer without a rank gets the one the big system writes (never overwrites a rank staff entered)
    for key, rank in analysis.ranks.items():
        alias = aliases.get(key)
        inv = alias.investigator if alias and alias.investigator_id else None
        if inv is not None and not inv.rank_full:
            inv.rank_full = rank
            inv.save()
    own_units = [u for u in Unit.objects.all() if u.name.strip() and is_own_unit(u.name)]
    own = (next((u for u in own_units if unit_key(u.name) == unit_key(settings.OWN_UNIT)), None)
           or (own_units[0] if own_units else Unit.objects.create(name=settings.OWN_UNIT)))

    now = timezone.now()
    to_create = []
    for key in analysis.new_keys:
        values = dict(analysis.rows[key].values)
        status_name = values.pop("status_name")
        day = values["report_date"]
        values["source_sheet"] = f"ปี {day.year + 543} ({day.year})" if day else "อัปเดตจากระบบใหญ่"
        to_create.append(Case(
            receipt_no=key, **values, report_year=day.year if day else None,
            status=statuses.get(status_name) if status_name else None, unit=own,
            investigator_alias=aliases.get(normalize_person_key(values["investigator_raw"])), last_batch=batch))
    Case.objects.bulk_create(to_create, batch_size=CHUNK)

    existing = _existing(Case, analysis.changes_by_key.keys(), select_related=("status",))
    to_update, fields = [], {"last_batch", "updated_at"}
    for key, changes in analysis.changes_by_key.items():
        obj = existing[key]
        for f, value in changes.items():
            if f == "status_name":
                obj.status = statuses.get(value)
                fields.add("status")
                if key in analysis.manual_status_released:
                    obj.status_manual_at = obj.status_manual_by = None
                    obj.status_file_value = ""
                    fields |= {"status_manual_at", "status_manual_by", "status_file_value"}
            else:
                setattr(obj, f, value)
                fields.add(f)
                if f == "report_date":
                    obj.report_year = value.year
                    fields.add("report_year")
                if f == "investigator_raw":
                    obj.investigator_alias = aliases.get(normalize_person_key(value))
                    fields.add("investigator_alias")
        obj.last_batch, obj.updated_at = batch, now
        to_update.append(obj)
    Case.objects.bulk_update(to_update, sorted(fields), batch_size=CHUNK)


def relink_cases() -> int:
    """Attach complainants to cases that have none yet (by เลขรับแจ้ง). Returns count linked."""
    unlinked = dict(Case.objects.filter(complainant__isnull=True).values_list("receipt_no", "pk"))
    if not unlinked:
        return 0
    taken = set(Case.objects.filter(complainant__isnull=False).values_list("complainant_id", flat=True))
    to_update = []
    for chunk in _chunks(unlinked.keys()):
        for receipt_no, comp_id in Complainant.objects.filter(receipt_no__in=chunk).values_list("receipt_no", "pk"):
            if comp_id not in taken:
                to_update.append(Case(pk=unlinked[receipt_no], complainant_id=comp_id))
    Case.objects.bulk_update(to_update, ["complainant"], batch_size=CHUNK)
    return len(to_update)


def commit(batch: ImportBatch, progress=None) -> Analysis:
    analysis = analyze(batch, progress=lambda p: progress and progress(int(p * 0.5)))
    with transaction.atomic():
        batch_removed = 0
        if batch.kind == ImportKind.COMPLAINANTS:
            _apply_complainants(batch, analysis)
        elif batch.kind == ImportKind.UPDATE:
            _apply_update(batch, analysis)  # never removes anything
        else:
            _apply_register(batch, analysis)
            batch_removed = _remove_other_unit_cases(analysis)
        linked = relink_cases()
        store_analysis(batch, analysis)
        batch.summary["linked"] = linked
        if batch.kind == ImportKind.REGISTER:
            batch.summary["removed"] = batch_removed
        batch.status = BatchStatus.DONE
        batch.committed_at = timezone.now()
        batch.progress = 100
        batch.save()
    return analysis


# --------------------------------------------------------------------------- mapping memory
def remembered_mapping(kind) -> dict:
    return dict(ColumnMapping.objects.filter(kind=kind).values_list("source_header", "field_name"))


def remember_mapping(kind, mapping: dict):
    """Persist user choices that differ from the standard header names."""
    labels = field_labels(kind)
    for fieldname, header in mapping.items():
        if header and header != labels.get(fieldname):
            ColumnMapping.objects.update_or_create(
                kind=kind, source_header=header, defaults={"field_name": fieldname}
            )


def prepare_batch(batch: ImportBatch) -> dict:
    """Fill available sheets and an automatic mapping. Returns workbook info."""
    info = workbook_info(batch.file.path)
    batch.available_sheets = [s["name"] for s in info]
    default = DEFAULT_SHEET.get(batch.kind)
    if default and default in batch.available_sheets:
        batch.sheets = [default]
    elif batch.kind == ImportKind.COMPLAINANTS:
        batch.sheets = batch.available_sheets[:1]
    else:
        batch.sheets = list(batch.available_sheets)
    headers = next((s["headers"] for s in info if s["name"] in batch.sheets), [])
    batch.mapping = auto_map(batch.kind, headers, remembered_mapping(batch.kind))
    batch.save()
    return {s["name"]: s["headers"] for s in info}
