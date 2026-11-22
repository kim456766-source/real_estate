"""Sign-in alerts: both parties are told about every sign-in, successful or refused.

  * The administrator (settings.LOGIN_ALERT_EMAIL) gets an email for EVERY attempt on the site.
  * The account owner gets an email at their own address ("was this you?"), so they see it on
    their phone even when someone else tried their account.

Works for every way in: the staff login page, the /admin login and "Continue with Google"
(Django sends `user_logged_in` / `user_login_failed` for all of them; the Google flow reports its
refusals through `google_failed()`).

Safety:
  * Passwords are never included (Django already masks them in the signal).
  * Refused attempts on an UNKNOWN username only alert the administrator: we never email an
    address just because someone typed it.
  * Refusal emails are capped per account and per connection, so nobody can flood the inboxes.
  * Emails are sent in the background so signing in never waits for the mail server.
"""
import hashlib
import logging
import threading

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone

from core.security import client_ip

from .models import AgentProfile, User

log = logging.getLogger("theo.security")

FAILURE_ALERTS_PER_ACCOUNT = 5   # refusal emails per account ...
FAILURE_ALERTS_PER_IP = 10       # ... and per connection ...
FAILURE_ALERT_WINDOW = 60 * 60   # ... per hour


# ---------------------------------------------------------------- helpers

def _device(request):
    """'Chrome on Windows' style summary of the browser, plus the IP."""
    ua = (request.META.get("HTTP_USER_AGENT", "") if request else "")[:400]
    low = ua.lower()
    browser = next((name for key, name in (("edg/", "Edge"), ("opr/", "Opera"), ("samsungbrowser", "Samsung Internet"),
                                           ("chrome/", "Chrome"), ("firefox/", "Firefox"), ("safari/", "Safari"))
                    if key in low), "Unknown browser")
    system = next((name for key, name in (("android", "Android"), ("iphone", "iPhone"), ("ipad", "iPad"),
                                          ("windows", "Windows"), ("mac os", "macOS"), ("linux", "Linux"))
                   if key in low), "unknown system")
    return f"{browser} on {system}", (client_ip(request) if request else "unknown")


def _when():
    return timezone.localtime().strftime("%d %b %Y, %H:%M:%S %Z").strip()


def _method(request):
    path = getattr(request, "path", "") or ""
    if "google" in path:
        return "Continue with Google"
    if path.startswith("/admin"):
        return "Admin site (username / email + password)"
    return "Username / email + password"


def _role(user):
    if user.is_superuser:
        return "Super administrator"
    return user.get_role_display() if hasattr(user, "get_role_display") else ""


def _find_account(login):
    """The account a typed username/email belongs to (same rules as the login backend), or None."""
    login = (login or "").strip()
    if not login:
        return None
    if "@" in login:
        matches = list(User.objects.filter(email__iexact=login)[:2])
        if len(matches) == 1:
            return matches[0]
    return User.objects.filter(username__iexact=login).first()


def _refusal_reason(user, typed_password):
    """Why a known account was refused (only says 'wrong password' if the password was wrong)."""
    if typed_password and user.check_password(typed_password):
        profile = getattr(user, "agent_profile", None)
        if profile and profile.application_status == AgentProfile.Application.PENDING:
            return "Correct password, but the agent application is still waiting for approval"
        if profile and profile.application_status == AgentProfile.Application.REJECTED:
            return "Correct password, but the agent application was rejected"
        if not user.is_active:
            return "Correct password, but the account is deactivated"
        return "Sign-in refused"
    return "Wrong password"


def _under_cap(scope, key, limit):
    # Hash: the key can contain whatever the visitor typed (spaces, brackets...), which some caches refuse
    cache_key = f"login-alert:{scope}:{hashlib.sha256(str(key).encode()).hexdigest()}"
    try:
        cache.add(cache_key, 0, FAILURE_ALERT_WINDOW)
        return cache.incr(cache_key) <= limit
    except Exception:  # cache trouble: alert anyway (rare, and better than silence)
        return True


def _admin_address():
    return (getattr(settings, "LOGIN_ALERT_EMAIL", "") or "").strip()


def _same(a, b):
    return bool(a and b and a.strip().lower() == b.strip().lower())


def _run(job):
    """Send in the background (or right away when LOGIN_ALERTS_ASYNC is off, e.g. in tests)."""
    def wrapped():
        try:
            job()
        except Exception:
            log.exception("Sign-in alert could not be sent")
        finally:
            close_old_connections()

    if getattr(settings, "LOGIN_ALERTS_ASYNC", True):
        threading.Thread(target=wrapped, daemon=True).start()
    else:
        wrapped()


