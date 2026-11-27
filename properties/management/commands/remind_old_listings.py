"""Remind agents/admins about listings online for a month.  python manage.py remind_old_listings"""
from django.core.management.base import BaseCommand

from properties.reminders import send_reminders


class Command(BaseCommand):
    help = "Remind agents (or admins) to repost or remove listings that have been online for a month"

    def handle(self, *args, **opts):
        n = send_reminders()
        self.stdout.write(self.style.SUCCESS(f"Sent {n} reminder(s)."))
