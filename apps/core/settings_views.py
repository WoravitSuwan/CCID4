"""Administrator settings pages."""
from __future__ import annotations

import difflib
import re

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.documents import docx_engine
from apps.documents.models import DocTemplate, TemplateKind
from apps.documents.placeholders import SUMMONS_PLACEHOLDERS
from apps.registry.forms import StyledForm, StyledModelForm
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
class InvestigatorForm(StyledModelForm):
    class Meta:
        model = Investigator
        fields = ["rank_full", "sign_name", "position", "phone", "unit", "is_active"]


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
        "help": "ข้อมูลนี้ใช้เป็นผู้ลงนามในหมายเรียก: ยศเต็ม (เช่น พันตำรวจโท), ชื่อ-สกุล, ตำแหน่ง, โทรศัพท์",
    })


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
        fields = ["name", "full_name", "appointment_place", "sender_address"]
        widgets = {"appointment_place": forms.Textarea(attrs={"rows": 3}),
                   "sender_address": forms.Textarea(attrs={"rows": 3})}


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
        unknown = [p for p in tpl.placeholders if p not in SUMMONS_PLACEHOLDERS]
        log(request, AuditAction.SETTINGS, tpl, f"อัปโหลดแบบฟอร์ม {tpl}")
        if unknown:
            messages.warning(request, "แบบฟอร์มมีตัวแทนที่ระบบไม่รู้จัก (ต้องแก้ก่อนออกหมาย): "
                             + ", ".join(f"{{{u}}}" for u in unknown))
        else:
            messages.success(request, f"อัปโหลด {tpl} แล้ว")
        return redirect("settings_templates")
    rows = DocTemplate.objects.all()
    for t in rows:
        t.unknown = [p for p in t.placeholders if p not in SUMMONS_PLACEHOLDERS]
    return render(request, "settings/templates.html", {
        "templates": rows, "form": form, "supported": SUMMONS_PLACEHOLDERS, "tab": "templates",
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
    from django.http import FileResponse

    if not perms.can_manage_settings(request.user):
        return redirect("dashboard")
    tpl = get_object_or_404(DocTemplate, pk=pk)
    log(request, AuditAction.DOWNLOAD, tpl, f"ดาวน์โหลดแบบฟอร์ม {tpl}")
    return FileResponse(open(tpl.file.path, "rb"), as_attachment=True,
                        filename=f"{tpl.get_kind_display()}_v{tpl.version}.docx")


# --------------------------------------------------------------------------- users
class UserForm(StyledModelForm):
    password = forms.CharField(label="รหัสผ่าน", required=False, widget=forms.PasswordInput,
                               help_text="เว้นว่างถ้าไม่ต้องการเปลี่ยน (อย่างน้อย 8 ตัวอักษร)")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "role", "investigator", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = User.objects.exclude(pk=self.instance.pk).exclude(investigator__isnull=True)
        self.fields["investigator"].queryset = Investigator.objects.exclude(
            pk__in=taken.values_list("investigator_id", flat=True))
        self.fields["investigator"].help_text = "ผูกได้ 1 บัญชีต่อ 1 พนักงานสอบสวน (ใช้กำหนดว่าเป็นคดีของใคร)"
        if not self.instance.pk:
            self.fields["password"].required = True

    def clean_password(self):
        pw = self.cleaned_data.get("password")
        if pw and len(pw) < 8:
            raise forms.ValidationError("รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร")
        return pw

    def clean(self):
        data = super().clean()
        if data.get("role") == Role.INVESTIGATOR and not data.get("investigator"):
            self.add_error("investigator", "บทบาทพนักงานสอบสวนต้องผูกกับข้อมูลพนักงานสอบสวน")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


@admin_only
def users(request):
    return render(request, "settings/users.html", {
        "users": User.objects.select_related("investigator").order_by("username"), "tab": "users",
    })


@admin_only
def user_edit(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else None
    form = UserForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        log(request, AuditAction.SETTINGS, obj, f"{'แก้ไข' if user else 'เพิ่ม'}ผู้ใช้ {obj.username} ({obj.role_label})")
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_users")
    suggestions = []
    if user and not user.investigator_id:
        suggestions = suggest(f"{user.first_name}{user.last_name}", Investigator.objects.filter(user__isnull=True),
                              key=lambda i: i.sign_name)
    return render(request, "settings/form.html", {
        "form": form, "title": "แก้ไขผู้ใช้" if user else "เพิ่มผู้ใช้", "tab": "users", "back": "settings_users",
        "suggestions": suggestions,
        "help": "ผู้ดูแลระบบ: ทุกอย่าง · หัวหน้า: ดูทั้งหมด ออกหมาย ส่งออก · พนักงานสอบสวน: ดูทั้งหมด "
                "ออกหมายเฉพาะคดีของตนเอง · ธุรการ: นำเข้า ออกหมาย ส่งออก",
    })


# --------------------------------------------------------------------------- system & audit
class SystemForm(StyledModelForm):
    class Meta:
        model = SystemSetting
        fields = ["retention_days", "default_reason", "default_appointment_days"]


@admin_only
def system(request):
    form = SystemForm(request.POST or None, instance=SystemSetting.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        log(request, AuditAction.SETTINGS, form.instance, "แก้ไขการตั้งค่าระบบ")
        messages.success(request, "บันทึกแล้ว")
        return redirect("settings_system")
    return render(request, "settings/form.html", {"form": form, "title": "การตั้งค่าระบบ", "tab": "system",
                                                  "back": "settings_system"})


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
