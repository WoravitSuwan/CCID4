"""Send the daily summary to the LINE group.

    python manage.py send_line_report            # today's summary, now
    python manage.py send_line_report --date 2026-11-09
    python manage.py send_line_report --if-due   # for cron / Task Scheduler without Celery: every 5-10 minutes
    python manage.py send_line_report --print    # show the text only, send nothing
"""
import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.calls import line_bot


class Command(BaseCommand):
    help = "ส่งสรุปผลประจำวันเข้ากลุ่ม LINE"

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
        parser.add_argument("--if-due", action="store_true", help="send only when the set time has passed")
        parser.add_argument("--print", action="store_true", help="print the text, send nothing")

    def handle(self, *args, **opts):
        if opts["if_due"]:
            self.stdout.write(line_bot.send_if_due())
            return
        try:
            day = dt.date.fromisoformat(opts["date"]) if opts["date"] else timezone.localdate()
        except ValueError as exc:
            raise CommandError("วันที่ต้องเป็นรูปแบบ YYYY-MM-DD") from exc
        if opts["print"]:
            self.stdout.write("\n\n".join(line_bot.texts_for(day)))
            return
        try:
            total = line_bot.send_day(day)
        except line_bot.LineError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"ส่งแล้ว ({total} Case)"))
