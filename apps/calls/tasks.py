from apps.core.tasking import shared_task


@shared_task
def line_daily_summary():
    from .line_bot import send_if_due

    return send_if_due()
