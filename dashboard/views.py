import csv
import io
import zipfile
from datetime import datetime, time, timedelta

import logging
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMessage
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.urls import reverse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import (AdminCreateForm, AgentCreateForm, AgentProfileForm, AgentReviewForm, BootstrapFormMixin,
                            InitialPasswordForm, UserRoleForm)
from accounts.models import AgentProfile, User
from appointments.forms import AppointmentManageForm
from appointments.models import Appointment
from core.models import AuditLog, SiteSetting
from core.security import SafeCsvWriter, safe_next_url
from core.utils import log_action
from inquiries.device import listing_text, whatsapp_link
from inquiries.forms import InquiryResponseForm
from inquiries.models import ContactMessage, Inquiry, InquiryMessage
from notifications.emails import send_branded
from notifications.services import email_contact, notify, notify_admins
from payments.forms import PaymentForm
from payments.gateways import GatewayError, GatewayNotConfigured, get_gateway
from payments.models import Payment
from properties.forms import CategoryForm, PropertyForm, PropertyReviewForm
from properties.models import Category, Property, PropertyImage
from properties.reminders import advice, listings_to_check
from properties.utils import PHOTO_UPLOAD_FAILED, PhotoUploadError, save_property_images


from .scoping import (appointments_for, can_edit_property, can_manage_appointments, can_manage_listing, can_publish,
                      inquiries_for, properties_for)

logger = logging.getLogger("theo.dashboard")


# ---------- access decorators ----------
def dashboard_required(view):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.uses_dashboard:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def admin_required(view):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_admin_role:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def paginate(request, qs, n=15):
    return Paginator(qs, n).get_page(request.GET.get("page"))


# ---------- overview ----------
@dashboard_required
def home(request):
    user = request.user
    props = properties_for(user)
    inquiries = inquiries_for(user)
    appts = appointments_for(user)
    now = timezone.now()
    stats = {
        "total": props.count(),
        "available": props.filter(availability=Property.Availability.AVAILABLE).count(),
        "sold": props.filter(availability=Property.Availability.SOLD).count(),
        "rented": props.filter(availability=Property.Availability.RENTED).count(),
        "pending": props.filter(approval_status=Property.Approval.PENDING).count(),
        "inquiries": inquiries.count(),
        "new_inquiries": inquiries.filter(status=Inquiry.Status.NEW).count(),
        "upcoming": appts.filter(scheduled_for__gte=now).exclude(status=Appointment.Status.CANCELLED).count(),
        "unclaimed": props.filter(agent__isnull=True).count() if user.is_admin_role else 0,
        "mine": props.filter(agent=user).count() if user.is_agent else 0,
    }
    if user.is_admin_role:
        stats.update({
            "clients": len({(e or "").lower() for e in inquiries.values_list("email", flat=True)}
                           | {p for p in appts.exclude(phone="").values_list("phone", flat=True)}),
            "agents": User.objects.filter(role=User.Role.AGENT).count(),
            "owners": props.exclude(owner_phone="").values("owner_phone").distinct().count(),
            "payments": Payment.objects.count(),
            "revenue": Payment.objects.filter(status=Payment.Status.SUCCESSFUL, currency="RWF")
                                      .aggregate(s=Sum("amount"))["s"] or 0,
        })

    by_type = list(props.values("property_type").annotate(n=Count("id")).order_by("-n"))
    type_labels = dict(Property.PropertyType.choices)
    six_months = now - timedelta(days=180)
    monthly_listings = (props.filter(created_at__gte=six_months).annotate(m=TruncMonth("created_at"))
                        .values("m").annotate(n=Count("id")).order_by("m"))
    monthly_inquiries = (inquiries.filter(created_at__gte=six_months).annotate(m=TruncMonth("created_at"))
                         .values("m").annotate(n=Count("id")).order_by("m"))
    months = sorted({r["m"] for r in monthly_listings} | {r["m"] for r in monthly_inquiries})
    lmap = {r["m"]: r["n"] for r in monthly_listings}
    imap = {r["m"]: r["n"] for r in monthly_inquiries}
    charts = {
        "type_labels": [type_labels.get(r["property_type"], r["property_type"]) for r in by_type],
        "type_values": [r["n"] for r in by_type],
        "month_labels": [m.strftime("%b %Y") for m in months],
        "month_listings": [lmap.get(m, 0) for m in months],
        "month_inquiries": [imap.get(m, 0) for m in months],
        "status_labels": ["Available", "Reserved", "Sold", "Rented"],
        "status_values": [props.filter(availability=s).count() for s in
                          ("available", "reserved", "sold", "rented")],
    }
    return render(request, "dashboard/home.html", {
        "stats": stats, "charts": charts,
        "recent_inquiries": inquiries[:5],
        "upcoming_appointments": appts.filter(scheduled_for__gte=now)
                                      .exclude(status=Appointment.Status.CANCELLED)[:5],
        "pending_properties": props.filter(approval_status=Property.Approval.PENDING)[:5],
        "to_check": listings_to_check(props)[:5],
        "to_check_count": listings_to_check(props).count(),
        "check_days": settings.LISTING_CHECK_DAYS,
    })


# ---------- properties ----------
@dashboard_required
def property_list(request):
    qs = properties_for(request.user)
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    assigned = request.GET.get("assigned", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(reference__icontains=q) | Q(sector__icontains=q) | Q(district__icontains=q)
                       | Q(owner_name__icontains=q) | Q(owner_phone__icontains=q))
    if status:
        qs = qs.filter(approval_status=status)
    if assigned == "unclaimed":
        qs = qs.filter(agent__isnull=True)
    elif assigned == "mine":
        qs = qs.filter(agent=request.user)
    elif assigned == "submissions":
        qs = qs.filter(source=Property.Source.WEBSITE)
    elif assigned == "check":  # online for a month: repost, mark sold/rented or remove
        qs = listings_to_check(qs)
    elif assigned == "hot":
        qs = qs.filter(Property.hot_deal_q())
    return render(request, "dashboard/properties/list.html", {
        "page_obj": paginate(request, qs), "q": q, "status": status, "assigned": assigned,
        "statuses": Property.Approval.choices,
        "agents": (User.objects.filter(role=User.Role.AGENT, is_active=True).order_by("first_name", "username")
                   if request.user.is_admin_role else []),
    })


