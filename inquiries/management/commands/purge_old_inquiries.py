from django.core.management.base import BaseCommand

from inquiries.cleanup import RETENTION_DAYS, purge_old_inquiries


class Command(BaseCommand):
    help = f"Delete conversations with no activity for {RETENTION_DAYS} days (also runs by itself once a day)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=RETENTION_DAYS)

    def handle(self, *args, **opts):
        n = purge_old_inquiries(opts["days"])
        self.stdout.write(self.style.SUCCESS(f"Deleted {n} old conversation(s)."))
