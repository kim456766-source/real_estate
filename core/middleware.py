import logging

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.cache import add_never_cache_headers, patch_cache_control

PRIVATE_PREFIXES = ("/dashboard/", "/accounts/", "/auth/", "/notifications/", "/admin/", "/properties/submit/")


class NoCachePrivatePagesMiddleware:
    """Private pages are never stored by the browser.

    After logging out (or on a shared computer) pressing the browser's Back button cannot show
    dashboard pages, client details or forms again: the browser has to ask the server, which
    sends the visitor to the login page.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if request.path.startswith(PRIVATE_PREFIXES) or (user is not None and user.is_authenticated):
            add_never_cache_headers(response)
            patch_cache_control(response, private=True, no_store=True)
            response.headers["Pragma"] = "no-cache"
        return response


class HousekeepingMiddleware:
    """Daily clean-up jobs that need no server scheduler: the first request of the day runs them
    (old conversations are deleted after a month, see inquiries/cleanup.py; month-old listings are
    flagged to their agent, see properties/reminders.py). Never breaks a page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            from inquiries.cleanup import purge_if_due
            from properties.reminders import remind_if_due
            purge_if_due()
            remind_if_due()  # listings online for a month: remind the agent / admins to check them
        except Exception:  # housekeeping must never affect the visitor
            logging.getLogger("theo.housekeeping").exception("Daily clean-up failed")
        return response


class ForcePasswordChangeMiddleware:
    """Someone signed in with an initial password (given by an administrator) must choose their own
    before doing anything else. They prove they hold the initial one by typing it on the change form."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and getattr(user, "must_change_password", False):
            allowed = (reverse("accounts:password_change"), reverse("accounts:logout"),
                       "/" + settings.STATIC_URL.lstrip("/"), "/" + settings.MEDIA_URL.lstrip("/"))
            if not request.path.startswith(allowed):
                return redirect("accounts:password_change")  # the page explains why
        return self.get_response(request)
