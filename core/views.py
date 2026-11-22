import logging

from django.shortcuts import render

from .security import client_ip

logger = logging.getLogger("theo.security")


def csrf_failure(request, reason=""):
    """Friendly page when a form's security token is missing/expired (instead of Django's technical page)."""
    logger.warning("CSRF failure from %s on %s: %s", client_ip(request), request.path, reason)
    return render(request, "403_csrf.html", status=403)
