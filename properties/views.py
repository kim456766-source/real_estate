from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import F, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from core.utils import client_ip
from favorites.session import favorite_ids
from inquiries.forms import InquiryForm
from notifications.services import email_contact, email_support, notify_admins

from .filters import PropertyFilter
from .forms import PUBLIC_PURPOSES, PUBLIC_TYPE_VALUES, PUBLIC_TYPES, PublicPropertyForm, SubmissionStatusForm
from .models import Property
from .ranking import recommended, refresh_if_stale
from .search import MAX_QUERY, keyword_filter, words_of
from .similar import similar_properties
from .utils import save_property_images, same_phone

# Public "category" pages are the listing page with a preset filter.
PRESETS = {
    "buy": {"title": "Properties for Sale", "filters": {"purpose": Property.Purpose.SALE}},
    "rent": {"title": "Properties for Rent", "filters": {"purpose": Property.Purpose.RENT}},
    "land": {"title": "Plots & Land", "filters": {"property_type__in": Property.LAND_TYPES}},
    "commercial": {"title": "Commercial Properties",
                   "filters": {"property_type__in": [Property.PropertyType.COMMERCIAL, Property.PropertyType.OFFICE]}},
    "deals": {"title": "Hot Deals", "filters": {"is_hot_deal": True},
              "subtitle": "Priced at least 10% below similar properties in the same area."},
}

SUBMISSIONS_PER_HOUR = 5


def public_properties():
    """Listings shown on the website: published and still on offer (sold / rented ones are hidden)."""
    return (Property.objects.filter(approval_status=Property.Approval.APPROVED)
            .exclude(availability__in=Property.TAKEN)
            .select_related("category", "agent").prefetch_related("images"))


def property_list(request, preset=None):
    refresh_if_stale()
    qs = recommended(public_properties())  # hot deals, new, featured, trending first (unless the visitor sorts)
    title, subtitle = "All Properties", ""
    if preset:
        conf = PRESETS[preset]
        qs = qs.filter(**conf["filters"])
        title, subtitle = conf["title"], conf.get("subtitle", "")
    if preset == "deals":
        qs = qs.order_by("-deal_percent")
    f = PropertyFilter(request.GET, queryset=qs)
    page = Paginator(f.qs, 12).get_page(request.GET.get("page"))
    return render(request, "properties/list.html", {
        "filter": f, "page_obj": page, "title": title, "subtitle": subtitle, "preset": preset,
        "has_filters": any(v for k, v in request.GET.items() if k != "page"),
    })


def search_suggest(request):
    """Live search (as the visitor types): matching places and properties, as JSON."""
    text = request.GET.get("q", "")[:MAX_QUERY].strip()
    if len(text) < 2:
        return JsonResponse({"places": [], "properties": [], "total": 0})
    words = words_of(text)
    base = public_properties()
    matches = base.filter(keyword_filter(text)) if words else base.none()

    # Places: sectors / districts that contain one of the words, with how many listings each has
    places = {}
    if words:
        place_q = Q()
        for w in words:
            place_q |= Q(sector__icontains=w) | Q(district__icontains=w)
        for sector, district in base.filter(place_q).values_list("sector", "district")[:200]:
            for name in (sector, district):
                name = (name or "").strip()
                if name and any(w in name.lower() for w in words):
                    key = name.lower()  # "kimironko" and "Kimironko" are the same place
                    shown, n = places.get(key, (name, 0))
                    places[key] = (shown if shown[:1].isupper() else name, n + 1)
    places = sorted(places.values(), key=lambda kv: -kv[1])[:4]

    props = []
    for p in recommended(matches)[:6]:
        cover = p.cover_image
        props.append({
            "title": p.title, "reference": p.reference, "url": p.get_absolute_url(),
            "price": f"{intcomma(int(p.price))} {p.currency}" + (f" / {p.rent_period}" if p.rent_period else ""),
            "location": p.location_display, "type": p.get_property_type_display(),
            "purpose": p.get_purpose_display(), "image": cover.image.url if cover else "",
        })
    return JsonResponse({
        "places": [{"name": n, "count": c} for n, c in places],
        "properties": props, "total": matches.count(),
    })


def property_detail(request, slug):
    prop = get_object_or_404(Property.objects.select_related("agent", "category")
                             .prefetch_related("images"), slug=slug)
    user = request.user
    can_preview = user.is_authenticated and user.uses_dashboard
    if not prop.is_public and not can_preview:
        raise Http404
    Property.objects.filter(pk=prop.pk).update(views=F("views") + 1)

    similar = similar_properties(prop, public_properties())  # same area or close price (properties/similar.py)
    public_qa = (prop.inquiries.filter(is_public=True).select_related("assigned_agent")
                 .prefetch_related("thread__author").order_by("-updated_at")[:20])
    return render(request, "properties/detail.html", {
        "property": prop,
        "public_qa": public_qa,
        "inquiry_form": InquiryForm(initial={"message": f"Hello, I'm interested in {prop.title} ({prop.reference})."}),
        "similar": similar,
        "is_favorite": prop.pk in favorite_ids(request),
    })


