import logging

from celery import shared_task

from .models import BatchStatus, ImportBatch

logger = logging.getLogger(__name__)


def _progress(batch_id):
    def update(value):
        ImportBatch.objects.filter(pk=batch_id).update(progress=max(0, min(100, int(value))))
    return update


@shared_task
def analyze_batch(batch_id):
    from .services import analyze, store_analysis

    batch = ImportBatch.objects.get(pk=batch_id)
    try:
        analysis = analyze(batch, progress=_progress(batch_id))
        store_analysis(batch, analysis)
        batch.status = BatchStatus.ANALYZED
        batch.progress = 100
        batch.save()
    except Exception as exc:
        logger.exception("analyze %s failed", batch_id)
        ImportBatch.objects.filter(pk=batch_id).update(status=BatchStatus.FAILED, error=str(exc)[:2000])


@shared_task
def commit_batch(batch_id, user_id=None):
    from django.contrib.auth import get_user_model

    from apps.core.audit import log
    from apps.core.models import AuditAction

    from .services import commit

    batch = ImportBatch.objects.get(pk=batch_id)
    try:
        commit(batch, progress=_progress(batch_id))
        user = get_user_model().objects.filter(pk=user_id).first()
        s = batch.summary
        log(user, AuditAction.IMPORT, batch,
            f"นำเข้า{batch.get_kind_display()} {batch.original_filename} ชีต {', '.join(batch.sheets)}: "
            f"เพิ่ม {s['new']} อัปเดต {s['updated']} มีปัญหา {s['issue_rows']} จับคู่ไม่ได้ {s['unmatched']}")
    except Exception as exc:
        logger.exception("commit %s failed", batch_id)
        ImportBatch.objects.filter(pk=batch_id).update(status=BatchStatus.FAILED, error=str(exc)[:2000])


def run(task, *args):
    """Queue in Celery; if the broker is unavailable run inline so the UI never hangs."""
    from django.conf import settings

    if settings.CELERY_TASK_ALWAYS_EAGER:
        return task(*args)
    try:
        return task.delay(*args)
    except Exception:
        logger.exception("celery unavailable, running %s inline", task.name)
        return task(*args)
