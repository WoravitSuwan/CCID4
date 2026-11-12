"""Administrator settings pages."""
from __future__ import annotations

import difflib
import re

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.documents import docx_engine
from apps.documents.models import DocTemplate, TemplateKind
from apps.documents.placeholders import ENVELOPE_PLACEHOLDERS, SUMMONS_PLACEHOLDERS
from apps.registry.forms import SignerForm, StyledForm, StyledModelForm
from apps.registry.models import Investigator, InvestigatorAlias, Status, Unit

from . import permissions as perms
from .audit import log
from .models import AuditAction, AuditLog, SystemSetting

admin_only = perms.require(perms.can_manage_settings)
User = get_user_model()

RANK_WORDS = ["พันตำรวจเอก", "พันตำรวจโท", "พันตำรวจตรี", "ร้อยตำรวจเอก", "ร้อยตำรวจโท", "ร้อยตำรวจตรี",
              "พ.ต.อ.", "พ.ต.ท.", "พ.ต.ต.", "ร.ต.อ.", "ร.ต.ท.", "ร.ต.ต.", "หญิง"]


def name_core(text: str) -> str:
    text = text or ""
    for w in RANK_WORDS:
        text = text.replace(w, "")
    return re.sub(r"\s+", "", text)


def suggest(name: str, candidates, key=lambda x: str(x), limit=3):
    target = name_core(name)
    scored = []
    for c in candidates:
        score = difflib.SequenceMatcher(None, target, name_core(key(c))).ratio()
        if score >= 0.5:
            scored.append((score, c))
    scored.sort(key=lambda s: -s[0])
    return [c for _, c in scored[:limit]]


def settings_home(request):
    if not perms.can_manage_settings(request.user):
        return redirect("dashboard")
    return redirect("settings_investigators")


# --------------------------------------------------------------------------- investigators
class InvestigatorForm(SignerForm):
    class Meta(SignerForm.Meta):
        fields = ["rank_full", "sign_name", "position", "phone", "unit", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].required = False
        self.fields["is_active"].required = False
        if self.instance.pk and self.instance.is_female:
            self.fields["rank_full"].widget.attrs["list"] = "dl-rank-f"


@admin_only
def investigators(request):
    invs = Investigator.objects.prefetch_related("aliases").select_related("unit")
    unlinked = list(InvestigatorAlias.objects.filter(investigator__isnull=True).order_by("raw_name"))
    all_invs = list(Investigator.objects.prefetch_related("aliases"))
    for alias in unlinked:
        alias.suggestions = suggest(alias.raw_name, all_invs,
                                    key=lambda i: " ".join([i.sign_name] + [a.raw_name for a in i.aliases.all()]))
        alias.case_count = alias.cases.count()
    return render(request, "settings/investigators.html", {
        "investigators": invs, "unlinked": unlinked, "all_investigators": all_invs, "tab": "investigators",
    })


@admin_only
def investigator_edit(request, pk=None):
    inv = get_object_or_404(Investigator, pk=pk) if pk else None
    form = InvestigatorForm(request.POST or None, instance=inv)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log(request, AuditAction.SETTINGS, obj, f"{'แก้ไข' if inv else 'เพิ่ม'}พนักงานสอบสวน {obj}")
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_investigators")
    return render(request, "settings/form.html", {
        "form": form, "title": "แก้ไขพนักงานสอบสวน" if inv else "เพิ่มพนักงานสอบสวน", "tab": "investigators",
        "back": "settings_investigators",
        "help": "ข้อมูลนี้ใช้เป็นผู้ลงนามในหมายเรียก ยศและตำแหน่งพิมพ์เองหรือเลือกจากรายการได้ "
                "ค่าที่เคยพิมพ์ระบบจะจำไว้ให้เลือกครั้งต่อไป",
    })


SESSION_SIGNER_IMPORT = "signer_import_rows"


