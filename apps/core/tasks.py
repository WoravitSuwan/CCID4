from celery import shared_task


@shared_task
def cleanup_media():
    from django.core.management import call_command

    call_command("cleanup_media")
