"""
Listing promotion: hot deals, trending and "recommended" ordering.

Hot deal
    A published, available property whose price is clearly below similar listings.
    "Similar" = same type, sale/rent, currency and rent period, in the same sector
    (or the same district if the sector has fewer than HOT_DEAL_MIN_COMPARABLES).
    Too few nearby = no verdict, so cheap areas aren't mislabelled as deals
    compared with Kigali. When sizes are known we compare
    price per m² (so a big cheap plot isn't confused with a small expensive one),
    otherwise the plain price. We use the median, which ignores extreme listings.

Trending
    Recent interest, decayed by age (like a "hot" ranking on news sites):
        (views + 5 x inquiries in last 14 days + 8 x visit requests in last 14 days)
        / (age_in_days + 2) ^ 1.3
    Featured properties get a boost so they rise to the top of "Trending".

Scores are stored on each property (fast to sort) and refreshed after any
relevant change, plus at most every 30 minutes when pages are visited.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.core.cache import cache
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.utils import timezone

from .models import Property

STALE_KEY = "property-scores-fresh"
RECENT_DAYS = 14
SCORE_FIELDS = ["market_price", "deal_percent", "is_hot_deal", "trend_score", "is_trending"]


def _comparable_key(p):
    return (p.property_type, p.purpose, p.currency, p.rent_period or "")


def _same(a, b):
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def _market_price(p, pool):
    """Typical price for `p` based on similar listings nearby, or None if there isn't enough data.

    Prices differ a lot between areas (Kimihurura vs Huye), so we compare in the same sector first,
    then the same district, and only nationwide if HOT_DEAL_ALLOW_NATIONWIDE is on.
    """
    min_n = settings.HOT_DEAL_MIN_COMPARABLES
    for area in (lambda q: _same(q.sector, p.sector), lambda q: _same(q.district, p.district)):
        local = [q for q in pool if area(q)]
        if len(local) >= min_n:
            pool = local
            break
    else:
        if not settings.HOT_DEAL_ALLOW_NATIONWIDE or len(pool) < min_n:
            return None
    sized = [q for q in pool if q.size_sqm]
    if p.size_sqm and len(sized) >= min_n:
        per_sqm = median(q.price / q.size_sqm for q in sized)
        return (per_sqm * p.size_sqm).quantize(Decimal("1"))
    return Decimal(median(q.price for q in pool)).quantize(Decimal("1"))


def refresh_scores():
    """Recompute hot deals and trending for every property. O(n²) per group: fine for thousands of listings."""
    now = timezone.now()
    since = now - timedelta(days=RECENT_DAYS)
    props = list(Property.objects.annotate(
        recent_inquiries=Count("inquiries", filter=Q(inquiries__created_at__gte=since), distinct=True),
        recent_visits=Count("appointments", filter=Q(appointments__created_at__gte=since), distinct=True),
    ))
    published = [p for p in props if p.approval_status == Property.Approval.APPROVED]
    groups = defaultdict(list)
    for p in published:
        groups[_comparable_key(p)].append(p)

    for p in props:
        p.market_price = p.deal_percent = None
        p.is_hot_deal = p.is_trending = False
        p.trend_score = 0
        if p.approval_status != Property.Approval.APPROVED:
            continue
        pool = [q for q in groups[_comparable_key(p)] if q.pk != p.pk]
        market = _market_price(p, pool)
        if market and market > 0:
            p.market_price = market
            p.deal_percent = int(round((market - p.price) / market * 100))
            p.is_hot_deal = (p.availability == Property.Availability.AVAILABLE
                             and settings.HOT_DEAL_MIN_PERCENT <= p.deal_percent <= settings.HOT_DEAL_MAX_PERCENT)
        if p.availability == Property.Availability.AVAILABLE:
            age_days = max((now - p.created_at).total_seconds() / 86400, 0)
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
    """Default order: hot deals, then new, then featured, then trending, then newest."""
    new_since = timezone.now() - timedelta(days=settings.NEW_LISTING_DAYS)
    return qs.annotate(
        is_new_rank=Case(When(created_at__gte=new_since, then=Value(1)), default=Value(0), output_field=IntegerField())
    ).order_by("-is_hot_deal", "-is_new_rank", "-is_featured", "-trend_score", "-created_at")