def _send(to, subject, **ctx):
    from notifications.emails import send_branded
    send_branded(to, subject, **ctx)


# ---------------------------------------------------------------- the two events

def alert_success(request, user):
    if not getattr(settings, "LOGIN_ALERTS_ENABLED", True):
        return
    device, ip = _device(request)
    when, method = _when(), _method(request)
    details = [("Account", user.get_full_name() or user.username), ("Username", user.username),
               ("Email", user.email), ("Role", _role(user)), ("Method", method), ("Time", when),
               ("Device", device), ("IP address", ip)]
    reset_url = reverse("accounts:password_reset")
    admin_to = _admin_address()

    # In-website notification for the account owner (shows in their bell)
    from notifications.models import Notification
    Notification.objects.create(user=user, title="New sign-in to your account",
                                message=f"{method} · {device} · {ip} · {when}", link="/accounts/profile/")

    def job():
        if user.email:
            _send(user.email, "New sign-in to your account", category="Security",
                  greeting_name=user.first_name or user.username,
                  intro="Your account was just signed in to.",
                  body="If this was you, you don't need to do anything.",
                  details=details, button_label="Wasn't me: reset my password", button_url=reset_url,
                  note="If you don't recognise this sign-in, reset your password now and tell the administrator.",
                  reason=f"Sent to every {settings.SITE_NAME} account holder each time their account is signed in to.")
        if admin_to and not _same(admin_to, user.email):
            _send(admin_to, f"Sign-in: {user.get_full_name() or user.username}", category="Security",
                  intro=f"{user.get_full_name() or user.username} signed in successfully.",
                  body="The account owner was also emailed about this sign-in.",
                  details=details, button_label="Open dashboard", button_url="/dashboard/",
                  reason="Sent to the administrator for every sign-in on the website.")

    log.info("Sign-in OK: %s from %s (%s)", user.username, ip, device)
    _run(job)


def alert_failure(request, typed_login, typed_password="", reason=None, account=None, lockout=False):
    """A refused sign-in. `account` may be given directly (Google); otherwise it is looked up.
    `lockout=True`: the account was just blocked; that notice gets its own budget (once per hour) so it
    still arrives after the ordinary refusal emails are used up."""
    if not getattr(settings, "LOGIN_ALERTS_ENABLED", True):
        return
    device, ip = _device(request)
    user = account or _find_account(typed_login)
    if reason is None:
        reason = _refusal_reason(user, typed_password) if user else (
            "No account uses this email yet: sent to the sign-up form" if "@" in (typed_login or "")
            else "No account with this username or email")
    when, method = _when(), _method(request)
    log.warning("Sign-in refused (%s) for %r from %s", reason, (typed_login or "")[:150], ip)

    key = f"user-{user.pk}" if user else (typed_login or "").strip().lower()[:150]
    account_ok = (_under_cap("lock", key, 1) if lockout
                  else _under_cap("acct", key, FAILURE_ALERTS_PER_ACCOUNT))
    if not (account_ok and _under_cap("ip", ip, FAILURE_ALERTS_PER_IP)):
        return  # still logged above; just no more emails this hour

    details = [("Typed", (typed_login or "")[:150]), ("Account", (user.get_full_name() or user.username) if user else "None found"),
               ("Reason", reason), ("Method", method), ("Time", when), ("Device", device), ("IP address", ip)]
    admin_to = _admin_address()
    reset_url = reverse("accounts:password_reset")

    def job():
        if user and user.email:
            _send(user.email, "Someone tried to sign in to your account", category="Security",
                  greeting_name=user.first_name or user.username,
                  intro="A sign-in to your account was just refused.",
                  body="Nobody got in. If this was you, check your password and try again.",
                  details=details, button_label="Reset my password", button_url=reset_url,
                  note="If this wasn't you, someone may know your username or email. Use a strong password and "
                       "never share it.",
                  reason=f"Sent to every {settings.SITE_NAME} account holder when a sign-in to their account is refused.")
        if admin_to and not (user and _same(admin_to, user.email)):
            who = (user.get_full_name() or user.username) if user else (typed_login or "unknown")[:150]
            _send(admin_to, f"Refused sign-in: {who}", category="Security",
                  intro=f"A sign-in attempt was refused: {reason.lower()}.",
                  body=("The account owner was also emailed." if user and user.email
                        else "No account owner to notify: the username or email doesn't match any account."),
                  details=details, button_label="Open dashboard", button_url="/dashboard/",
                  reason="Sent to the administrator for every refused sign-in on the website.")

    _run(job)


def google_failed(request, why, email="", account=None):
    """Called by the Google sign-in flow when it refuses someone."""
    alert_failure(request, email or "(Google account)", reason=f"Google sign-in refused: {why}", account=account)
