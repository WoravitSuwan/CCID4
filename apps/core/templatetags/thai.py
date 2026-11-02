import datetime as dt
import re

from django import template
from django.utils import timezone

from apps.core import thai_format as tf

register = template.Library()


@register.filter
def thai_date(value, style="short"):
    """UI date with พ.ศ. year (Arabic digits for readability on screen)."""
    if not value:
        return "-"
    if isinstance(value, dt.datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        if style == "datetime":
            return f"{value.day} {tf.thai_month_abbr(value.month)} {tf.ce_to_be(value)} {value:%H:%M} น."
        value = value.date()
    if style == "full":
        return tf.thai_date_full(value, thai_digits=False)
    return f"{value.day} {tf.thai_month_abbr(value.month)} {tf.ce_to_be(value)}"


@register.filter
def be_year(value):
    try:
        return tf.ce_to_be(int(value))
    except (TypeError, ValueError):
        return value


@register.filter
def mask_id(value):
    """1234567890123 -> x-xxxx-xxxxx-12-3 (only the last 3 digits are shown)."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return "-"
    if len(digits) != 13:
        return "x" * max(0, len(digits) - 3) + digits[-3:]
    return f"x-xxxx-xxxxx-{digits[10:12]}-{digits[12]}"


@register.filter
def format_id(value):
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 13:
        return value or "-"
    return f"{digits[0]}-{digits[1:5]}-{digits[5:10]}-{digits[10:12]}-{digits[12]}"


@register.filter
def money(value):
    if value is None or value == "":
        return "-"
    return f"{value:,.2f}"


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """Current querystring with some parameters replaced (for pagination/sorting)."""
    params = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v is None or v == "":
            params.pop(k, None)
        else:
            params[k] = v
    return params.urlencode()