@dashboard_required
def property_create(request):
    form = PropertyForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        prop = form.save(commit=False)
        user = request.user
        prop.created_by = user
        if user.is_owner:
            prop.owner = user
        if user.is_agent:
            prop.agent = user
        auto_publish = user.is_admin_role or (
            user.is_agent and getattr(user.agent_profile, "can_publish_directly", False))
        prop.approval_status = Property.Approval.APPROVED if auto_publish else Property.Approval.PENDING
        try:
            with transaction.atomic():  # listing + photos are saved together, or not at all
                prop.save()
                save_property_images(prop, form.cleaned_data["new_images"])
        except PhotoUploadError:
            form.add_error("new_images", PHOTO_UPLOAD_FAILED)
            return render(request, "dashboard/properties/form.html", {"form": form, "is_new": True})
        log_action(request, "property_created", prop)
        if not auto_publish:
            notify_admins("Property awaiting approval", f"{user} submitted {prop.title}.",
                          f"/dashboard/properties/{prop.pk}/review/")
        messages.success(request, "Property saved." + ("" if auto_publish else " It will be public after approval."))
        return redirect("dashboard:property_edit", pk=prop.pk)
    return render(request, "dashboard/properties/form.html", {"form": form, "is_new": True})


def _edit_context(request, form, prop):
    check = listings_to_check(Property.objects.filter(pk=prop.pk)).first()  # online for a month?
    return {"form": form, "property": prop, "is_new": False, "can_publish": can_publish(request.user, prop),
            "can_manage": can_manage_listing(request.user, prop),
            "check": check, "check_advice": advice(check) if check else "",
            "hot_deal_max_days": settings.HOT_DEAL_MAX_DAYS, "today": timezone.localdate()}


