"""
Listing promotion: hot deals, trending and "recommended" ordering.

Hot deal
    Chosen by an administrator for a set time (Property.hot_deal_until), whatever the listing's age.
    Nothing is computed from prices: prices are set by the agent, owner or admin, with no discount.
    Hot deals are shown first on the website until their time runs out.

Trending
    Recent interest, decayed by age (like a "hot" ranking on news sites):
        (views + 5 x inquiries in last 14 days + 8 x visit requests in last 14 days)
        / (age_in_days + 2) ^ 1.3
    Featured properties get a boost so they rise to the top of "Trending".

Scores are stored on each property (fast to sort) and refreshed after any
relevant change, plus at most every 30 minutes when pages are visited.
"""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone

from .models import Property

STALE_KEY = "property-scores-fresh"
RECENT_DAYS = 14
SCORE_FIELDS = ["trend_score", "is_trending"]


def refresh_scores():
    """Recompute trending for every property."""
    now = timezone.now()
    since = now - timedelta(days=RECENT_DAYS)
    props = list(Property.objects.annotate(
        recent_inquiries=Count("inquiries", filter=Q(inquiries__created_at__gte=since), distinct=True),
        recent_visits=Count("appointments", filter=Q(appointments__created_at__gte=since), distinct=True),
    ))
    for p in props:
        p.is_trending = False
        p.trend_score = 0
        if p.approval_status == Property.Approval.APPROVED and p.availability == Property.Availability.AVAILABLE:
            age_days = max((now - p.listed_at).total_seconds() / 86400, 0)
            interest = p.views + 5 * p.recent_inquiries + 8 * p.recent_visits
            score = (interest + 1) / (age_days + 2) ** 1.3
            p.trend_score = round(score * (2 if p.is_featured else 1), 4)

    ranked = sorted((p for p in props if p.trend_score > 0), key=lambda p: p.trend_score, reverse=True)
    for p in ranked[:settings.TRENDING_COUNT]:
        p.is_trending = True

    Property.objects.bulk_update(props, SCORE_FIELDS, batch_size=500)  # bulk_update sends no signals
    cache.set(STALE_KEY, True, 30 * 60)
    return len(props)


def refresh_if_stale():
    if not cache.get(STALE_KEY):
        refresh_scores()


def recommended(qs):
    """Default order: hot deals (picked by an admin), then new, then featured, then trending, then newest."""
    now = timezone.now()
    new_since = now - timedelta(days=settings.NEW_LISTING_DAYS)
    return qs.annotate(
        is_hot_rank=Case(When(Property.hot_deal_q(), then=Value(1)), default=Value(0), output_field=IntegerField()),
        is_new_rank=Case(When(listed_at__gte=new_since, then=Value(1)), default=Value(0), output_field=IntegerField()),
    ).order_by("-is_hot_rank", "-is_new_rank", "-is_featured", "-trend_score", "-listed_at")
