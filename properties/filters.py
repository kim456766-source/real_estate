import django_filters
from django import forms
from django.db.models import Q

from .models import Property
from .search import keyword_filter

SORT_CHOICES = [
    ("-created_at", "Newest first"),  # no sort = "Recommended" (properties/ranking.py)
    ("price", "Price: low to high"),
    ("-price", "Price: high to low"),
    ("-size_sqm", "Largest first"),
    ("-views", "Most viewed"),
]


class PropertyFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method="search", label="Keyword")
    location = django_filters.CharFilter(method="by_location", label="Location")
    property_type = django_filters.ChoiceFilter(choices=Property.PropertyType.choices, empty_label="Any type")
    purpose = django_filters.ChoiceFilter(choices=Property.Purpose.choices, empty_label="Buy or rent")
    availability = django_filters.ChoiceFilter(  # sold / rented listings are not shown publicly
        choices=[c for c in Property.Availability.choices if c[0] not in Property.TAKEN], empty_label="Any status")
    min_price = django_filters.NumberFilter(field_name="price", lookup_expr="gte", label="Min price")
    max_price = django_filters.NumberFilter(field_name="price", lookup_expr="lte", label="Max price")
    bedrooms = django_filters.NumberFilter(field_name="bedrooms", lookup_expr="gte", label="Bedrooms (min)")
    bathrooms = django_filters.NumberFilter(field_name="bathrooms", lookup_expr="gte", label="Bathrooms (min)")
    min_size = django_filters.NumberFilter(field_name="size_sqm", lookup_expr="gte", label="Min size (m²)")
    max_size = django_filters.NumberFilter(field_name="size_sqm", lookup_expr="lte", label="Max size (m²)")
    category = django_filters.CharFilter(field_name="category__slug")
    sort = django_filters.OrderingFilter(
        fields=(("created_at", "created_at"), ("price", "price"), ("size_sqm", "size_sqm"), ("views", "views")),
        choices=SORT_CHOICES, empty_label="Sort by", label="Sort",
    )

    class Meta:
        model = Property
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.form.fields.values():
            css = "form-select" if isinstance(f.widget, forms.Select) else "form-control"
            f.widget.attrs.setdefault("class", css)
            f.widget.attrs.setdefault("placeholder", f.label)

    def search(self, qs, name, value):
        # Same matching as the live search box, plus the description (see properties/search.py)
        return qs.filter(keyword_filter(value, with_description=True))

    def by_location(self, qs, name, value):
        return qs.filter(Q(district__icontains=value) | Q(sector__icontains=value)
                         | Q(village__icontains=value) | Q(address__icontains=value))
