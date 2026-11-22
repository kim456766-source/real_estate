from django.db.models import Count, Q
from django.shortcuts import render

from accounts.models import User
from inquiries.models import Inquiry
from properties.filters import PropertyFilter
from properties.models import Category, Property
from properties.ranking import refresh_if_stale
from properties.views import public_properties


def home(request):
    refresh_if_stale()
    props = public_properties()
    available = props.filter(availability=Property.Availability.AVAILABLE)
    hot_deals = available.filter(is_hot_deal=True).order_by("-deal_percent")[:8]
    trending = available.filter(trend_score__gt=0).order_by("-trend_score")[:8]   # featured get a boost
    just_listed = available.order_by("-created_at")[:8]
    categories = Category.objects.annotate(
        n=Count("properties", filter=Q(properties__approval_status=Property.Approval.APPROVED)))
    stats = {
        "properties": props.count(),
        "agents": User.objects.filter(role=User.Role.AGENT, is_active=True).count(),
        "locations": props.exclude(sector="").values("sector").distinct().count(),
        "inquiries": Inquiry.objects.count(),
    }
    return render(request, "pages/home.html", {
        "hot_deals": hot_deals, "trending": trending, "just_listed": just_listed,
        "categories": categories, "stats": stats,
        "filter": PropertyFilter(),
    })


def about(request):
    agents = User.objects.filter(role=User.Role.AGENT, is_active=True).select_related("agent_profile")[:8]
    return render(request, "pages/about.html", {"agents": agents})


# Support is by WhatsApp and phone. Each button opens WhatsApp with the question already typed.
WHATSAPP_TOPICS = [
    ("I want to buy", "Hello Theo Property, I want to buy a property. My budget is "),
    ("I want to rent", "Hello Theo Property, I'm looking for a place to rent in "),
    ("I want to sell or rent out", "Hello Theo Property, I'm a property owner and I want to sell / rent out my property."),
    ("Book a visit", "Hello Theo Property, I'd like to visit a property. Reference: "),
    ("Other question", "Hello Theo Property, I need help with "),
]


def contact(request):
    return render(request, "pages/contact.html", {"whatsapp_topics": WHATSAPP_TOPICS})
