import hashlib
import time

from django.conf import settings
from django.core import signing
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from django.core.cache import cache

from core.security import hit_rate_limit
from core.utils import client_ip
from notifications.services import notify

from . import email_codes
from .forms import (AgentApplicationForm, ChooseNewPasswordForm, EmailCodeForm, LoginForm, OwnPasswordChangeForm,
                    ProfileForm, ResetRequestForm)
from .models import EmailCode, User


class LoginView(auth_views.LoginView):
    """Staff login (administrators and agents). Visitors never need an account."""

    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        if form.signup_email:  # nobody uses this email yet: go straight to creating the account
            return start_sign_up(self.request, form.signup_email)
        return super().form_invalid(form)

    def form_valid(self, form):
        """Correct password. With LOGIN_EMAIL_CODE on, sign-in finishes only after the emailed code."""
        user = form.get_user()
        if not settings.LOGIN_EMAIL_CODE or not user.email:
            return super().form_valid(form)
        result = email_codes.issue_code(user, EmailCode.Purpose.LOGIN)
        if result == email_codes.FAILED:
            form.add_error(None, "We couldn't email your sign-in code right now. Please try again in a moment.")
            return super().form_invalid(form)
        if result == email_codes.TOO_MANY:
            form.add_error(None, "Too many sign-in codes were requested. Please wait an hour and try again.")
            return super().form_invalid(form)
        self.request.session[PENDING_LOGIN] = {
            "uid": user.pk, "backend": user.backend, "next": self.get_redirect_url(), "at": time.time()}
        return redirect("accounts:login_code")


PENDING_LOGIN = "pending_login"
PENDING_LOGIN_SECONDS = 15 * 60
RESET_EMAIL = "reset_email"
CODE_TRIES_PER_IP = 30  # wrong codes per connection per 15 minutes (on top of the per-code limit)


def _pending_login_user(request):
    data = request.session.get(PENDING_LOGIN)
    if not data or time.time() - data.get("at", 0) > PENDING_LOGIN_SECONDS:
        return None, None
    return User.objects.filter(pk=data["uid"], is_active=True).first(), data


def login_code(request):
    """Step 2 of signing in: the 6-digit code that was emailed after the correct password."""
    user, data = _pending_login_user(request)
    if user is None:
        request.session.pop(PENDING_LOGIN, None)
        messages.info(request, "Please sign in again.")
        return redirect("accounts:login")
    form = EmailCodeForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "resend":
        result = email_codes.issue_code(user, EmailCode.Purpose.LOGIN)
        _say_resend_result(request, result)
        return redirect("accounts:login_code")
    if request.method == "POST" and form.is_valid():
        if hit_rate_limit(request, "login-code", CODE_TRIES_PER_IP, 15 * 60):
            form.add_error("code", "Too many attempts. Please wait 15 minutes.")
        else:
            result = email_codes.check_code(user, EmailCode.Purpose.LOGIN, form.cleaned_data["code"])
            if result == email_codes.OK:
                request.session.pop(PENDING_LOGIN, None)
                auth_login(request, user, backend=data["backend"])
                nxt = data.get("next") or ""
                if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, request.is_secure()):
                    return redirect(nxt)
                return redirect("accounts:after_login")
            if result == email_codes.LOCKED:
                request.session.pop(PENDING_LOGIN, None)
                messages.error(request, "Too many wrong codes. Please sign in again to get a new code.")
                return redirect("accounts:login")
            form.add_error("code", "That code has expired. Send a new one below." if result == email_codes.EXPIRED
                           else "That code is not correct. Check the email and try again.")
    return render(request, "accounts/login_code.html", {
        "form": form, "email": email_codes.mask_email(user.email), "minutes": email_codes.CODE_MINUTES})


def _say_resend_result(request, result):
    if result == email_codes.SENT:
        messages.success(request, "A new code is on its way. Check your inbox (and Spam).")
    elif result == email_codes.TOO_SOON:
        messages.info(request, f"We just sent a code. Please wait {email_codes.RESEND_SECONDS} seconds before asking again.")
    elif result == email_codes.TOO_MANY:
        messages.error(request, "Too many codes were requested. Please wait an hour and try again.")
    else:
        messages.error(request, "We couldn't send the email right now. Please try again in a moment.")


RESET_REQUESTS_PER_IP = 10  # per hour