@admin_only
def signer_template(request):
    from apps.registry.signer_import import template_workbook

    from .downloads import content_disposition

    log(request, AuditAction.DOWNLOAD, None, "ดาวน์โหลดแบบฟอร์มรายชื่อผู้ลงนาม (Excel)")
    resp = HttpResponse(template_workbook(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = content_disposition("รายชื่อผู้ลงนาม_ยศ_ตำแหน่ง.xlsx")
    return resp


@admin_only
def signer_import(request):
    """Upload an Excel list of signers -> preview -> confirm."""
    from apps.registry import signer_import as si

    rows = None
    if request.method == "POST" and request.POST.get("action") == "confirm":
        stored = request.session.pop(SESSION_SIGNER_IMPORT, None)
        if not stored:
            messages.error(request, "ไม่พบข้อมูลที่ตรวจสอบไว้ กรุณาอัปโหลดไฟล์อีกครั้ง")
            return redirect("settings_signer_import")
        counts = si.apply(stored)
        log(request, AuditAction.SETTINGS, None,
            f"นำเข้ารายชื่อผู้ลงนามจาก Excel: เพิ่ม {counts['new']} แก้ไข {counts['update']} ไม่เปลี่ยน {counts['same']}")
        messages.success(request, f"นำเข้ารายชื่อผู้ลงนามแล้ว: เพิ่มใหม่ {counts['new']} คน, แก้ไข {counts['update']} คน, "
                                  f"ไม่เปลี่ยน {counts['same']} คน")
        return redirect("settings_investigators")
    if request.method == "POST" and request.FILES.get("file"):
        upload = request.FILES["file"]
        if not upload.name.lower().endswith((".xlsx", ".xlsm")):
            messages.error(request, "รองรับเฉพาะไฟล์ .xlsx")
        else:
            parsed, error = si.parse(upload)
            if error:
                messages.error(request, error)
            else:
                rows = si.plan(parsed)
                request.session[SESSION_SIGNER_IMPORT] = [r.as_dict() for r in rows]
    summary = None
    if rows is not None:
        summary = {a: sum(1 for r in rows if r.action == a) for a in ("new", "update", "same", "skip")}
    return render(request, "settings/signer_import.html", {"rows": rows, "summary": summary, "tab": "investigators"})


@require_POST
@admin_only
def alias_link(request, pk):
    alias = get_object_or_404(InvestigatorAlias, pk=pk)
    target = request.POST.get("investigator")
    if target == "new":
        name = re.sub(r"^หญิง\s*", "", alias.raw_name).strip()
        inv = Investigator.objects.create(sign_name=name)
        alias.investigator = inv
        alias.save()
        log(request, AuditAction.SETTINGS, alias, f"สร้างพนักงานสอบสวนใหม่จากชื่อ '{alias.raw_name}'")
        messages.success(request, f"สร้าง '{name}' แล้ว กรุณากรอกยศ ตำแหน่ง และโทรศัพท์ให้ครบ")
        return redirect("settings_investigator_edit", pk=inv.pk)
    if target and target.isdigit():
        inv = get_object_or_404(Investigator, pk=target)
        alias.investigator = inv
        alias.save()
        log(request, AuditAction.SETTINGS, alias, f"ผูกชื่อ '{alias.raw_name}' กับ {inv}")
        messages.success(request, f"ผูก '{alias.raw_name}' กับ {inv} แล้ว ({alias.cases.count()} คดี)")
    return redirect("settings_investigators")


@require_POST
@admin_only
def alias_unlink(request, pk):
    alias = get_object_or_404(InvestigatorAlias, pk=pk)
    old = alias.investigator
    alias.investigator = None
    alias.save()
    log(request, AuditAction.SETTINGS, alias, f"ยกเลิกการผูกชื่อ '{alias.raw_name}' กับ {old}")
    messages.info(request, "ยกเลิกการผูกแล้ว")
    return redirect("settings_investigators")


# --------------------------------------------------------------------------- units
class UnitForm(StyledModelForm):
    class Meta:
        model = Unit
        fields = ["name", "full_name", "appointment_place", "sender_address", "postage_permit"]
        widgets = {"appointment_place": forms.Textarea(attrs={"rows": 3}),
                   "sender_address": forms.Textarea(attrs={"rows": 3}),
                   "postage_permit": forms.Textarea(attrs={"rows": 3})}


@admin_only
def units(request):
    return render(request, "settings/units.html", {"units": Unit.objects.all(), "tab": "units"})


@admin_only
def unit_edit(request, pk=None):
    unit = get_object_or_404(Unit, pk=pk) if pk else None
    form = UnitForm(request.POST or None, instance=unit)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log(request, AuditAction.SETTINGS, obj, f"{'แก้ไข' if unit else 'เพิ่ม'}หน่วย {obj}")
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_units")
    return render(request, "settings/form.html", {
        "form": form, "title": "แก้ไขหน่วย" if unit else "เพิ่มหน่วย", "tab": "units", "back": "settings_units",
        "help": "'สถานที่นัด' ใส่ในหมายเรียกหลังคำว่า 'ไป ณ ที่' และ 'ที่อยู่ผู้ส่ง' ใช้บนใบจ่าหน้าซอง",
    })


# --------------------------------------------------------------------------- statuses
class StatusForm(StyledModelForm):
    class Meta:
        model = Status
        fields = ["name", "is_final", "sort_order"]


@admin_only
def statuses(request):
    form = StatusForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log(request, AuditAction.SETTINGS, obj, f"เพิ่มสถานะ {obj}")
        messages.success(request, "เพิ่มสถานะแล้ว")
        return redirect("settings_statuses")
    rows = Status.objects.all()
    from django.db.models import Count

    counts = dict(Status.objects.annotate(n=Count("case")).values_list("pk", "n"))
    return render(request, "settings/statuses.html", {"statuses": rows, "counts": counts, "form": form,
                                                      "tab": "statuses"})


@require_POST
@admin_only
def status_toggle(request, pk):
    status = get_object_or_404(Status, pk=pk)
    if "is_final" in request.POST:
        status.is_final = request.POST.get("is_final") == "1"
    status.needs_review = False
    status.save()
    log(request, AuditAction.SETTINGS, status,
        f"สถานะ '{status.name}' = {'สิ้นสุด' if status.is_final else 'ยังไม่สิ้นสุด'}")
    messages.success(request, f"บันทึกสถานะ '{status.name}' แล้ว")
    return redirect("settings_statuses")


# --------------------------------------------------------------------------- templates
class TemplateUploadForm(StyledForm):
    kind = forms.ChoiceField(label="ประเภท", choices=TemplateKind.choices)
    name = forms.CharField(label="ชื่อแบบฟอร์ม", max_length=200)
    file = forms.FileField(label="ไฟล์ .docx")
    show_hidden_text = forms.BooleanField(label="แสดงข้อความที่ซ่อนไว้ (พิมพ์ลง A4 เปล่า)", required=False)
    activate = forms.BooleanField(label="ใช้งานแบบฟอร์มนี้ทันที", required=False, initial=True)

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".docx"):
            raise forms.ValidationError("รองรับเฉพาะไฟล์ .docx (ถ้าเป็น .doc ให้เปิดใน Word แล้วบันทึกเป็น .docx)")
        return f


@admin_only
def templates(request):
    form = TemplateUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            last = DocTemplate.objects.filter(kind=d["kind"]).order_by("-version").first()
            tpl = DocTemplate(kind=d["kind"], name=d["name"], version=(last.version + 1) if last else 1,
                              uploaded_by=request.user, show_hidden_text=d["show_hidden_text"])
            tpl.file.save(d["file"].name, d["file"], save=False)
            try:
                tpl.placeholders = docx_engine.scan_placeholders(tpl.file.path)["placeholders"]
            except Exception as exc:
                tpl.file.delete(save=False)
                messages.error(request, f"อ่านไฟล์ Word ไม่ได้: {exc}")
                return redirect("settings_templates")
            tpl.save()
            if d["activate"]:
                tpl.activate()
        known = ENVELOPE_PLACEHOLDERS if tpl.kind == TemplateKind.ENVELOPE else SUMMONS_PLACEHOLDERS
        unknown = [p for p in tpl.placeholders if p not in known]
        log(request, AuditAction.SETTINGS, tpl, f"อัปโหลดแบบฟอร์ม {tpl}")
        if unknown:
            messages.warning(request, "แบบฟอร์มมีตัวแทนที่ระบบไม่รู้จัก (ต้องแก้ก่อนออกหมาย): "
                             + ", ".join(f"{{{u}}}" for u in unknown))
        else:
            messages.success(request, f"อัปโหลด {tpl} แล้ว")
        return redirect("settings_templates")
    rows = DocTemplate.objects.all()
    for t in rows:
        known = ENVELOPE_PLACEHOLDERS if t.kind == TemplateKind.ENVELOPE else SUMMONS_PLACEHOLDERS
        t.unknown = [p for p in t.placeholders if p not in known]
    return render(request, "settings/templates.html", {
        "templates": rows, "form": form, "supported": ENVELOPE_PLACEHOLDERS, "tab": "templates",
    })


@require_POST
@admin_only
def template_action(request, pk):
    tpl = get_object_or_404(DocTemplate, pk=pk)
    action = request.POST.get("action")
    if action == "activate":
        tpl.activate()
        messages.success(request, f"ใช้งาน {tpl} แล้ว")
    elif action == "toggle_hidden":
        tpl.show_hidden_text = not tpl.show_hidden_text
        tpl.save(update_fields=["show_hidden_text"])
        messages.success(request, "บันทึกแล้ว")
    log(request, AuditAction.SETTINGS, tpl, f"แบบฟอร์ม {tpl}: {action}")
    return redirect("settings_templates")


def template_download(request, pk):
    from .downloads import file_download

    if not perms.can_manage_settings(request.user):
        return redirect("dashboard")
    tpl = get_object_or_404(DocTemplate, pk=pk)
    log(request, AuditAction.DOWNLOAD, tpl, f"ดาวน์โหลดแบบฟอร์ม {tpl}")
    return file_download(tpl.file.path, f"{tpl.get_kind_display()}_v{tpl.version}.docx")


# --------------------------------------------------------------------------- users
class UserForm(StyledModelForm):
    password = forms.CharField(label="รหัสผ่าน", required=False, widget=forms.PasswordInput,
                               help_text="เว้นว่างถ้าไม่ต้องการเปลี่ยน (อย่างน้อย 8 ตัวอักษร)")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["password"].required = True

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if pw and len(pw) < 8:
            raise forms.ValidationError("รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร")
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = user.is_superuser = user.role == Role.ADMIN
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


@admin_only
def users(request):
    return render(request, "settings/users.html", {
        "users": User.objects.order_by("username"), "tab": "users",
    })


@admin_only
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        demoting = d["role"] != Role.ADMIN or not d["is_active"]
        other_admins = User.objects.filter(role=Role.ADMIN, is_active=True).exclude(pk=getattr(user, "pk", None))
        if user is not None and demoting and (user.pk == request.user.pk or not other_admins.exists()):
            form.add_error("role", "เปลี่ยนบทบาทหรือปิดบัญชีนี้ไม่ได้ ต้องมีผู้ดูแลระบบที่ใช้งานอยู่อย่างน้อย 1 คน "
                                   "และห้ามลดสิทธิ์บัญชีของตนเอง")
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log(request, AuditAction.SETTINGS, obj, f"{'แก้ไข' if user else 'เพิ่ม'}ผู้ใช้ {obj.username} ({obj.role_label})")
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_users")
    return render(request, "settings/form.html", {
        "form": form, "title": "แก้ไขผู้ใช้" if user else "เพิ่มผู้ใช้", "tab": "users", "back": "settings_users",
        "help": "ผู้ดูแลระบบ: ใช้งานได้ทุกส่วน (นำเข้า ทะเบียนคดี ออกหมาย ส่งออก มอบหมายงานโทร ตั้งค่า) · "
                "เจ้าหน้าที่ติดต่อผู้เสียหาย (outsource): เห็นเฉพาะคดีที่ได้รับมอบหมาย และบันทึกผลการโทรเท่านั้น",
    })


# --------------------------------------------------------------------------- system & audit
LOGO_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def logo_content_type(data: bytes) -> str:
    """Recognise PNG / JPEG / WebP by their first bytes (never trust the file name)."""
    for magic, ctype in LOGO_TYPES.items():
        if data.startswith(magic) and (ctype != "image/webp" or data[8:12] == b"WEBP"):
            return ctype
    return ""


class SystemForm(StyledModelForm):
    logo = forms.FileField(label="โลโก้หน่วย (PNG / JPG / WebP ไม่เกิน 2 MB)", required=False,
                           help_text="แสดงที่แถบเมนู หน้าเข้าสู่ระบบ และไอคอนแท็บเบราว์เซอร์ แนะนำรูปสี่เหลี่ยมจัตุรัส พื้นหลังโปร่งใส",
                           widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/webp"}))
    remove_logo = forms.BooleanField(label="ลบโลโก้ (กลับไปใช้ไอคอนเดิม)", required=False)

    class Meta:
        model = SystemSetting
        fields = ["retention_days", "default_reason", "default_appointment_days", "letter_number"]

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        if not f:
            return None
        if f.size > LOGO_MAX_BYTES:
            raise forms.ValidationError("ไฟล์ใหญ่เกิน 2 MB")
        data = f.read()
        ctype = logo_content_type(data)
        if not ctype:
            raise forms.ValidationError("รองรับเฉพาะรูป PNG, JPG หรือ WebP")
        return data, ctype

    def save(self, commit=True):
        from django.utils import timezone

        obj = super().save(commit=False)
        logo = self.cleaned_data.get("logo")
        if logo:
            obj.logo_data, obj.logo_type = logo
            obj.logo_updated = timezone.now()
        elif self.cleaned_data.get("remove_logo"):
            obj.logo_data, obj.logo_type, obj.logo_updated = None, "", None
        if commit:
            obj.save()
        return obj


def branding_logo(request):
    """The unit logo (public: also shown on the login page)."""
    from django.http import Http404

    obj = SystemSetting.objects.filter(pk=1).only("logo_data", "logo_type").first()
    if not obj or not obj.logo_type or not obj.logo_data:
        raise Http404
    resp = HttpResponse(bytes(obj.logo_data), content_type=obj.logo_type)
    resp["Cache-Control"] = "public, max-age=86400"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@admin_only
def system(request):
    form = SystemForm(request.POST or None, request.FILES or None, instance=SystemSetting.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        logo_note = (" (เปลี่ยนโลโก้)" if form.cleaned_data.get("logo")
                     else " (ลบโลโก้)" if form.cleaned_data.get("remove_logo") else "")
        log(request, AuditAction.SETTINGS, form.instance, "แก้ไขการตั้งค่าระบบ" + logo_note)
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_system")
    return render(request, "settings/form.html", {"form": form, "title": "การตั้งค่าระบบ", "tab": "system",
                                                  "back": "settings_system"})


class LineForm(StyledModelForm):
    class Meta:
        model = SystemSetting
        fields = ["line_enabled", "line_send_time", "line_skip_empty", "line_include_list"]
        widgets = {"line_send_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}


class LineKeysForm(forms.Form):
    """The channel's secret and access token. Inputs never show a saved value; blank keeps the old one."""
    secret = forms.CharField(label="Channel secret", required=False, max_length=100,
                             widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "off"}),
                             help_text="LINE Developers → Channel → แท็บ Basic settings")
    token = forms.CharField(label="Channel access token (long-lived)", required=False, max_length=1000,
                            widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "off"}),
                            help_text="LINE Developers → Channel → แท็บ Messaging API → Issue")

    def clean(self):
        data = super().clean()
        for name in ("secret", "token"):
            data[name] = "".join((data.get(name) or "").split())  # pasted with spaces or a new line
        return data


