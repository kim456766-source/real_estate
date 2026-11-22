import hashlib

from django.conf import settings
from django.core import signing
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from django.core.cache import cache

from core.utils import client_ip
from notifications.services import notify

from .forms import AgentApplicationForm, LoginForm, OwnPasswordChangeForm, ProfileForm
from .models import User


class LoginView(auth_views.LoginView):
    """Staff login (administrators and agents). Visitors never need an account."""

    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_invalid(self, form):
        if form.signup_email:  # nobody uses this email yet: go straight to creating the account
            return start_sign_up(self.request, form.signup_email)
        return super().form_invalid(form)


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
