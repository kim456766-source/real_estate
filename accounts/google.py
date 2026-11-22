"""'Continue with Google' for staff (agents and administrators).

Google only proves who the person is. They get in only if an ACTIVE agent/admin account already uses
that exact (verified) Google email. No account is ever created here, so outsiders cannot sign in.

Flow (OpenID Connect, authorization code):
  1. /auth/google/            -> send the person to Google with a random `state` and `nonce`
  2. /auth/google/callback/   -> check `state`, swap the one-time code for an ID token (server to
                                 Google over HTTPS), check the token, find the staff account, sign in.
"""
import base64
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.security import hit_rate_limit

from .login_alerts import google_failed
from .models import User

log = logging.getLogger("theo.security")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
SESSION_KEY = "google_oauth"
FAILURES_PER_HOUR = 20


def redirect_uri(request):
    return settings.GOOGLE_OAUTH_REDIRECT_URI or request.build_absolute_uri(reverse("google_callback"))


def google_start(request):
    if not settings.GOOGLE_LOGIN_ENABLED:
        raise Http404
    nxt = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
        nxt = ""
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    request.session[SESSION_KEY] = {"state": state, "nonce": nonce, "next": nxt, "at": time.time()}
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": redirect_uri(request),
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "prompt": "select_account",
    }
    return redirect(f"{AUTH_URL}?{urllib.parse.urlencode(params)}")


def exchange_code(request, code):
    """Swap the one-time code for Google's ID token (direct HTTPS call to Google)."""
    data = urllib.parse.urlencode({
        "code": code, "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri(request), "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.loads(res.read().decode())


def read_id_token(id_token):
    """The token's claims. It came straight from Google's token endpoint over HTTPS (not through the
    browser), which OpenID Connect accepts as proof it is genuine; we still check what it says."""
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _fail(request, text, why, email="", account=None):
    log.warning("Google sign-in refused (%s) from %s", why, request.META.get("REMOTE_ADDR"))
    google_failed(request, why, email=email, account=account)  # tell the administrator (and the owner)
    hit_rate_limit(request, "google-login", FAILURES_PER_HOUR)
    messages.error(request, text)
    return redirect("accounts:login")


def google_callback(request):
    if not settings.GOOGLE_LOGIN_ENABLED:
        raise Http404
    saved = request.session.pop(SESSION_KEY, None)
    if hit_rate_limit(request, "google-login-check", FAILURES_PER_HOUR * 3):
        messages.error(request, "Too many sign-in attempts. Please wait a while and try again.")
        return redirect("accounts:login")
    if request.GET.get("error"):
        messages.info(request, "Google sign-in was cancelled.")
        return redirect("accounts:login")
    if not saved or not secrets.compare_digest(saved["state"], request.GET.get("state", "")) \
            or time.time() - saved["at"] > 600:
        return _fail(request, "That Google sign-in link expired. Please try again.", "bad or old state")
    code = request.GET.get("code", "")
    if not code:
        return _fail(request, "Google sign-in failed. Please try again.", "no code")
    try:
        tokens = exchange_code(request, code)
        claims = read_id_token(tokens["id_token"])
    except (urllib.error.URLError, KeyError, ValueError, IndexError) as exc:
        log.warning("Google token exchange failed: %s", exc)
        messages.error(request, "Couldn't reach Google. Please try again or sign in with your password.")
        return redirect("accounts:login")

    if (claims.get("iss") not in ISSUERS or claims.get("aud") != settings.GOOGLE_OAUTH_CLIENT_ID
            or claims.get("exp", 0) < time.time() or not secrets.compare_digest(claims.get("nonce", ""), saved["nonce"])):
        return _fail(request, "Google sign-in failed. Please try again.", "token checks")
    email = (claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") not in (True, "true"):
        return _fail(request, "Your Google email isn't verified, so it can't be used to sign in.", "unverified email", email=email)

    user = User.objects.filter(email__iexact=email).first()
    if not user:  # verified email nobody uses yet: let them sign up with it
        from .views import start_sign_up
        log.info("Google sign-in: no account for %s, sent to sign-up", email)
        google_failed(request, "no account yet, sent to the sign-up form", email=email)
        return start_sign_up(request, email, claims.get("given_name") or "", claims.get("family_name") or "",
                             via_google=True)
    if not (user.is_superuser or user.role in (User.Role.ADMIN, User.Role.AGENT)):
        return _fail(request, f"No agent or administrator account uses {email}. Sign in with your username and "
                              "password, or ask the administrator to use this email on your account.", "no staff account", email=email, account=user)
    if not user.is_active:
        return _fail(request, "This account is not active. Contact the administrator.", "inactive account", email=email, account=user)

    login(request, user, backend="accounts.backends.EmailOrUsernameBackend")
    log.info("Google sign-in: %s", user.username)
    return redirect(saved.get("next") or "accounts:after_login")