@admin_only
def line(request):
    """บอท LINE: keys, confirm the group, daily time, test send."""
    from django.urls import reverse
    from django.utils import timezone

    from apps.calls import daily_report, line_bot

    s = SystemSetting.get()
    form = LineForm(request.POST if request.POST.get("action") == "save" else None, instance=s)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save" and form.is_valid():
            form.save()
            log(request, AuditAction.SETTINGS, s, "แก้ไขการตั้งค่าบอท LINE"
                + (" (เปิดส่งรายชื่อ)" if form.cleaned_data["line_include_list"] else ""))
            messages.success(request, "บันทึกแล้ว")
            return redirect("settings_line")
        if action == "confirm" and s.line_pending_group_id:
            s.line_group_id, s.line_group_name = s.line_pending_group_id, s.line_pending_group_name
            s.line_pending_group_id = s.line_pending_group_name = ""
            s.save()
            log(request, AuditAction.SETTINGS, s, f"เลือกกลุ่ม LINE {s.line_group_name or s.line_group_id}")
            messages.success(request, "ยืนยันกลุ่ม LINE แล้ว ระบบจะส่งข้อความเข้ากลุ่มนี้เท่านั้น")
            return redirect("settings_line")
        if action in ("reject", "forget"):
            if action == "reject":
                s.line_pending_group_id = s.line_pending_group_name = ""
            else:
                log(request, AuditAction.SETTINGS, s, f"เลิกส่งเข้ากลุ่ม LINE {s.line_group_name or s.line_group_id}")
                s.line_group_id = s.line_group_name = ""
            s.save()
            messages.success(request, "เรียบร้อย")
            return redirect("settings_line")
        if action == "keys":
            keys = LineKeysForm(request.POST)
            if keys.is_valid():
                secret, token = keys.cleaned_data["secret"], keys.cleaned_data["token"]
                if secret:
                    s.line_channel_secret = secret
                if token:
                    s.line_access_token = token
                s.save(update_fields=["line_channel_secret", "line_access_token"])
                log(request, AuditAction.SETTINGS, s, "บันทึกรหัสเชื่อมต่อ LINE"
                    + "".join(f" ({x})" for x, v in (("Channel secret", secret), ("access token", token)) if v))
                if line_bot.configured():
                    try:
                        name = line_bot.bot_name()
                    except line_bot.LineError as exc:
                        messages.error(request, f"บันทึกแล้ว แต่ LINE ไม่รับ access token นี้: {exc}")
                    else:
                        messages.success(request, f"เชื่อมต่อบอท \"{name}\" สำเร็จ")
                else:
                    messages.success(request, "บันทึกแล้ว (ยังต้องใส่อีกช่องให้ครบ)")
            return redirect("settings_line")
        if action == "clear_keys":
            s.line_channel_secret = s.line_access_token = ""
            s.save(update_fields=["line_channel_secret", "line_access_token"])
            log(request, AuditAction.SETTINGS, s, "ลบรหัสเชื่อมต่อ LINE")
            messages.success(request, "ลบรหัสเชื่อมต่อ LINE แล้ว บอทหยุดทำงาน")
            return redirect("settings_line")
        if action == "test":
            day = timezone.localdate()
            try:
                total = line_bot.send_day(day)
            except line_bot.LineError as exc:
                messages.error(request, f"ส่งไม่ได้: {exc}")
            else:
                log(request, AuditAction.EXPORT, None,
                    f"ส่งสรุปผลประจำวัน {daily_report.file_day(day)} เข้ากลุ่ม LINE ({total:,} Case, ทดสอบ)")
                messages.success(request, "ส่งสรุปผลวันนี้เข้ากลุ่ม LINE แล้ว")
            return redirect("settings_line")
    return render(request, "settings/line.html", {
        "form": form, "s": s, "tab": "line", "configured": line_bot.configured(),
        "keys_form": LineKeysForm(), "from_env": line_bot.from_env(),
        "has_secret": bool(s.line_channel_secret), "has_token": bool(s.line_access_token),
        "webhook_url": request.build_absolute_uri(reverse("line_webhook")),
        "quota": line_bot.quota() if line_bot.configured() and "quota" in request.GET else None,
        "quota_checked": "quota" in request.GET, "help_text": line_bot.HELP,
    })


