from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .audit import log
from .models import AuditAction


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log(request, AuditAction.LOGIN, user=user, detail=f"เข้าสู่ระบบ {user.username}")