@dashboard_required
def property_edit(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    if not can_edit_property(request.user, prop):
        raise PermissionDenied
    form = PropertyForm(request.POST or None, request.FILES or None, instance=prop, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():  # changes + new photos are saved together, or not at all
                prop = form.save()
                save_property_images(prop, form.cleaned_data["new_images"])
        except PhotoUploadError:
            form.add_error("new_images", PHOTO_UPLOAD_FAILED)
            prop = Property.objects.get(pk=pk)  # what is really saved (the attempt was undone)
            return render(request, "dashboard/properties/form.html", _edit_context(request, form, prop))
        log_action(request, "property_updated", prop)
        messages.success(request, "Property updated.")
        return redirect("dashboard:property_edit", pk=prop.pk)
    return render(request, "dashboard/properties/form.html", _edit_context(request, form, prop))


@admin_required
@require_POST
def property_assign(request, pk):
    """Admin gives a property (e.g. an owner submission) to an agent. Only that agent will see it."""
    prop = get_object_or_404(Property, pk=pk)
    agent = User.objects.filter(pk=request.POST.get("agent") or 0, role=User.Role.AGENT, is_active=True).first()
    if agent is None:
        messages.error(request, "Choose an active agent.")
        return redirect(safe_next_url(request, reverse("dashboard:properties")))
    prop.agent = agent
    prop.save(update_fields=["agent", "updated_at"])  # open client requests move with it (properties/signals.py)
    log_action(request, "property_assigned", prop, f"Assigned to {agent}")
    notify(agent, "New property assigned to you",
           "Next steps: call the owner to check the details and arrange photos, complete the listing, "
           "then publish and advertise it.",
           f"/dashboard/properties/{prop.pk}/edit/", email=True, category="Property",
           intro=f"{request.user} gave you a property to handle.",
           details=[("Property", prop.title), ("Reference", prop.reference), ("Location", prop.location_display),
                    ("Price", f"{prop.price:,.0f} {prop.currency}"), ("Owner", prop.owner_name),
                    ("Owner phone", prop.owner_phone)],
           button=("Open the property", f"/dashboard/properties/{prop.pk}/edit/"),
           reason="You received this because an administrator assigned this property to you.")
    messages.success(request, f"{prop.reference} is now handled by {agent.get_full_name() or agent.username}.")
    return redirect(safe_next_url(request, reverse("dashboard:properties")))


@dashboard_required
@require_POST
def property_release(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    if not (request.user.is_admin_role or prop.agent_id == request.user.pk):
        raise PermissionDenied
    prop.agent = None
    prop.save(update_fields=["agent", "updated_at"])
    log_action(request, "property_released", prop)
    if not request.user.is_admin_role:
        notify_admins("Property released", f"{request.user} released {prop.reference} – {prop.title}. "
                      "Assign it to another agent.", "/dashboard/properties/?assigned=unclaimed")
    messages.info(request, f"{prop.reference} is unassigned. An administrator will give it to another agent.")
    return redirect("dashboard:properties")


@dashboard_required
def property_share(request, pk):
    """Share kit: ready-made ad text, share links and photos for posting on other platforms."""
    prop = get_object_or_404(properties_for(request.user), pk=pk)
    url = request.build_absolute_uri(prop.get_absolute_url())
    agent = prop.agent or request.user
    contact = agent.phone or SiteSetting.load().phone
    price = f"{prop.price:,.0f} {prop.currency}" + (f" / {prop.rent_period}" if prop.purpose == "rent" and prop.rent_period else "")
    lines = [f"🏡 {prop.get_purpose_display().upper()}: {prop.title}",
             f"📍 {prop.location_display}",
             f"💰 {price}"]
    if not prop.is_land:
        lines.append(f"🛏 {prop.bedrooms} bedrooms · 🛁 {prop.bathrooms} bathrooms · 🚗 {prop.parking_spaces} parking")
    if prop.size_sqm:
        lines.append(f"📐 {prop.size_sqm} m²")
    if prop.amenity_list:
        lines.append("✅ " + ", ".join(prop.amenity_list[:6]))
    lines += ["", prop.description[:400] + ("…" if len(prop.description) > 400 else ""), "",
              f"📞 Call / WhatsApp {agent}: {contact}", f"🔗 Details & photos: {url}", f"Ref: {prop.reference}",
              "#RwandaRealEstate #Kigali #TheoProperty"]
    ad_text = "\n".join(lines)
    return render(request, "dashboard/properties/share.html", {
        "property": prop, "ad_text": ad_text, "url": url, "short_text": f"{prop.title} – {price}",
    })


@dashboard_required
def property_photos_zip(request, pk):
    """Download all photos of a property in one ZIP (for posting on other platforms)."""
    prop = get_object_or_404(properties_for(request.user), pk=pk)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, img in enumerate(prop.images.all(), start=1):
            with img.image.open("rb") as fh:
                ext = img.image.name.rsplit(".", 1)[-1].lower()
                zf.writestr(f"{prop.reference}-{n:02d}.{ext}", fh.read())
    response = HttpResponse(buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{prop.reference}-photos.zip"'
    log_action(request, "photos_downloaded", prop)
    return response


@dashboard_required
@require_POST
def property_delete(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    if not can_manage_listing(request.user, prop):  # admins: any listing; agents: only their own
        raise PermissionDenied
    log_action(request, "property_deleted", prop)
    for img in prop.images.all():
        try:
            img.image.delete(save=False)  # remove the photo files too (Cloudinary / disk)
        except Exception:  # a missing file or a storage hiccup must not block deleting the listing
            logger.warning("Could not delete photo file %s", img.image.name, exc_info=True)
    prop.delete()
    messages.success(request, "Property deleted.")
    return redirect("dashboard:properties")


@dashboard_required
@require_POST
def image_delete(request, pk):
    img = get_object_or_404(PropertyImage, pk=pk)
    if not can_edit_property(request.user, img.property):
        raise PermissionDenied
    prop = img.property
    try:
        img.image.delete(save=False)
    except Exception:  # file already gone (e.g. lost before Cloudinary was set up): still remove the record
        logger.warning("Could not delete photo file %s", img.image.name, exc_info=True)
    img.delete()
    return redirect("dashboard:property_edit", pk=prop.pk)


@dashboard_required
@require_POST
def image_cover(request, pk):
    img = get_object_or_404(PropertyImage, pk=pk)
    if not can_edit_property(request.user, img.property):
        raise PermissionDenied
    img.property.images.update(is_cover=False)
    img.is_cover = True
    img.save(update_fields=["is_cover"])
    return redirect("dashboard:property_edit", pk=img.property.pk)


@admin_required
def property_review(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    old_status = prop.approval_status
    form = PropertyReviewForm(request.POST or None, instance=prop)
    if request.method == "POST" and form.is_valid():
        prop = form.save()
        if prop.approval_status != old_status:
            log_action(request, f"property_{prop.approval_status}", prop, prop.rejection_reason)
            text = {
                "approved": f"Your property \"{prop.title}\" is now live on Theo Property.",
                "rejected": f"Your property \"{prop.title}\" was rejected. Reason: {prop.rejection_reason or 'n/a'}",
                "inactive": f"Your property \"{prop.title}\" has been deactivated.",
            }.get(prop.approval_status)
            if text:
                _announce_status(prop, text)
        messages.success(request, "Review saved.")
        return redirect("dashboard:properties")
    return render(request, "dashboard/properties/review.html", {"form": form, "property": prop})


def _announce_status(prop, text):
    """Tell the agent (in-app) and the owner (email, no account) about a listing status change."""
    status = prop.get_approval_status_display()
    title = f"Listing update: {prop.title} is {status.lower()}"
    details = [("Property", prop.title), ("Reference", prop.reference), ("Location", prop.location_display),
               ("Status", status), ("Reason", prop.rejection_reason)]
    for person in {prop.owner, prop.agent, prop.created_by} - {None}:
        notify(person, title, text, f"/dashboard/properties/{prop.pk}/edit/", email=True, category="Property update",
               intro=text, details=details, button=("Open the listing", f"/dashboard/properties/{prop.pk}/edit/"))
    if prop.owner_email:
        live = prop.approval_status == Property.Approval.APPROVED
        email_contact(prop.owner_email, title,
                      "Keep your reference number: you can check your listing's status at any time on our website.",
                      name=prop.owner_name, category="Your property", intro=text, details=details,
                      button=("View it on the website", prop.get_absolute_url()) if live
                      else ("Check your listing status", reverse("properties:status")),
                      reason=f"You received this because you listed this property with {settings.SITE_NAME}.")


@dashboard_required
@require_POST
def property_quick_status(request, pk, status):
    """One-click publish / lock (take offline) buttons.
    Publishing: admins, or agents allowed to publish directly. Locking: admins, or the listing's own agent."""
    prop = get_object_or_404(Property, pk=pk)
    if status == Property.Approval.APPROVED:
        allowed = can_publish(request.user, prop)
    elif status == Property.Approval.INACTIVE:
        allowed = can_manage_listing(request.user, prop)
    else:
        allowed = False
    if not allowed:
        raise PermissionDenied
    prop.approval_status = status
    prop.save(update_fields=["approval_status", "updated_at"])
    log_action(request, f"property_{status}", prop)
    verb = "is now live on Theo Property" if status == Property.Approval.APPROVED else "has been deactivated"
    _announce_status(prop, f"Your property \"{prop.title}\" {verb}.")
    messages.success(request, f"{prop.reference} is now {prop.get_approval_status_display()}.")
    return redirect(safe_next_url(request, reverse("dashboard:properties")))


@dashboard_required
@require_POST
def property_availability(request, pk, status):
    """'Mark as sold / rented' (taken: hidden from the website, no new questions, open chats closed)
    or 'Available again'."""
    prop = get_object_or_404(Property, pk=pk)
    if status not in Property.Availability.values or not can_edit_property(request.user, prop):
        raise PermissionDenied
    prop.availability = status
    prop.save(update_fields=["availability", "updated_at"])  # the save signal closes open conversations
    log_action(request, f"property_{status}", prop)
    if prop.is_taken:
        messages.success(request, f"{prop.reference} marked as {prop.get_availability_display().lower()}. It is hidden "
                                  "from the website and clients with open conversations were told.")
    else:
        messages.success(request, f"{prop.reference} is {prop.get_availability_display().lower()} again and back on the website.")
    return redirect(safe_next_url(request, reverse("dashboard:properties")))


@admin_required
@require_POST
def property_hot_deal(request, pk):
    """Admins pick a listing as a hot deal (whatever its age) until a chosen date, or stop it.
    The price is never changed: it stays as set by the agent, owner or admin."""
    prop = get_object_or_404(Property, pk=pk)
    back = safe_next_url(request, reverse("dashboard:property_edit", args=[pk]))
    if request.POST.get("action") == "stop":
        prop.hot_deal_until = None
        prop.save(update_fields=["hot_deal_until", "updated_at"])
        log_action(request, "hot_deal_stopped", prop)
        messages.success(request, f"{prop.reference} is no longer a hot deal.")
        return redirect(back)
    if prop.approval_status != Property.Approval.APPROVED or prop.availability != Property.Availability.AVAILABLE:
        messages.error(request, "Only listings that are published and available can be hot deals.")
        return redirect(back)
    now = timezone.now()
    until = None
    raw_date = request.POST.get("until", "").strip()
    if raw_date:
        try:
            day = forms.DateField().clean(raw_date)
            until = timezone.make_aware(datetime.combine(day, time(23, 59)))  # to the end of that day
        except (ValidationError, ValueError):
            until = None
    else:
        try:
            days = int(request.POST.get("days", ""))
            until = now + timedelta(days=days) if days > 0 else None
        except ValueError:
            until = None
    if not until or until <= now or until > now + timedelta(days=settings.HOT_DEAL_MAX_DAYS):
        messages.error(request, f"Choose an end date between tomorrow and {settings.HOT_DEAL_MAX_DAYS} days from now.")
        return redirect(back)
    prop.hot_deal_until = until
    prop.save(update_fields=["hot_deal_until", "updated_at"])
    log_action(request, "hot_deal_set", prop, f"until {until:%Y-%m-%d %H:%M}")
    if prop.agent and prop.agent != request.user:
        notify(prop.agent, f"{prop.reference} is now a hot deal",
               f"\"{prop.title}\" is shown at the top of the website as a hot deal until {until:%d %b %Y}.",
               f"/dashboard/properties/{prop.pk}/edit/")
    messages.success(request, f"{prop.reference} is a hot deal until {timezone.localtime(until):%d %b %Y %H:%M}. "
                              "It is shown first on the website until then.")
    return redirect(back)


@dashboard_required
@require_POST
def property_repost(request, pk):
    """'Repost': the listing is still on offer, so it comes back to the top as new and the 30-day
    'check this listing' clock starts again. Admins: any listing; agents: their own."""
    prop = get_object_or_404(Property, pk=pk)
    if not can_manage_listing(request.user, prop):
        raise PermissionDenied
    if prop.approval_status != Property.Approval.APPROVED or prop.is_taken:
        messages.error(request, "Only published listings that are still on offer can be reposted.")
        return redirect(safe_next_url(request, reverse("dashboard:properties")))
    prop.listed_at = timezone.now()
    prop.reminder_sent_at = None
    prop.save(update_fields=["listed_at", "reminder_sent_at", "updated_at"])
    log_action(request, "property_reposted", prop)
    messages.success(request, f"{prop.reference} has been reposted: it shows as new on the website again.")
    return redirect(safe_next_url(request, reverse("dashboard:properties")))


# ---------- people: clients & owners (contacts, no accounts) ----------
@dashboard_required
def client_contacts(request):
    """Clients are visitors who sent inquiries or booked visits, grouped by phone/email."""
    q = request.GET.get("q", "").strip()
    inquiries = inquiries_for(request.user)
    visits = appointments_for(request.user)
    if q:
        inquiries = inquiries.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
        visits = visits.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    people = {}
    for kind, rows in (("inquiries", inquiries.values("name", "email", "phone", "created_at")),
                       ("visits", visits.values("name", "email", "phone", "created_at"))):
        for r in rows:
            key = (r["phone"] or "").replace(" ", "")[-9:] or (r["email"] or "").lower()
            if not key:
                continue
            if not key:
                continue
            p = people.setdefault(key, {"name": r["name"], "email": r["email"], "phone": r["phone"],
                                        "inquiries": 0, "visits": 0, "last": r["created_at"]})
            p[kind] += 1
            p["email"] = p["email"] or r["email"]
            p["phone"] = p["phone"] or r["phone"]
            p["last"] = max(p["last"], r["created_at"])
    rows = sorted(people.values(), key=lambda p: p["last"], reverse=True)
    return render(request, "dashboard/contacts/clients.html", {"page_obj": paginate(request, rows, 25), "q": q})


@dashboard_required
def owner_contacts(request):
    """Property owners, grouped from the properties they submitted."""
    q = request.GET.get("q", "").strip()
    qs = properties_for(request.user).exclude(owner_name="")
    if q:
        qs = qs.filter(Q(owner_name__icontains=q) | Q(owner_phone__icontains=q) | Q(owner_email__icontains=q))
    rows = (qs.values("owner_name", "owner_phone", "owner_email")
            .annotate(n=Count("id"), live=Count("id", filter=Q(approval_status=Property.Approval.APPROVED)))
            .order_by("owner_name"))
    return render(request, "dashboard/contacts/owners.html", {"page_obj": paginate(request, rows, 25), "q": q})


# ---------- staff accounts (agents / admins) ----------
def superuser_required(view):
    """Only the super admin: agent applications, creating / promoting / demoting administrators."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


def send_account_ready(request, member, title, intro, initial_password=True):
    """Notification + branded email when an account is created or approved. Never contains the password."""
    login_url = request.build_absolute_uri(reverse("accounts:login"))
    reset_url = request.build_absolute_uri(reverse("accounts:password_reset"))
    body = ("You can sign in with your username or your email address (both are shown below).\n\n"
            f"Forgot your password later? Use \"Forgot password?\" on the sign-in page, or open {reset_url}, "
            "and we'll send a reset link to this email.")
    note = ("Use the initial password the administrator gave you in person or by phone. The first time you sign "
            "in, you'll be asked to choose your own password. Never share your password with anyone."
            if initial_password else "Use the password you chose when you applied. Never share it with anyone.")
    notify(member, title, body, "/dashboard/", email=True, category="Your account", intro=intro,
           details=[("Name", member.get_full_name()), ("Username", member.username), ("Email", member.email),
                    ("Role", "Super admin" if member.is_superuser else member.get_role_display())],
           button=("Sign in to your account", login_url), note=note)


def can_manage_user(actor, member):
    """Who may change whom: never yourself or a super admin; administrators only by the super admin;
    agents and others by any administrator."""
    if member.pk == actor.pk or member.is_superuser:
        return False
    if member.is_admin_role:
        return actor.is_superuser
    return actor.is_admin_role


USER_ACTIONS = ("activate", "deactivate", "promote", "demote")


@admin_required
@require_POST
def user_action(request, pk, action):
    """Quick buttons: activate, deactivate, promote to administrator, make agent (super admin for the last two)."""
    member = get_object_or_404(User, pk=pk)
    if action not in USER_ACTIONS or not can_manage_user(request.user, member):
        raise PermissionDenied
    if action in ("promote", "demote") and not request.user.is_superuser:
        raise PermissionDenied
    name = member.get_full_name() or member.username
    profile = getattr(member, "agent_profile", None) if member.is_agent else None
    if action == "activate":
        if profile and profile.application_status != AgentProfile.Application.APPROVED:
            messages.error(request, "This agent's application hasn't been approved yet. "
                                    "The super admin approves it from Agent applications.")
            return redirect(safe_next_url(request, reverse("dashboard:user_detail", args=[pk])))
        member.is_active = True
        member.save(update_fields=["is_active"])
        notify(member, "Your account is active again",
               "You can sign in again with your username or email and your usual password.", "/dashboard/",
               email=True, category="Your account", intro="An administrator has activated your account.",
               details=[("Username", member.username), ("Email", member.email), ("Status", "Active")],
               button=("Sign in", reverse("accounts:login")))
        text = f"{name} is now active."
    elif action == "deactivate":
        member.is_active = False
        member.save(update_fields=["is_active"])
        notify(member, "Your account has been deactivated",
               "You can no longer sign in to the dashboard. If you think this is a mistake, contact the administrator.",
               email=True, category="Your account", intro="An administrator has deactivated your account.",
               details=[("Username", member.username), ("Status", "Deactivated")])
        text = f"{name} is deactivated and can no longer sign in."
    elif action == "promote":
        member.role = User.Role.ADMIN
        member.save(update_fields=["role"])
        notify(member, "You are now an administrator",
               "You can now review and publish listings, manage agents, see all inquiries and change site settings.",
               "/dashboard/", email=True, category="Your account",
               intro=f"The super admin has made you an administrator of {settings.SITE_NAME}.",
               details=[("Username", member.username), ("New role", "Administrator")],
               button=("Open the dashboard", "/dashboard/"))
        text = f"{name} is now an administrator."
    else:  # demote
        member.role = User.Role.AGENT
        member.save()  # creates the agent profile if missing
        notify(member, "Your role has changed to agent",
               "You keep access to your own listings and client conversations, but no longer to administrator tools.",
               "/dashboard/", email=True, category="Your account",
               intro="The super admin has changed your account from administrator to agent.",
               details=[("Username", member.username), ("New role", "Real Estate Agent")],
               button=("Open the dashboard", "/dashboard/"))
        text = f"{name} is now an agent."
    log_action(request, f"user_{action}", member)
    messages.success(request, text)
    return redirect(safe_next_url(request, reverse("dashboard:user_detail", args=[pk])))


@admin_required
def user_set_password(request, pk):
    """Give someone a new initial password (e.g. they forgot theirs). They must change it at next sign-in."""
    member = get_object_or_404(User, pk=pk)
    if not can_manage_user(request.user, member):
        raise PermissionDenied
    form = InitialPasswordForm(member, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()  # also signs them out everywhere (their old sessions stop working)
        log_action(request, "user_password_reset", member)
        notify(member, "Your password was reset by an administrator",
               "You have been signed out everywhere. Sign in with the new initial password the administrator gives "
               "you, then you'll be asked to choose your own.", email=True, category="Security",
               intro="An administrator has set a new initial password on your account.",
               details=[("Username", member.username), ("Email", member.email)],
               button=("Sign in", reverse("accounts:login")),
               note="The new password is never sent by email: the administrator gives it to you in person or by "
                    "phone. If you didn't expect this, contact the administrator straight away.")
        messages.success(request, f"Initial password set for {member}. Give it to them privately: they must "
                                  "change it the first time they sign in.")
        return redirect("dashboard:user_detail", pk=pk)
    return render(request, "dashboard/users/set_password.html", {"member": member, "form": form})


@superuser_required
def admin_create(request):
    form = AdminCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        admin = form.save()
        log_action(request, "admin_created", admin)
        send_account_ready(request, admin, "Your administrator account is ready",
                           f"The super admin created an administrator account for you on {settings.SITE_NAME}.")
        messages.success(request, f"Administrator {admin} created and emailed at {admin.email}. "
                                  "Give them the initial password privately.")
        return redirect("dashboard:users", role="admin")
    return render(request, "dashboard/users/agent_form.html", {
        "form": form, "page_heading": "Create administrator", "submit_label": "Create administrator account"})


@admin_required
def user_list(request, role):
    if role not in (User.Role.AGENT, User.Role.ADMIN):
        raise PermissionDenied
    qs = User.objects.filter(role=role).order_by("-date_joined")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q)
                       | Q(last_name__icontains=q) | Q(phone__icontains=q))
    qs = qs.annotate(n_agent=Count("agent_properties", distinct=True))
    page = paginate(request, qs)
    for m in page:
        m.can_manage = can_manage_user(request.user, m)
    return render(request, "dashboard/users/list.html", {"page_obj": page,
        "role": role, "role_label": dict(User.Role.choices)[role], "q": q})


@admin_required
def user_detail(request, pk):
    member = get_object_or_404(User, pk=pk)
    can_manage = can_manage_user(request.user, member)
    was_active = member.is_active  # read before the form copies POST data onto `member`
    role_form = UserRoleForm(request.POST or None, instance=member, prefix="role", actor=request.user) if can_manage else None
    agent_form = None
    if member.is_agent and can_manage:
        agent_form = AgentProfileForm(request.POST or None, instance=member.agent_profile, prefix="agent")
    if request.method == "POST" and not can_manage:
        raise PermissionDenied
    if request.method == "POST":
        if role_form.is_valid() and (agent_form is None or agent_form.is_valid()):
            profile = getattr(member, "agent_profile", None) if member.is_agent else None
            unapproved = profile and profile.application_status != AgentProfile.Application.APPROVED
            if member == request.user and not role_form.cleaned_data["is_active"]:
                messages.error(request, "You cannot deactivate your own account.")
            elif unapproved and role_form.cleaned_data["is_active"] and not was_active:
                messages.error(request, "This agent's application hasn't been approved. Only the super admin can "
                                        "approve it from Agent applications.")
            else:
                role_form.save()
                if agent_form:
                    agent_form.save()
                log_action(request, "user_updated", member, f"role={member.role} active={member.is_active}")
                messages.success(request, "User updated.")
            return redirect("dashboard:user_detail", pk=member.pk)
    return render(request, "dashboard/users/detail.html", {
        "member": member, "role_form": role_form, "agent_form": agent_form, "can_manage": can_manage,
        "properties": Property.objects.filter(agent=member)[:10],
        "inquiries": member.assigned_inquiries.select_related("property")[:10],
        "appointments": member.agent_appointments.select_related("property")[:10],
    })


@superuser_required
def agent_applications(request):
    status = request.GET.get("status", AgentProfile.Application.PENDING)
    qs = (AgentProfile.objects.select_related("user", "reviewed_by")
          .exclude(applied_at__isnull=True).order_by("-applied_at"))
    if status in AgentProfile.Application.values:
        qs = qs.filter(application_status=status)
    counts = dict(AgentProfile.objects.exclude(applied_at__isnull=True)
                  .values_list("application_status").annotate(n=Count("id")))
    return render(request, "dashboard/users/agent_applications.html", {
        "page_obj": paginate(request, qs), "status": status, "counts": counts,
        "statuses": AgentProfile.Application.choices, "review_form": AgentReviewForm()})


@superuser_required
@require_POST
def agent_application_decide(request, pk, decision):
    profile = get_object_or_404(AgentProfile.objects.select_related("user"), pk=pk)
    agent = profile.user
    if decision == "approve":
        profile.application_status = AgentProfile.Application.APPROVED
        profile.rejection_reason = ""
        profile.is_verified = True
        agent.is_active = True
    elif decision == "reject":
        form = AgentReviewForm(request.POST)
        form.is_valid()
        profile.application_status = AgentProfile.Application.REJECTED
        profile.rejection_reason = form.cleaned_data.get("rejection_reason", "")
        agent.is_active = False
    else:
        raise PermissionDenied
    profile.reviewed_by = request.user
    profile.reviewed_at = timezone.now()
    profile.save()
    agent.save(update_fields=["is_active"])
    log_action(request, f"agent_application_{profile.application_status}", agent, profile.rejection_reason)
    if decision == "approve":
        # They chose their own password when applying, so it isn't an "initial" one
        send_account_ready(request, agent, "Your agent account is approved",
                           f"Good news: your application to join {settings.SITE_NAME} as an agent has been approved.",
                           initial_password=False)
    else:
        notify(agent, "About your agent application",
               "Thank you for your interest. After review, we can't approve your agent account at this time. "
               "If you have questions, contact us on WhatsApp.",
               email=True, category="Agent application",
               intro=f"Your application to join {settings.SITE_NAME} as an agent was not approved.",
               details=[("Name", agent.get_full_name()), ("Applied on", profile.applied_at and
                                                          timezone.localtime(profile.applied_at).strftime("%d %b %Y"))],
               quote=profile.rejection_reason, quote_label="Reason")
    messages.success(request, f"{agent.get_full_name() or agent.username}: application "
                              f"{profile.get_application_status_display().lower()}.")
    return redirect(safe_next_url(request, reverse("dashboard:agent_applications")))


@admin_required
def agent_create(request):
    form = AgentCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        agent = form.save()
        log_action(request, "agent_created", agent)
        send_account_ready(request, agent, "Your agent account is ready",
                           f"An administrator created an agent account for you on {settings.SITE_NAME}.")
        messages.success(request, f"Agent {agent} registered and emailed at {agent.email}. Give them the initial "
                                  "password privately: they must change it the first time they sign in.")
        return redirect("dashboard:users", role="agent")
    return render(request, "dashboard/users/agent_form.html", {"form": form})


# ---------- inquiries ----------
@dashboard_required
def inquiry_list(request):
    qs = inquiries_for(request.user).prefetch_related("property__images")
    status = request.GET.get("status", "")
    if status:
        qs = qs.filter(status=status)
    return render(request, "dashboard/inquiries/list.html", {
        "page_obj": paginate(request, qs), "status": status, "statuses": Inquiry.Status.choices})


@dashboard_required
def inquiry_detail(request, pk):
    inquiry = get_object_or_404(inquiries_for(request.user), pk=pk)
    can_respond = request.user.is_admin_role or request.user.is_agent
    form = InquiryResponseForm(request.POST or None, instance=inquiry, user=request.user) if can_respond else None
    if request.method == "POST" and form and form.is_valid():
        inquiry = form.save(commit=False)
        reply = form.cleaned_data.get("reply", "").strip()
        if reply:
            InquiryMessage.objects.create(inquiry=inquiry, from_client=False, author=request.user, body=reply)
            inquiry.response = reply  # latest staff reply, kept for reports
            inquiry.responded_at = inquiry.responded_at or timezone.now()
            if "status" not in form.changed_data:
                inquiry.status = Inquiry.Status.RESPONDED
            prop = inquiry.property
            agent_name = request.user.get_full_name() or request.user.username
            email_contact(
                inquiry.email, f"{agent_name} replied about {prop.title}",
                "To answer, open \"Agent replies\" on our website from the same phone or computer you used to send "
                "your message, or chat with the agent on WhatsApp.",
                name=inquiry.name, category="Agent reply",
                intro=f"{agent_name}, the agent for this property, answered your message.",
                quote=reply, quote_label=f"{agent_name} wrote",
                details=[("Property", f"{prop.title} ({prop.reference})"), ("Location", prop.location_display),
                         ("Price", f"{prop.price:,.0f} {prop.currency}" + (f" / {prop.rent_period}" if prop.rent_period else "")),
                         ("Agent", agent_name), ("Agent phone", request.user.phone)],
                button=("Reply on the website", reverse("inquiries:conversations")),
                note="Never pay money before visiting the property.",
                reason=f"You received this because you asked {settings.SITE_NAME} about this property.")
        inquiry.save()
        if inquiry.assigned_agent and "assigned_agent" in form.changed_data:
            notify(inquiry.assigned_agent, "A client conversation was assigned to you",
                   f"Inquiry #{inquiry.pk} from {inquiry.name} about {inquiry.property}.",
                   f"/dashboard/inquiries/{inquiry.pk}/", email=True, category="New inquiry",
                   intro=f"{request.user} assigned a client's conversation to you.",
                   details=[("Property", f"{inquiry.property.title} ({inquiry.property.reference})"),
                            ("Client", inquiry.name), ("Phone", inquiry.phone)],
                   quote=inquiry.message, quote_label=f"{inquiry.name} asked",
                   button=("Open the conversation", f"/dashboard/inquiries/{inquiry.pk}/#reply"))
        log_action(request, "inquiry_updated", inquiry)
        messages.success(request, "Reply sent." if reply else "Inquiry updated.")
        return redirect(reverse("dashboard:inquiry_detail", args=[pk]) + "#reply")
    prop = inquiry.property
    client_whatsapp = whatsapp_link(inquiry.phone, listing_text(
        prop, request.build_absolute_uri(prop.get_absolute_url()),
        f"Hello {inquiry.name}, this is {request.user.get_full_name() or request.user.username} from "
        f"{settings.SITE_NAME}, about your message on this property."))
    return render(request, "dashboard/inquiries/detail.html", {
        "inquiry": inquiry, "form": form, "thread": inquiry.thread.select_related("author"),
        "client_whatsapp": client_whatsapp,
        "retention_days": settings.INQUIRY_RETENTION_DAYS})


# ---------- appointments ----------
@dashboard_required
def appointment_list(request):
    qs = appointments_for(request.user)
    status = request.GET.get("status", "")
    when = request.GET.get("when", "upcoming")
    if status:
        qs = qs.filter(status=status)
    if when == "upcoming":
        qs = qs.filter(scheduled_for__gte=timezone.now())
    elif when == "past":
        qs = qs.filter(scheduled_for__lt=timezone.now()).order_by("-scheduled_for")
    return render(request, "dashboard/appointments/list.html", {
        "page_obj": paginate(request, qs), "status": status, "when": when,
        "statuses": Appointment.Status.choices})


@dashboard_required
def appointment_detail(request, pk):
    appt = get_object_or_404(appointments_for(request.user), pk=pk)
    can_manage = can_manage_appointments(request.user)
    form = AppointmentManageForm(request.POST or None, instance=appt, user=request.user) if can_manage else None
    if request.method == "POST" and form and form.is_valid():
        changed = set(form.changed_data)
        appt = form.save(commit=False)
        if "scheduled_for" in changed and appt.status == Appointment.Status.REQUESTED:
            appt.status = Appointment.Status.RESCHEDULED
        appt.save()
        notified = False
        if changed & {"status", "scheduled_for"} and appt.email:
            when = timezone.localtime(appt.scheduled_for)
            status = appt.get_status_display().lower()
            email_contact(appt.email, f"Your visit to {appt.property.title} is {status}",
                          "If the time doesn't suit you any more, call or WhatsApp the agent to change it.",
                          name=appt.name, category="Property visit",
                          intro=f"Your visit is {status} for {when:%A %d %B %Y at %H:%M}.",
                          details=[("Property", f"{appt.property.title} ({appt.property.reference})"),
                                   ("Address", appt.property.address or appt.property.location_display),
                                   ("Date and time", f"{when:%A %d %B %Y, %H:%M}"), ("Status", appt.get_status_display()),
                                   ("Agent", appt.agent and (appt.agent.get_full_name() or appt.agent.username)),
                                   ("Agent phone", appt.agent and appt.agent.phone)],
                          quote=appt.staff_note, quote_label="Note from the agent",
                          button=("View the property", appt.property.get_absolute_url()),
                          note="Never pay money before you have visited the property and met the agent.")
            notified = True
        log_action(request, "appointment_updated", appt)
        messages.success(request, "Appointment updated." + (" Client emailed." if notified else
                                                            f" Remember to call the client on {appt.phone}."))
        return redirect("dashboard:appointment_detail", pk=pk)
    return render(request, "dashboard/appointments/detail.html", {"appt": appt, "form": form})


# ---------- payments ----------
@admin_required
def payment_list(request):
    qs = Payment.objects.select_related("property")
    status = request.GET.get("status", "")
    method = request.GET.get("method", "")
    if status:
        qs = qs.filter(status=status)
    if method:
        qs = qs.filter(method=method)
    totals = qs.filter(status=Payment.Status.SUCCESSFUL).values("currency").annotate(total=Sum("amount"))
    return render(request, "dashboard/payments/list.html", {
        "page_obj": paginate(request, qs), "status": status, "method": method, "totals": totals,
        "statuses": Payment.Status.choices, "methods": Payment.Method.choices})


@admin_required
def payment_create(request):
    initial = {k: request.GET.get(k, "") for k in ("payer_name", "payer_phone", "payer_email", "property")}
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.recorded_by = request.user
        payment.save()
        log_action(request, "payment_recorded", payment)
        messages.success(request, "Payment recorded as pending. Verify it to mark it successful.")
        return redirect("dashboard:payment_detail", pk=payment.pk)
    return render(request, "dashboard/payments/form.html", {"form": form})


@admin_required
def payment_detail(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("client", "property", "verified_by"), pk=pk)
    gateway = get_gateway(payment.method)
    return render(request, "dashboard/payments/detail.html", {
        "payment": payment, "is_mobile": payment.method in Payment.MOBILE_METHODS,
        "gateway_ready": bool(gateway and gateway.is_configured())})


@admin_required
@require_POST
def payment_action(request, pk, action):
    payment = get_object_or_404(Payment, pk=pk)
    try:
        with transaction.atomic():  # a failed step undoes its own changes, nothing half-saved
            if action == "verify":
                # Manual confirmation of cash / bank payments by staff
                if payment.method in Payment.MOBILE_METHODS:
                    raise ValidationError("Mobile money must be confirmed by the provider (use 'Check status').")
                payment.verified_by = request.user
                payment.status = Payment.Status.SUCCESSFUL
                payment.paid_at = payment.paid_at or timezone.now()
                payment.save()
            elif action == "check":
                get_gateway(payment.method).check_status(payment)
            elif action == "request":
                get_gateway(payment.method).request_payment(payment)
            elif action in ("fail", "cancel"):
                payment.status = Payment.Status.FAILED if action == "fail" else Payment.Status.CANCELLED
                payment.save()
            else:
                raise PermissionDenied
            log_action(request, f"payment_{action}", payment)
            if payment.status == Payment.Status.SUCCESSFUL:
                email_contact(payment.payer_email, f"Payment received: {payment.amount:,.0f} {payment.currency}",
                              "Keep this email as your receipt. Quote the reference if you contact us about it.",
                              name=payment.payer_name, category="Payment",
                              intro="Thank you, we have received your payment.",
                              details=[("Reference", payment.reference),
                                       ("Amount", f"{payment.amount:,.0f} {payment.currency}"),
                                       ("Status", payment.get_status_display())])
            messages.success(request, f"Payment is now {payment.get_status_display()}.")
    except (ValidationError, GatewayNotConfigured, GatewayError, NotImplementedError, AttributeError) as e:
        messages.error(request, getattr(e, "message", None) or str(e) or "Gateway not available.")
    except PermissionDenied:
        raise
    except Exception:
        logger.exception("Payment action %s failed for payment %s", action, pk)
        messages.error(request, "The payment provider could not be reached. Nothing was changed; please try again.")
    return redirect("dashboard:payment_detail", pk=pk)


# ---------- reports ----------
@dashboard_required
def reports(request):
    props = properties_for(request.user)
    by_sector = props.exclude(sector="").values("sector").annotate(n=Count("id")).order_by("-n")[:10]
    by_type = props.values("property_type").annotate(n=Count("id")).order_by("-n")
    by_purpose = props.values("purpose").annotate(n=Count("id"))
    top_viewed = props.filter(approval_status=Property.Approval.APPROVED).order_by("-views")[:10]
    most_inquired = props.annotate(n=Count("inquiries")).filter(n__gt=0).order_by("-n")[:10]
    ctx = {"by_sector": by_sector, "by_type": by_type, "by_purpose": by_purpose,
           "top_viewed": top_viewed, "most_inquired": most_inquired,
           "type_labels": dict(Property.PropertyType.choices),
           "purpose_labels": dict(Property.Purpose.choices)}
    if request.user.is_admin_role:
        ctx["payments_by_method"] = (Payment.objects.filter(status=Payment.Status.SUCCESSFUL)
                                     .values("method", "currency").annotate(total=Sum("amount"), n=Count("id")))
        ctx["method_labels"] = dict(Payment.Method.choices)
        ctx["agent_performance"] = (User.objects.filter(role=User.Role.AGENT)
                                    .annotate(listings=Count("agent_properties", distinct=True),
                                              inq=Count("assigned_inquiries", distinct=True),
                                              visits=Count("agent_appointments", distinct=True))
                                    .order_by("-listings")[:10])
    return render(request, "dashboard/reports.html", ctx)


@dashboard_required
def export_csv(request, kind):
    user = request.user
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="theo_{kind}_{timezone.now():%Y%m%d}.csv"'
    w = SafeCsvWriter(csv.writer(response))
    if kind == "properties":
        w.writerow(["Reference", "Title", "Type", "Purpose", "Price", "Currency", "District", "Sector",
                    "Bedrooms", "Bathrooms", "Size m2", "Availability", "Approval", "Owner", "Owner phone",
                    "Source", "Agent", "Views", "Created"])
        for p in properties_for(user):
            w.writerow([p.reference, p.title, p.get_property_type_display(), p.get_purpose_display(), p.price,
                        p.currency, p.district, p.sector, p.bedrooms, p.bathrooms, p.size_sqm,
                        p.get_availability_display(), p.get_approval_status_display(), p.owner_display,
                        p.owner_phone, p.get_source_display(), p.agent or "", p.views, f"{p.created_at:%Y-%m-%d}"])
    elif kind == "inquiries":
        w.writerow(["ID", "Property", "Name", "Email", "Phone", "Status", "Agent", "Date"])
        for i in inquiries_for(user):
            w.writerow([i.pk, i.property.reference, i.name, i.email, i.phone, i.get_status_display(),
                        i.assigned_agent or "", f"{i.created_at:%Y-%m-%d}"])
    elif kind == "appointments":
        w.writerow(["ID", "Property", "Client", "Phone", "Email", "Agent", "Scheduled", "Status"])
        for a in appointments_for(user):
            w.writerow([a.pk, a.property.reference, a.name, a.phone, a.email, a.agent or "",
                        f"{timezone.localtime(a.scheduled_for):%Y-%m-%d %H:%M}", a.get_status_display()])
    elif kind == "payments" and user.is_admin_role:
        w.writerow(["Reference", "Payer", "Phone", "Property", "Amount", "Currency", "Method", "Purpose", "Status",
                    "Transaction ID", "Paid at"])
        for p in Payment.objects.select_related("property"):
            w.writerow([p.reference, p.payer_name, p.payer_phone, p.property.reference if p.property else "",
                        p.amount, p.currency,
                        p.get_method_display(), p.get_purpose_display(), p.get_status_display(),
                        p.provider_transaction_id, f"{p.paid_at:%Y-%m-%d}" if p.paid_at else ""])
    else:
        raise PermissionDenied
    log_action(request, "report_exported", description=kind)
    return response


# ---------- settings, messages, audit ----------
class SiteSettingForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = SiteSetting
        fields = "__all__"
        widgets = {"about": forms.Textarea(attrs={"rows": 3})}


@admin_required
def site_settings(request):
    settings_obj = SiteSetting.load()
    form = SiteSettingForm(request.POST or None, instance=settings_obj, prefix="site")
    cat_form = CategoryForm(prefix="cat")
    if request.method == "POST":
        if "save_category" in request.POST:
            cat_form = CategoryForm(request.POST, prefix="cat")
            if cat_form.is_valid():
                cat = cat_form.save()
                log_action(request, "category_created", cat)
                messages.success(request, "Category added.")
                return redirect("dashboard:settings")
        elif form.is_valid():
            form.save()
            log_action(request, "settings_updated", settings_obj)
            messages.success(request, "Settings saved.")
            return redirect("dashboard:settings")
    categories = Category.objects.annotate(n=Count("properties"))
    return render(request, "dashboard/settings.html", {
        "form": form, "cat_form": cat_form, "categories": categories,
        "email_test_mode": "console" in settings.EMAIL_BACKEND, "email_from": settings.DEFAULT_FROM_EMAIL})


@admin_required
@require_POST
def send_test_email(request):
    """Lets the admin confirm that support emails really arrive."""
    to = SiteSetting.load().email
    if "console" in settings.EMAIL_BACKEND:
        messages.warning(request, "Email is in test mode: messages are printed in the server terminal, not sent. "
                                  "Add the Gmail App Password as EMAIL_HOST_PASSWORD in .env and restart the server.")
        return redirect("dashboard:settings")
    try:
        sent = send_branded(to, "Test email", category="Test", fail_silently=False,
                            intro="Good news: your website can send emails.",
                            body="This is how emails from your website look. Clients, owners, agents and "
                                 "administrators receive messages in this layout.",
                            details=[("Sent by", str(request.user)), ("Sent to", to),
                                     ("From address", settings.DEFAULT_FROM_EMAIL)],
                            button_label="Open the dashboard", button_url="/dashboard/")
        if sent:
            messages.success(request, f"Test email sent to {to}. Check the inbox (and the Spam folder).")
        else:
            messages.error(request, "The email server did not accept the message.")
    except Exception as e:  # show the real reason (wrong password, blocked port, ...)
        messages.error(request, f"Could not send email: {e}")
    log_action(request, "test_email_sent", description=to)
    return redirect("dashboard:settings")


@admin_required
@require_POST
def category_delete(request, pk):
    cat = get_object_or_404(Category, pk=pk)
    log_action(request, "category_deleted", cat)
    cat.delete()
    messages.success(request, "Category removed.")
    return redirect("dashboard:settings")


@admin_required
def contact_messages(request):
    if request.method == "POST":
        ContactMessage.objects.filter(pk=request.POST.get("pk")).update(is_handled=True)
        return redirect("dashboard:messages")
    return render(request, "dashboard/messages.html", {"page_obj": paginate(request, ContactMessage.objects.all())})


@admin_required
def audit_logs(request):
    return render(request, "dashboard/audit.html",
                  {"page_obj": paginate(request, AuditLog.objects.select_related("user"), 30)})