def password_reset_request(request):
    """'Forgot password?': email a 6-digit code to the account's address.
    The answer is the same whether or not an account uses that email (no account guessing)."""
    form = ResetRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if hit_rate_limit(request, "reset-request", RESET_REQUESTS_PER_IP, 3600):
            form.add_error(None, "Too many requests from your connection. Please try again later.")
        else:
            user = User.objects.filter(email__iexact=email, is_active=True).first()
            if user is not None:
                result = email_codes.issue_code(user, EmailCode.Purpose.RESET)
                if result == email_codes.FAILED:
                    form.add_error(None, "We couldn't send the email right now. Please try again in a moment.")
                    return render(request, "accounts/password_reset.html", {"form": form})
            request.session[RESET_EMAIL] = email
            return redirect("accounts:password_reset_code")
    return render(request, "accounts/password_reset.html", {"form": form})


def password_reset_code(request):
    """Enter the emailed code and choose a new password (one page)."""
    email = request.session.get(RESET_EMAIL)
    if not email:
        return redirect("accounts:password_reset")
    user = User.objects.filter(email__iexact=email, is_active=True).first()
    if request.method == "POST" and request.POST.get("action") == "resend":
        if user is not None:
            _say_resend_result(request, email_codes.issue_code(user, EmailCode.Purpose.RESET))
        else:
            messages.success(request, "A new code is on its way. Check your inbox (and Spam).")
        return redirect("accounts:password_reset_code")
    code_form = EmailCodeForm(request.POST or None)
    # Password rules are checked against the real account (or a stand-in, so the page looks the same)
    pw_form = ChooseNewPasswordForm(user or User(email=email), request.POST or None)
    if request.method == "POST" and code_form.is_valid() and pw_form.is_valid():
        if hit_rate_limit(request, "reset-code", CODE_TRIES_PER_IP, 15 * 60):
            code_form.add_error("code", "Too many attempts. Please wait 15 minutes.")
        else:
            result = (email_codes.check_code(user, EmailCode.Purpose.RESET, code_form.cleaned_data["code"])
                      if user is not None else email_codes.WRONG)
            if result == email_codes.OK:
                pw_form.save()  # also clears "must change password"
                request.session.pop(RESET_EMAIL, None)
                messages.success(request, "Your password was changed. You can sign in with it now.")
                return redirect("accounts:login")
            if result == email_codes.LOCKED:
                code_form.add_error("code", "Too many wrong codes. Send a new code below.")
            elif result == email_codes.EXPIRED:
                code_form.add_error("code", "That code has expired. Send a new one below.")
            else:
                code_form.add_error("code", "That code is not correct. Check the email and try again.")
    return render(request, "accounts/password_reset_code.html", {
        "code_form": code_form, "pw_form": pw_form, "email": email_codes.mask_email(email),
        "minutes": email_codes.CODE_MINUTES})


SIGNUP_PREFILL = "signup_prefill"
INVITE_SALT = "accounts.agent-signup-invite"
INVITE_DAYS = 7
INVITES_PER_IP_PER_HOUR = 5


def invite_link(email, first_name="", last_name=""):
    """Signed sign-up link (works on any device, for INVITE_DAYS days; can't be forged or edited)."""
    token = signing.dumps({"e": email, "f": first_name[:150], "l": last_name[:150]}, salt=INVITE_SALT, compress=True)
    return f"{reverse('accounts:agent_register')}?invite={token}"


def send_sign_up_invite(request, email, first_name="", last_name=""):
    """Email the person a link that opens the sign-up form already filled in, so they can finish on their
    phone. At most one per address per day and a few per connection per hour (no spamming strangers)."""
    from core.security import hit_rate_limit
    from notifications.services import email_contact

    from .login_alerts import _run
    address_key = f"signup-invite:{hashlib.sha256(email.encode()).hexdigest()}"
    if cache.get(address_key) or hit_rate_limit(request, "signup-invite", INVITES_PER_IP_PER_HOUR):
        return False
    cache.set(address_key, 1, 24 * 3600)
    link = invite_link(email, first_name, last_name)
    google = settings.GOOGLE_LOGIN_ENABLED

    def job():
        email_contact(email, "Finish creating your account", name=first_name,
                      category="Create your account",
                      intro=f"Someone just tried to sign in to {settings.SITE_NAME} with this email, "
                            "but no account uses it yet.",
                      message="Press the button to open the sign-up form with your email already filled in. "
                              + ("You can also sign up with your Google account from that page. " if google else "")
                              + "The administrator approves new accounts, then you can sign in.",
                      details=[("Email", email), ("Link valid for", f"{INVITE_DAYS} days")],
                      button=("Create my account", link),
                      note="If this wasn't you, ignore this email: no account is created unless you fill in the form.",
                      reason="Sent once because this email address was typed on our sign-in page.")
    _run(job)
    return True


