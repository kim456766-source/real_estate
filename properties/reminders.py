"""Remind agents (or admins) to check listings that have been on the website for a month.

A listing that was (re)posted LISTING_CHECK_DAYS ago and is still on offer gets a reminder: the agent
who handles it (by email and in the dashboard), or all admins when no active agent handles it.
The reminder shows how many clients reached out, so they can decide to repost it, mark it sold /
rented, or remove it. It repeats every LISTING_REMIND_AGAIN_DAYS until they act. "Repost" starts the
30 days again.

Runs by itself at most once a day (see core.middleware.HousekeepingMiddleware), or by hand:
    python manage.py remind_old_listings
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Property

log = logging.getLogger("theo.housekeeping")
DUE_KEY = "listing-reminders-done-today"


def listings_to_check(qs=None):
    """On the website (published, not sold/rented) and (re)posted more than LISTING_CHECK_DAYS ago,
    with how many clients reached out since it was (re)posted."""
    qs = Property.objects.all() if qs is None else qs
    cutoff = timezone.now() - timedelta(days=settings.LISTING_CHECK_DAYS)
    return (qs.filter(approval_status=Property.Approval.APPROVED, listed_at__lt=cutoff)
            .exclude(availability__in=Property.TAKEN)
            .annotate(recent_inquiries=Count("inquiries", filter=Q(inquiries__created_at__gte=cutoff), distinct=True),
                      recent_visits=Count("appointments", filter=Q(appointments__created_at__gte=cutoff), distinct=True))
            .order_by("listed_at"))


def advice(prop):
    reached = prop.recent_inquiries + prop.recent_visits
    if reached == 0:
        return ("No client has contacted you about it in the last month. Check that it is still on offer, then "
                "repost it (better photos or a clearer description help) or remove it from the website.")
    return (f"{reached} client{'s' if reached != 1 else ''} reached out in the last month. If it is still on offer, "
            "repost it to bring it back to the top; if it is sold or rented, mark it so.")


def send_reminders():
    """Send the reminders that are due. Returns how many listings were reminded about."""
    from notifications.services import notify, notify_admins

    now = timezone.now()
    again = now - timedelta(days=settings.LISTING_REMIND_AGAIN_DAYS)
    # Not reminded yet, reminded over a week ago, or reminded before the latest repost
    due = listings_to_check().filter(Q(reminder_sent_at__isnull=True) | Q(reminder_sent_at__lt=again)
                                     | Q(reminder_sent_at__lt=F("listed_at")))
    count = 0
    for prop in due.select_related("agent"):
        # Claim it first, so two workers never send the same reminder twice
        if not Property.objects.filter(pk=prop.pk, reminder_sent_at=prop.reminder_sent_at).update(reminder_sent_at=now):
            continue
        days = prop.listed_days
        title = f"Check your listing: {prop.reference} has been online for {days} days"
        link = f"/dashboard/properties/{prop.pk}/edit/"
        text = (f"\"{prop.title}\" was posted {days} days ago. {advice(prop)}")
        details = [("Property", f"{prop.title} ({prop.reference})"), ("Location", prop.location_display),
                   ("Price", f"{prop.price:,.0f} {prop.currency}"), ("Online for", f"{days} days"),
                   ("Views (total)", prop.views), ("Messages in the last month", prop.recent_inquiries),
                   ("Visit requests in the last month", prop.recent_visits)]
        agent = prop.agent if prop.agent and prop.agent.is_active else None
        if agent:
            notify(agent, title, text, link, email=True, category="Listing check",
                   intro=f"One of your listings has been on {settings.SITE_NAME} for over a month.",
                   details=details, button=("Repost, update or remove it", link),
                   reason="You received this because you are the agent for this property.")
        else:
            notify_admins(title, text + " No active agent handles it.", link)
        count += 1
    if count:
        log.info("Sent %s listing check reminder(s)", count)
    return count


def remind_if_due():
    if cache.add(DUE_KEY, True, 24 * 60 * 60):  # first caller of the day
        send_reminders()