def submit_property(request):
    """Public form: owners list a property without creating an account.

    Step 1: choose the kind of property and sale/rent (?type=house&purpose=sale).
    Step 2: the form for that kind. Any other kind is refused.
    """
    ptype = request.GET.get("type", "")
    purpose = request.GET.get("purpose", "")
    valid = ptype in PUBLIC_TYPE_VALUES and purpose in ("sale", "rent")
    if not valid or "change" in request.GET:
        if not valid and (request.GET or request.method == "POST"):
            messages.error(request, "Please choose one of the property types below and whether it is for sale or rent.")
        return render(request, "properties/submit_choose.html", {
            "types": PUBLIC_TYPES, "purposes": PUBLIC_PURPOSES,
            "selected_type": ptype if ptype in PUBLIC_TYPE_VALUES else "",
            "selected_purpose": purpose if purpose in ("sale", "rent") else ""})

    form = PublicPropertyForm(request.POST or None, request.FILES or None, property_type=ptype, purpose=purpose)
    ctx = {"form": form, "type_label": dict((t[0], t[1]) for t in PUBLIC_TYPES)[ptype],
           "type_icon": dict((t[0], t[2]) for t in PUBLIC_TYPES)[ptype],
           "purpose_label": dict((p[0], p[1]) for p in PUBLIC_PURPOSES)[purpose],
           "ptype": ptype, "purpose": purpose, "is_land": ptype in Property.LAND_TYPES}
    if request.method == "POST":
        key = f"property-submit:{client_ip(request)}"
        if cache.get(key, 0) >= SUBMISSIONS_PER_HOUR:
            messages.error(request, "Too many submissions from your connection. Please try again later "
                                    "or contact us directly.")
            return render(request, "properties/submit.html", ctx)
        if form.is_valid():
            prop = form.save(commit=False)
            prop.source = Property.Source.WEBSITE
            prop.approval_status = Property.Approval.PENDING
            prop.save()
            save_property_images(prop, form.cleaned_data["new_images"])
            cache.set(key, cache.get(key, 0) + 1, 3600)

            msg = (f"{prop.owner_name} ({prop.owner_phone}) submitted \"{prop.title}\" in {prop.location_display}. "
                   "Assign it to an agent.")
            link = f"/dashboard/properties/{prop.pk}/edit/"
            notify_admins("New property submission", msg, link)
            details = [("Property", prop.title), ("Reference", prop.reference),
                       ("Type", f"{prop.get_property_type_display()} · {prop.get_purpose_display()}"),
                       ("Location", prop.location_display), ("Price", f"{prop.price:,.0f} {prop.currency}"),
                       ("Owner", prop.owner_name), ("Owner phone", prop.owner_phone), ("Owner email", prop.owner_email)]
            email_support("New property submission",
                          f"An owner submitted a property on the website. Assign it to an agent so they can "
                          f"call {prop.owner_name or 'the owner'}, check it and publish it.",
                          reply_to=prop.owner_email or None, link=request.build_absolute_uri(link),
                          details=details, category="New property")
            if prop.owner_email:
                email_contact(
                    prop.owner_email, f"We received your property ({prop.reference})",
                    f"An agent will call you on {prop.owner_phone} to check the details and arrange photos. "
                    "Once it's verified, we publish it and advertise it on our website and social media.",
                    name=prop.owner_name, category="Your property",
                    intro=f"Thank you for listing your property with {settings.SITE_NAME}.",
                    details=details[:5] + [("Status", "Waiting for an agent to verify")],
                    button=("Check your listing status", reverse("properties:status")),
                    note=f"Keep your reference number ({prop.reference}). Our agents will never ask you to pay "
                         "money before your property is advertised.",
                    reason=f"You received this because you submitted a property on {settings.SITE_NAME}.")
            request.session["last_submission"] = prop.reference
            return redirect("properties:submit_done")
    return render(request, "properties/submit.html", ctx)


def submit_done(request):
    reference = request.session.get("last_submission")
    if not reference:
        return redirect("properties:submit")
    return render(request, "properties/submit_done.html", {"reference": reference})


def submission_status(request):
    """Owners check their listing status with reference + phone (no account needed)."""
    form = SubmissionStatusForm(request.GET or None)
    prop = None
    if request.GET and form.is_valid():
        candidate = Property.objects.filter(reference__iexact=form.cleaned_data["reference"].strip()).first()
        if candidate and same_phone(candidate.owner_phone, form.cleaned_data["phone"]):
            prop = candidate
        else:
            messages.error(request, "No property found with that reference and phone number.")
    return render(request, "properties/status.html", {"form": form, "property": prop})