def start_sign_up(request, email, first_name="", last_name="", via_google=False):
    """Open the sign-up form with the email (and name) already filled in, and email the person a sign-up
    link too. The prefill is kept in the session, not the address bar, so the email doesn't end up in
    browser history or server logs."""
    request.session[SIGNUP_PREFILL] = {"email": email, "first_name": first_name[:150], "last_name": last_name[:150]}
    emailed = send_sign_up_invite(request, email, first_name, last_name)
    how = f"Your Google account ({email})" if via_google else email
    google_tip = (" You can also use the Sign up with Google button."
                  if settings.GOOGLE_LOGIN_ENABLED and not via_google else "")
    messages.info(request, f"{how} doesn't have an account yet. Fill in the rest of the form below to create one."
                           f"{google_tip}"
                           f"{' We also emailed a sign-up link to ' + email + ' so you can finish on your phone.' if emailed else ''}"
                           " The administrator approves new accounts, then you can sign in.")
    return redirect("accounts:agent_register")


def _prefill_from_invite(request):
    """?invite=<token> from the email: put its details in the form (and remember them for this visit)."""
    token = request.GET.get("invite")
    if not token:
        return
    try:
        data = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_DAYS * 24 * 3600)
    except signing.SignatureExpired:
        messages.warning(request, "That sign-up link has expired. Fill in the form below, or sign in again to get a new one.")
        return
    except signing.BadSignature:
        return
    request.session[SIGNUP_PREFILL] = {"email": data.get("e", ""), "first_name": data.get("f", ""),
                                       "last_name": data.get("l", "")}


class PasswordChangeView(auth_views.PasswordChangeView):
    """Change password. The current (or initial) password must be typed first; after that the
    'initial password' mark is cleared and the person can use the dashboard."""

    template_name = "accounts/password_change.html"
    form_class = OwnPasswordChangeForm

    def get_success_url(self):
        return reverse("accounts:after_login")

    def form_valid(self, form):
        response = super().form_valid(form)  # saves and keeps the person signed in
        user = self.request.user
        if user.must_change_password:
            User.objects.filter(pk=user.pk).update(must_change_password=False)
        messages.success(self.request, "Your password has been changed. Keep it private.")
        return response


class LogoutView(auth_views.LogoutView):
    """Log out and tell the browser to forget the pages it saw, so Back can't show them again."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        response.headers["Clear-Site-Data"] = '"cache"'
        return response


APPLICATIONS_PER_HOUR = 3


def agent_register(request):
    """Public: a real estate agent applies. The account stays locked until the super admin approves it."""
    if request.user.is_authenticated:
        return redirect("accounts:after_login")
    if request.method == "GET":
        _prefill_from_invite(request)
    prefill = request.session.get(SIGNUP_PREFILL) or {}
    form = AgentApplicationForm(request.POST or None, initial={k: v for k, v in prefill.items() if v})
    if request.method == "POST":
        key = f"agent-apply:{client_ip(request)}"
        if cache.get(key, 0) >= APPLICATIONS_PER_HOUR:
            messages.error(request, "Too many applications from your connection. Please try again later.")
        elif form.is_valid():
            agent = form.save()
            request.session.pop(SIGNUP_PREFILL, None)
            cache.set(key, cache.get(key, 0) + 1, 3600)
            msg = (f"{agent.get_full_name()} ({agent.phone}) applied to become an agent"
                   f"{' with ' + agent.agent_profile.agency_name if agent.agent_profile.agency_name else ''}.")
            for boss in User.objects.filter(is_superuser=True, is_active=True):
                notify(boss, "New agent application", msg, "/dashboard/agent-applications/")
            # Confirmation to the applicant, at the email they gave (sign-in comes after approval)
            notify(agent, "We received your agent application",
                   "The administrator will review it and we'll email you here as soon as there's a decision. "
                   "You can't sign in until it is approved.", email=True, category="Agent application",
                   intro=f"Thank you for applying to join {settings.SITE_NAME} as a real estate agent.",
                   details=[("Name", agent.get_full_name()), ("Username", agent.username), ("Email", agent.email),
                            ("Phone", agent.phone), ("Status", "Waiting for review")],
                   note="Once approved, you'll sign in with your username or this email address and the password "
                        "you chose. Keep your password private.")
            request.session["agent_applied"] = agent.get_full_name() or agent.username
            return redirect("accounts:agent_register_done")
    return render(request, "accounts/agent_register.html", {"form": form})


def agent_register_done(request):
    name = request.session.get("agent_applied")
    if not name:
        return redirect("accounts:agent_register")
    return render(request, "accounts/agent_register_done.html", {"name": name})


@login_required
def after_login(request):
    if request.user.uses_dashboard:
        return redirect("dashboard:home")
    return redirect("pages:home")


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})
