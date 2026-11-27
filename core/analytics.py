"""Count website visits for "Active users" and "Analytics" (see core.models.SiteVisit)."""
import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

log = logging.getLogger("theo.analytics")

ACTIVE_MINUTES = 5          # "online now" = seen in the last 5 minutes
SESSION_GAP_MINUTES = 30    # coming back after 30 minutes away counts as opening the site again
VISIT_RETENTION_DAYS = 180
SKIP_PREFIXES = ("/static/", "/media/", "/healthz/", "/favicon", "/robots.txt", "/admin/jsi18n/",
                 "/inquiries/chat/", "/properties/search/suggest/", "/api/")
BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "uptime", "monitor", "curl", "wget", "python-requests",
               "headless", "preview", "facebookexternalhit", "whatsapp/", "pingdom", "httpclient")


def is_bot(request):
    ua = request.META.get("HTTP_USER_AGENT", "").lower()
    return not ua or any(m in ua for m in BOT_MARKERS)


def visitor_key(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    from inquiries.device import current_key
    from core.security import client_ip
    return current_key(request) or connection_key(request)


def connection_key(request):
    """Anonymous visitor without a device key: one-way hash of their connection and browser."""
    from core.security import client_ip
    raw = f"{settings.SECRET_KEY}:{client_ip(request)}:{request.META.get('HTTP_USER_AGENT', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def should_count(request, response):
    if request.method != "GET" or response.status_code != 200:
        return False
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return False
    if not response.get("Content-Type", "").startswith("text/html"):
        return False
    return not request.path.startswith(SKIP_PREFIXES) and not is_bot(request)


def record_visit(request):
    from core.models import SiteVisit
    from core.security import client_ip
    from inquiries.device import describe

    now = timezone.now()
    day = timezone.localdate()
    key = visitor_key(request)
    path = request.path[:200]
    user = request.user if getattr(request, "user", None) is not None and request.user.is_authenticated else None
    rows = SiteVisit.objects.filter(day=day, visitor_key=key)
    if user is None and not rows.exists():
        # Same person who just got a device key (they sent a message): keep counting them as one visitor
        try:
            with transaction.atomic():
                SiteVisit.objects.filter(day=day, visitor_key=connection_key(request)).update(visitor_key=key)
        except IntegrityError:
            pass
    # Back after a while: the site was opened again
    updated = rows.filter(last_seen__lt=now - timedelta(minutes=SESSION_GAP_MINUTES)).update(
        opens=F("opens") + 1, hits=F("hits") + 1, last_seen=now, last_path=path, user=user)
    if not updated:
        updated = rows.update(hits=F("hits") + 1, last_seen=now, last_path=path, user=user)
    if not updated:
        try:
            with transaction.atomic():
                SiteVisit.objects.create(day=day, visitor_key=key, user=user, first_seen=now, last_seen=now,
                                         first_path=path, last_path=path, device_info=describe(request),
                                         ip=client_ip(request))
        except IntegrityError:  # two pages opened at the same moment: the other one created it
            SiteVisit.objects.filter(day=day, visitor_key=key).update(hits=F("hits") + 1, last_seen=now,
                                                                      last_path=path)


def active_since():
    return timezone.now() - timedelta(minutes=ACTIVE_MINUTES)


def purge_old_visits():
    from core.models import SiteVisit
    SiteVisit.objects.filter(day__lt=timezone.localdate() - timedelta(days=VISIT_RETENTION_DAYS)).delete()
