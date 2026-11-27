from django.conf import settings
from django.utils.functional import SimpleLazyObject

from favorites.session import favorite_ids
from inquiries.views import unread_count

from .models import SiteSetting

_STATIC_FILES = [settings.BASE_DIR / "static" / "css" / "style.css", settings.BASE_DIR / "static" / "js" / "main.js"]


def static_version():
    """Changes whenever our CSS/JS changes, so browsers never use an outdated cached copy."""
    try:
        return str(int(max(f.stat().st_mtime for f in _STATIC_FILES)))
    except OSError:
        return "1"


def site_context(request):
    ctx = {
        "site": SiteSetting.load(),
        "GOOGLE_MAPS_API_KEY": settings.GOOGLE_MAPS_API_KEY,
        "unread_notifications": 0,
        "fav_ids": favorite_ids(request),
        "STATIC_VERSION": static_version(),
        "google_login_enabled": settings.GOOGLE_LOGIN_ENABLED,
        # Badge on the floating "Agent replies" button (only queried when a public page shows it)
        "agent_unread": SimpleLazyObject(lambda: unread_count(request)) if hasattr(request, "session") else 0,
    }
    if request.user.is_authenticated:
        ctx["unread_notifications"] = request.user.notifications.filter(is_read=False).count()
        if request.user.is_superuser:
            from accounts.models import AgentProfile
            ctx["pending_agent_applications"] = AgentProfile.objects.filter(
                application_status=AgentProfile.Application.PENDING).count()
    return ctx