@admin_only
def audit(request):
    qs = AuditLog.objects.select_related("user")
    action = request.GET.get("action")
    if action:
        qs = qs.filter(action=action)
    user = request.GET.get("user")
    if user and user.isdigit():
        qs = qs.filter(user_id=user)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(detail__icontains=q)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "settings/audit.html", {
        "page": page, "actions": AuditAction.choices, "users": User.objects.order_by("username"), "tab": "audit",
    })


# --------------------------------------------------------------------------- wipe all case data
WIPE_PHRASE = "ลบข้อมูลทั้งหมด"


class WipeForm(StyledForm):
    scope = forms.ChoiceField(label="ต้องการลบอะไร", widget=forms.RadioSelect, initial="summons", choices=[
        ("calls_today", "บันทึกการโทรของวันนี้"),
        ("calls", "บันทึกการโทรทั้งหมด"),
        ("summons", "เฉพาะหมายเรียกที่ออก (เก็บคดี ข้อมูลผู้แจ้ง และบันทึกการโทรไว้)"),
        ("all", "ข้อมูลคดีทั้งหมด (เริ่มนำเข้าใหม่ตั้งแต่ต้น)")])
    restore_status = forms.BooleanField(
        label="คืนสถานะคดีที่ระบบเปลี่ยนเป็น 'ออกหมายเรียก' ตอนออกหมาย กลับเป็นสถานะเดิม", required=False,
        initial=True)
    confirm = forms.CharField(label=f'พิมพ์คำว่า "{WIPE_PHRASE}" เพื่อยืนยัน', max_length=50)
    deleted_by = forms.CharField(label="ชื่อ-นามสกุล ผู้ลบข้อมูล", max_length=150)
    password = forms.CharField(label="รหัสผ่านของคุณ", widget=forms.PasswordInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_confirm(self):
        value = " ".join(self.cleaned_data["confirm"].split())
        if value != WIPE_PHRASE:
            raise forms.ValidationError(f'ต้องพิมพ์ "{WIPE_PHRASE}" ให้ตรงทุกตัวอักษร')
        return value

    def clean_deleted_by(self):
        value = " ".join(self.cleaned_data["deleted_by"].split())
        if len(value) < 3:
            raise forms.ValidationError("กรอกชื่อ-นามสกุลของผู้ลบ")
        return value

    def clean_password(self):
        if not self.user or not self.user.check_password(self.cleaned_data["password"]):
            raise forms.ValidationError("รหัสผ่านไม่ถูกต้อง")
        return ""


@admin_only
def wipe(request):
    """Delete every case, import, summons and call record - to start the trial over."""
    from .wipe import call_counts, counts, summons_counts, wipe_calls, wipe_case_data, wipe_summonses

    form = WipeForm(request.POST or None, user=request.user,
                    initial={"scope": request.GET.get("scope") or "summons"})
    if request.method == "POST" and form.is_valid():
        scope = form.cleaned_data["scope"]
        if scope in ("calls_today", "calls"):
            before = wipe_calls(today_only=scope == "calls_today")
            summary = ", ".join(f"{k} {v:,}" for k, v in before.items())
            what = "ลบบันทึกการโทรของวันนี้" if scope == "calls_today" else "ลบบันทึกการโทรทั้งหมด"
            log(request, AuditAction.WIPE, None,
                f"{what} โดย {form.cleaned_data['deleted_by']} (บัญชี {request.user.username}): {summary}",
                deleted_by=form.cleaned_data["deleted_by"], scope=scope, counts=before)
            messages.success(request, f"{what}แล้ว ({summary}) — คดีกลับเป็นเหมือนก่อนโทร")
            return redirect("calls_queue")
        everything = scope == "all"
        before = wipe_case_data() if everything else wipe_summonses(form.cleaned_data["restore_status"])
        summary = ", ".join(f"{k} {v:,}" for k, v in before.items())
        what = "ล้างข้อมูลคดีทั้งหมด" if everything else "ลบหมายเรียกที่ออกทั้งหมด"
        log(request, AuditAction.WIPE, None,
            f"{what} โดย {form.cleaned_data['deleted_by']} (บัญชี {request.user.username}): {summary}",
            deleted_by=form.cleaned_data["deleted_by"], scope=form.cleaned_data["scope"], counts=before)
        if everything:
            messages.success(request, f"ล้างข้อมูลแล้ว ({summary}) — นำเข้าทะเบียนคดีและไฟล์ผู้แจ้งใหม่ได้ที่เมนู นำเข้าข้อมูล")
            return redirect("import_home")
        messages.success(request, f"ลบหมายเรียกแล้ว ({summary}) — ออกหมายใหม่ได้ เลขครั้งที่จะเริ่มจาก 1")
        return redirect("summons_list")
    return render(request, "settings/wipe.html", {"form": form, "counts": counts(), "summons_counts": summons_counts(),
                                                  "call_counts": call_counts(), "phrase": WIPE_PHRASE, "tab": "wipe"})

