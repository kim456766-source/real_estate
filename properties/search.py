"""Keyword search shared by the live search suggestions and the results page.

Every word typed must match somewhere (title, location, reference, type or sale/rent), so
"house kimironko" finds houses in Kimironko and "apartment rent" finds apartments for rent.
"""
import re

from django.db.models import Q

from .models import Property

MAX_QUERY = 80
MAX_WORDS = 6

# Words people type -> property types / purposes
TYPE_WORDS = {}
for value, label in Property.PropertyType.choices:
    for word in {value, label.lower(), label.lower().rstrip("s")} | set(label.lower().split()):
        if len(word) >= 3:
            TYPE_WORDS.setdefault(word, set()).add(value)
for extra, value in (("flat", "apartment"), ("flats", "apartment"), ("plots", "plot"), ("houses", "house"),
                     ("apartments", "apartment"), ("shop", "commercial"), ("office", "office")):
    if value in dict(Property.PropertyType.choices):
        TYPE_WORDS.setdefault(extra, set()).add(value)
PURPOSE_WORDS = {"rent": Property.Purpose.RENT, "rental": Property.Purpose.RENT, "renting": Property.Purpose.RENT,
                 "sale": Property.Purpose.SALE, "buy": Property.Purpose.SALE, "sell": Property.Purpose.SALE,
                 "selling": Property.Purpose.SALE}
STOP_WORDS = {"in", "at", "for", "a", "an", "the", "to", "of", "near", "with", "and"}


def words_of(text):
    text = (text or "")[:MAX_QUERY].lower()
    return [w for w in re.findall(r"[\w²-]+", text) if w not in STOP_WORDS][:MAX_WORDS]


def keyword_filter(text, with_description=False):
    """A Q() where every word must match. Empty text -> no filter."""
    q = Q()
    for word in words_of(text):
        w = Q(title__icontains=word) | Q(sector__icontains=word) | Q(district__icontains=word) \
            | Q(village__icontains=word) | Q(address__icontains=word) | Q(reference__icontains=word)
        if with_description:
            w |= Q(description__icontains=word)
        if word in TYPE_WORDS:
            w |= Q(property_type__in=TYPE_WORDS[word])
        if word in PURPOSE_WORDS:
            w |= Q(purpose=PURPOSE_WORDS[word])
        q &= w
    return q
