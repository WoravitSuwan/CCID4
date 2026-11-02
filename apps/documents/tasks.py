import logging

from celery import shared_task
from django.utils import timezone

from .models import ExportJob, JobStatus

logger = logging.getLogger(__name__)


@shared_task
def export_job(job_id):
    from .services import run_export

    job = ExportJob.objects.get(pk=job_id)
    try:
        run_export(job)
    except Exception as exc:  # report to the UI
        logger.exception("export %s failed", job_id)
        job.status = JobStatus.FAILED
        job.message = str(exc)[:500]
        job.finished_at = timezone.now()
        job.save()
