import logging

from django.shortcuts import render

from .security import client_ip

logger = logging.getLogger("theo.security")


def csrf_failure(request, reason=""):
    """Friendly page when a form's security token is missing/expired (instead of Django's technical page)."""
    logger.warning("CSRF failure from %s on %s: %s", client_ip(request), request.path, reason)
    return render(request, "403_csrf.html", status=403)


def healthz(request):
    """Tiny page for uptime monitors (e.g. UptimeRobot every 5 minutes). Being visited regularly keeps the
    free Render server awake, so real visitors never wait ~30 s for it to start. It also keeps the database
    connection warm. Shows nothing private."""
    from django.db import connection
    from django.http import HttpResponse
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        logging.getLogger("theo.health").exception("Health check: database not reachable")
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    response = HttpResponse("ok", content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response
