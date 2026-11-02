from .models import AuditAction, AuditLog


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def log(request_or_user, action: str, target=None, detail: str = "", user=None, **extra) -> AuditLog:
    """Write one audit record. Accepts a request (preferred) or a user."""
    request = request_or_user if hasattr(request_or_user, "META") else None
    if user is None:
        user = getattr(request, "user", None) if request is not None else request_or_user
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    target_type = target_id = ""
    if target is not None:
        target_type = target._meta.model_name
        target_id = str(target.pk)
    return AuditLog.objects.create(
        user=user,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail[:5000],
        extra=extra or {},
        ip_address=client_ip(request),
    )


__all__ = ["log", "AuditAction"]
