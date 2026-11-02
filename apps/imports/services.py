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
from django.db import transaction
from django.utils import timezone

from apps.core.thai_format import normalize_year_to_ce
from apps.registry.address import clean_text
from apps.registry.defaults import FINAL_BY_DEFAULT
from apps.registry.models import (
    Case, Complainant, InvestigatorAlias, Status, Unit,
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
    if isinstance(raw_day, dt.datetime):
        report_date = raw_day.date()
    elif day and month and year:
        try:
            report_date = dt.date(normalize_year_to_ce(year), month, day)
        except ValueError:
            report_date = None
    if report_date is None:
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
            else:
                prow.values = parse_register_row(raw, batch.mapping, sheet, analysis, prow)
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
    _diff(analysis)
    if progress:
        progress(100)
    return analysis


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
                                       f"'{raw_inv}' ยังไม่ได้ผูกกับข้อมูลผู้ลงนาม")
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
        if batch.kind == ImportKind.COMPLAINANTS:
            _apply_complainants(batch, analysis)
        else:
            _apply_register(batch, analysis)
        linked = relink_cases()
        store_analysis(batch, analysis)
        batch.summary["linked"] = linked
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
