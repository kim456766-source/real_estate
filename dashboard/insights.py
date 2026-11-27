"""Dashboard: Active users, Analytics, Documents, Help and problem reports (+ the public 'Report a problem')."""
import os
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.forms import BootstrapFormMixin
from accounts.models import User
from appointments.models import Appointment
from core.analytics import ACTIVE_MINUTES, active_since
from core.models import SiteVisit
from core.security import client_ip, hit_rate_limit, safe_next_url
from core.utils import log_action
from inquiries.device import describe
from inquiries.models import Inquiry
from notifications.services import email_support, notify_admins
from properties.models import Property

from .models import Document, ProblemReport
from .scoping import properties_for
from .views import admin_required, dashboard_required, paginate


# ---------- Active users ----------
@admin_required
def active_users(request):
    """Who is on the website right now (last ACTIVE_MINUTES minutes): staff by name, visitors by device
    and page. Plus when each admin / agent was last seen."""
    since = active_since()
    online = SiteVisit.objects.filter(last_seen__gte=since).select_related("user").order_by("-last_seen")
    staff_online = [v for v in online if v.user and v.user.uses_dashboard]
    visitors_online = [v for v in online if not (v.user and v.user.uses_dashboard)]
    # A visitor who asked an agent about a property is known by name (same device key as their message)
    known = {}
    for inq in (Inquiry.objects.filter(device_key__in=[v.visitor_key for v in visitors_online])
                .exclude(device_key="").select_related("property").order_by("created_at")):
        known[inq.device_key] = inq
    for v in visitors_online:
        v.client = known.get(v.visitor_key)
    staff = (User.objects.filter(Q(role__in=[User.Role.ADMIN, User.Role.AGENT]) | Q(is_superuser=True), is_active=True)
             .annotate(seen=Max("site_visits__last_seen")).order_by("-seen", "first_name"))
    online_ids = {v.user_id for v in staff_online}
    today = SiteVisit.objects.filter(day=timezone.localdate())
    return render(request, "dashboard/insights/active_users.html", {
        "staff_online": staff_online, "visitors_online": visitors_online, "staff": staff, "online_ids": online_ids,
        "minutes": ACTIVE_MINUTES,
        "today_visitors": today.exclude(user__role__in=[User.Role.ADMIN, User.Role.AGENT]).count(),
        "today_opens": today.exclude(user__role__in=[User.Role.ADMIN, User.Role.AGENT]).aggregate(s=Sum("opens"))["s"] or 0,
        "today_asked": Inquiry.objects.filter(created_at__date=timezone.localdate()).count(),
        "today_views": today.aggregate(s=Sum("hits"))["s"] or 0,
    })


# ---------- Analytics ----------
@admin_required
def analytics(request):
    days = 30 if request.GET.get("days") not in ("7", "90") else int(request.GET["days"])
    start = timezone.localdate() - timedelta(days=days - 1)
    visits = SiteVisit.objects.filter(day__gte=start).exclude(user__role__in=[User.Role.ADMIN, User.Role.AGENT])
    since = timezone.now() - timedelta(days=days)
    per_day = {r["day"]: r for r in visits.values("day").annotate(visitors=Count("id"), views=Sum("hits"),
                                                                    opens=Sum("opens"))}
    inq_day = {r["d"]: r["n"] for r in Inquiry.objects.filter(created_at__gte=since)
               .annotate(d=TruncDate("created_at")).values("d").annotate(n=Count("id"))}
    labels, visitors, views, inquiries, opens = [], [], [], [], []
    for i in range(days):
        d = start + timedelta(days=i)
        labels.append(d.strftime("%d %b"))
        visitors.append(per_day.get(d, {}).get("visitors", 0))
        opens.append(per_day.get(d, {}).get("opens", 0) or 0)
        views.append(per_day.get(d, {}).get("views", 0) or 0)
        inquiries.append(inq_day.get(d, 0))
    devices = {}
    for v in visits.only("device_info"):
        devices[v.device_kind] = devices.get(v.device_kind, 0) + 1
    total_visitors = sum(visitors)
    asked = (Inquiry.objects.filter(created_at__gte=since).select_related("property", "assigned_agent")
             .order_by("-created_at"))
    total_inquiries = Inquiry.objects.filter(created_at__gte=since).count()
    landing = (visits.exclude(first_path="").values("first_path").annotate(n=Count("id")).order_by("-n")[:10])
    public = Property.objects.filter(approval_status=Property.Approval.APPROVED)
    return render(request, "dashboard/insights/analytics.html", {
        "days": days,
        "totals": {
            "visitors": total_visitors, "views": sum(views), "inquiries": total_inquiries, "opens": sum(opens),
            "clients": asked.values("phone", "email", "device_key", "name").distinct().count(),
            "visits": Appointment.objects.filter(created_at__gte=since).count(),
            "conversion": round(total_inquiries * 100 / total_visitors, 1) if total_visitors else 0,
            "pages_per_visitor": round(sum(views) / total_visitors, 1) if total_visitors else 0,
            "new_listings": Property.objects.filter(created_at__gte=since).count(),
        },
        "asked": asked[:50],
        "chart": {"labels": labels, "visitors": visitors, "views": views, "inquiries": inquiries, "opens": opens,
                  "device_labels": list(devices), "device_values": list(devices.values())},
        "landing": landing,
        "top_viewed": public.order_by("-views")[:8],
        "top_inquired": public.annotate(n=Count("inquiries", filter=Q(inquiries__created_at__gte=since)))
                              .filter(n__gt=0).order_by("-n")[:8],
    })


