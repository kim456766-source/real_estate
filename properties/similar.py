"""'Similar properties' on a property page.

Similar = same type and same sale/rent, AND at least one of:
  * same area (same sector, or else the same district), or
  * a close price: within PRICE_RANGE (±30%) in the same currency (and the same rent period for rentals).
Best first: same area + close price, then same area, then close price; ties go to the nearest price.
Nothing qualifies -> nothing is shown (better than unrelated listings).
"""
from decimal import Decimal

from django.db.models import Q

PRICE_RANGE = Decimal("0.30")
MAX_SHOWN = 4
POOL = 300  # candidates examined (enough for a city-sized catalogue)


def _same(a, b):
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def similar_properties(prop, queryset, limit=MAX_SHOWN):
    """`queryset` = what the public may see. Returns a list; each item has `.similar_reason`."""
    low, high = prop.price * (1 - PRICE_RANGE), prop.price * (1 + PRICE_RANGE)
    area = Q()
    if prop.sector:
        area |= Q(sector__iexact=prop.sector.strip())
    if prop.district:
        area |= Q(district__iexact=prop.district.strip())
    price = Q(currency=prop.currency, price__gte=low, price__lte=high)
    if prop.purpose == prop.Purpose.RENT:
        price &= Q(rent_period=prop.rent_period)
    candidates = (queryset.filter(property_type=prop.property_type, purpose=prop.purpose)
                  .exclude(pk=prop.pk).filter(area | price)[:POOL])

    ranked = []
    for p in candidates:
        same_sector = _same(p.sector, prop.sector)
        same_area = same_sector or _same(p.district, prop.district)
        close_price = (p.currency == prop.currency and low <= p.price <= high
                       and (prop.purpose != prop.Purpose.RENT or p.rent_period == prop.rent_period))
        if not (same_area or close_price):
            continue
        place = p.sector if same_sector else p.district
        if same_area and close_price:
            p.similar_reason = f"Same area ({place}) · similar price"
        elif same_area:
            p.similar_reason = f"Same area ({place})"
        else:
            p.similar_reason = "Similar price"
        tier = 0 if (same_area and close_price) else 1 if same_area else 2
        gap = abs(p.price - prop.price) / prop.price if prop.price else Decimal(0)
        if p.currency != prop.currency:
            gap = Decimal(99)  # different currency: prices can't be compared
        ranked.append((tier, 0 if same_sector else 1, gap, p))  # then same sector before same district
    ranked.sort(key=lambda t: t[:3])
    return [t[3] for t in ranked[:limit]]
