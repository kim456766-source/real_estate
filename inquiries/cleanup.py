"""Delete old conversations to save storage.

A conversation (the inquiry and all its messages) is deleted RETENTION_DAYS after its LAST activity,
so a chat that is still going on is never cut off. It runs by itself at most once a day (triggered
by normal site traffic, see core.middleware.HousekeepingMiddleware), and can also be run by hand or
from a scheduled task:  python manage.py purge_old_inquiries
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import Inquiry

log = logging.getLogger("theo.housekeeping")

RETENTION_DAYS = getattr(settings, "INQUIRY_RETENTION_DAYS", 30)
DUE_KEY = "inquiry-purge-done-today"


def purge_old_inquiries(days=None):
    """Delete conversations with no activity for `days` days. Returns how many were deleted."""
    cutoff = timezone.now() - timedelta(days=days or RETENTION_DAYS)
    old = Inquiry.objects.filter(updated_at__lt=cutoff)
    count = old.count()
    if count:
        old.delete()  # their messages are deleted with them
        log.info("Deleted %s conversation(s) with no activity since %s", count, cutoff.date())
    return count


def purge_if_due():
    """Run the clean-up at most once a day."""
    if cache.add(DUE_KEY, True, 24 * 60 * 60):  # add() only succeeds for the first caller of the day
        purge_old_inquiries()