# ---------- Documents ----------
class DocumentForm(BootstrapFormMixin, forms.ModelForm):
    upload = forms.FileField(label="File (PDF, Word, Excel or photo, up to 10 MB)")

    class Meta:
        model = Document
        fields = ["upload", "title", "category", "property", "visibility", "notes"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["property"].queryset = properties_for(user).order_by("-created_at")
        self.fields["property"].label_from_instance = lambda p: f"{p.reference} · {p.title}"
        self.fields["title"].required = False
        self.fields["title"].help_text = "Leave empty to use the file name."
        if not user.is_admin_role:  # agents: their own listings' documents, visible to admins and agents
            self.fields["visibility"].choices = [(Document.Visibility.STAFF, "Admins and agents")]

    def clean_upload(self):
        f = self.cleaned_data["upload"]
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in Document.ALLOWED:
            raise forms.ValidationError("This type of file is not accepted. Use PDF, Word, Excel, a photo, TXT or CSV.")
        if f.size > Document.MAX_BYTES:
            raise forms.ValidationError("The file is larger than 10 MB. Please compress it or split it.")
        return f

    def clean(self):
        data = super().clean()
        if not self.user.is_admin_role and not data.get("property"):
            self.add_error("property", "Choose the listing this document belongs to.")
        return data

    def save(self, commit=True):
        doc = super().save(commit=False)
        f = self.cleaned_data["upload"]
        doc.file_name = os.path.basename(f.name)[:200]
        doc.content_type = Document.ALLOWED[os.path.splitext(f.name)[1].lower()]
        doc.size = f.size
        doc.data = f.read()
        doc.title = doc.title or os.path.splitext(doc.file_name)[0][:150]
        doc.uploaded_by = self.user
        if commit:
            doc.save()
        return doc


def documents_for(user):
    qs = Document.objects.select_related("property", "uploaded_by").defer("data")
    if user.is_admin_role:
        return qs
    if user.is_agent:
        return qs.filter(Q(property__agent=user) | Q(property__isnull=True), visibility=Document.Visibility.STAFF)
    return qs.none()


@dashboard_required
def documents(request):
    if not (request.user.is_admin_role or request.user.is_agent):
        raise PermissionDenied
    qs = documents_for(request.user)
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(file_name__icontains=q) | Q(notes__icontains=q)
                       | Q(property__reference__icontains=q) | Q(property__title__icontains=q))
    if category:
        qs = qs.filter(category=category)
    form = DocumentForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            doc = form.save()
            log_action(request, "document_uploaded", doc.property, doc.title)
            messages.success(request, f"\"{doc.title}\" was uploaded.")
            return redirect("dashboard:documents")
        messages.error(request, "The document was not uploaded. Check the form below.")
    return render(request, "dashboard/insights/documents.html", {
        "page_obj": paginate(request, qs, 20), "form": form, "q": q, "category": category,
        "categories": Document.Category.choices})


