from django import forms

from .models import AddressOverride, Investigator, Status, Unit

INPUT = "w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-600 focus:ring-blue-600"


class StyledFormMixin:
    def _style(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.RadioSelect)):
                widget.attrs.setdefault("class", "h-4 w-4 rounded border-slate-300 text-blue-700")
            else:
                widget.attrs.setdefault("class", INPUT)


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class StyledForm(StyledFormMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()


class AddressOverrideForm(StyledModelForm):
    class Meta:
        model = AddressOverride
        fields = ["recipient_name", "house_no", "moo", "building", "soi", "road", "subdistrict", "district",
                  "province", "postcode", "note"]
        widgets = {name: forms.TextInput() for name in
                   ["house_no", "moo", "building", "soi", "road", "subdistrict", "district", "province",
                    "recipient_name"]}


class CaseFilterForm(StyledForm):
    q = forms.CharField(label="ค้นหา", required=False,
                        widget=forms.TextInput(attrs={"placeholder": "เลขรับแจ้ง / ชื่อ / เลขคดี / เบอร์โทร"}))
    status = forms.ModelChoiceField(label="สถานะ", queryset=Status.objects.all(), required=False,
                                    empty_label="ทุกสถานะ")
    year = forms.TypedChoiceField(label="ปีที่รับแจ้ง", required=False, coerce=int, empty_value=None)
    unit = forms.ModelChoiceField(label="หน่วย", queryset=Unit.objects.all(), required=False, empty_label="ทุกหน่วย")
    investigator = forms.ChoiceField(label="พนักงานสอบสวน", required=False)
    case_type = forms.ChoiceField(label="ประเภทคดี", required=False)
    open = forms.BooleanField(label="ยังไม่สิ้นสุด", required=False)
    incomplete = forms.BooleanField(label="ที่อยู่ไม่ครบ", required=False)
    unmatched = forms.BooleanField(label="จับคู่ไม่ได้", required=False)
    never = forms.BooleanField(label="ยังไม่เคยออกหมาย", required=False)
    conflict = forms.BooleanField(label="ที่อยู่ในไฟล์เปลี่ยนหลังแก้เอง", required=False)

    def __init__(self, *args, years=(), case_types=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["year"].choices = [("", "ทุกปี")] + [(y, f"{y + 543}") for y in years]
        invs = [(str(i.pk), str(i)) for i in Investigator.objects.all()]
        self.fields["investigator"].choices = [("", "ทุกคน"), ("none", "— ยังไม่ได้ผูกผู้ลงนาม —")] + invs
        self.fields["case_type"].choices = [("", "ทุกประเภท")] + [(t, t) for t in case_types]