@dashboard_required
def document_download(request, pk):
    doc = get_object_or_404(documents_for(request.user).defer(None), pk=pk)
    log_action(request, "document_opened", doc.property, doc.title)
    response = HttpResponse(bytes(doc.data), content_type=doc.content_type)
    inline = doc.content_type == "application/pdf" or doc.content_type.startswith("image/")
    safe_name = doc.file_name.replace('"', "")
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{safe_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@dashboard_required
@require_POST
def document_delete(request, pk):
    doc = get_object_or_404(documents_for(request.user), pk=pk)
    if not (request.user.is_admin_role or doc.uploaded_by_id == request.user.pk):
        raise PermissionDenied
    log_action(request, "document_deleted", doc.property, doc.title)
    doc.delete()
    messages.success(request, "Document deleted.")
    return redirect("dashboard:documents")


# ---------- Report a problem (public + staff) and the admin inbox ----------
class ProblemReportForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProblemReport
        fields = ["kind", "message", "name", "contact"]
        labels = {"kind": "What is the problem?", "message": "Tell us more",
                  "name": "Your name (optional)", "contact": "Phone or email (optional, if you want an answer)"}
        widgets = {"message": forms.Textarea(attrs={"rows": 4})}


def report_problem(request):
    """Anyone can report a fake listing, wrong information, an agent or a website problem."""
    prop = None
    ref = request.GET.get("property") or request.POST.get("property") or ""
    if ref:
        prop = Property.objects.filter(reference__iexact=ref.strip(), approval_status=Property.Approval.APPROVED).first()
    initial = {"kind": ProblemReport.Kind.SCAM if prop else ProblemReport.Kind.WEBSITE}
    if request.user.is_authenticated:
        initial.update(name=request.user.get_full_name(), contact=request.user.email)
    form = ProblemReportForm(request.POST or None, initial=initial)
    if not prop:  # "wrong information" / "already sold" only make sense about a listing
        form.fields["kind"].choices = [c for c in ProblemReport.Kind.choices
                                       if c[0] not in (ProblemReport.Kind.TAKEN, ProblemReport.Kind.WRONG)]
    if request.method == "POST":
        if hit_rate_limit(request, "problem_report", 5):
            messages.error(request, "You have sent several reports already. Please try again later or contact us on WhatsApp.")
        elif form.is_valid():
            report = form.save(commit=False)
            report.property = prop
            report.reporter = request.user if request.user.is_authenticated else None
            report.page = (request.POST.get("page") or request.META.get("HTTP_REFERER", ""))[:200]
            report.device_info = describe(request)
            report.ip = client_ip(request)
            report.save()
            what = f"{report.get_kind_display()}" + (f" on {prop.reference} – {prop.title}" if prop else "")
            notify_admins("New problem report", f"{what}: \"{report.message[:200]}\"", "/dashboard/problem-reports/")
            email_support(f"Problem report: {report.get_kind_display()}", f"Someone reported: {what}.",
                          link=request.build_absolute_uri(reverse("dashboard:problem_reports")),
                          details=[("Reported by", report.name or "Anonymous"), ("Contact", report.contact),
                                   ("Page", report.page)],
                          quote=report.message, quote_label="They wrote", category="Problem report")
            messages.success(request, "Thank you. Your report was sent to our administrators, who will look into it.")
            if request.user.is_authenticated and request.user.uses_dashboard:
                return redirect("dashboard:help")
            return redirect(prop.get_absolute_url() if prop else reverse("pages:home"))
    return render(request, "pages/report_problem.html", {"form": form, "property": prop})


@admin_required
def problem_reports(request):
    status = request.GET.get("status", "open")
    qs = ProblemReport.objects.select_related("property", "reporter", "handled_by")
    if status in ProblemReport.Status.values:
        qs = qs.filter(status=status)
    kind = request.GET.get("kind", "")
    if kind:
        qs = qs.filter(kind=kind)
    counts = dict(ProblemReport.objects.values_list("status").annotate(n=Count("id")))
    return render(request, "dashboard/insights/problem_reports.html", {
        "page_obj": paginate(request, qs, 20), "status": status, "kind": kind,
        "statuses": ProblemReport.Status.choices, "kinds": ProblemReport.Kind.choices, "counts": counts})


@admin_required
@require_POST
def problem_report_action(request, pk, action):
    report = get_object_or_404(ProblemReport, pk=pk)
    new = {"resolve": ProblemReport.Status.RESOLVED, "dismiss": ProblemReport.Status.DISMISSED,
           "reopen": ProblemReport.Status.OPEN}.get(action)
    if not new:
        raise PermissionDenied
    report.status = new
    report.admin_note = request.POST.get("note", report.admin_note)[:300]
    report.handled_by = request.user
    report.handled_at = timezone.now()
    report.save()
    log_action(request, f"problem_report_{action}", report.property, report.get_kind_display())
    messages.success(request, f"Report marked as {report.get_status_display().lower()}.")
    return redirect(safe_next_url(request, reverse("dashboard:problem_reports")))


# ---------- Help ----------
@dashboard_required
def help_page(request):
    return render(request, "dashboard/insights/help.html", {
        "report_form": ProblemReportForm(initial={"kind": ProblemReport.Kind.WEBSITE,
                                                  "name": request.user.get_full_name(), "contact": request.user.email}),
        "check_days": settings.LISTING_CHECK_DAYS, "retention_days": settings.INQUIRY_RETENTION_DAYS})
