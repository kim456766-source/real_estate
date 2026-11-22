from django import forms
from django.contrib.auth.forms import (AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm,
                                       UserCreationForm)
from django.core.validators import validate_email

from core.security import clean_text, hit_rate_limit, rate_limit_exceeded, reset_rate_limit

from .models import AgentProfile, User


class BootstrapFormMixin:
    """Add Bootstrap classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                w.attrs.setdefault("class", "form-check-input")
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs.setdefault("class", "form-select")
            elif not isinstance(w, forms.HiddenInput):
                w.attrs.setdefault("class", "form-control")
            if field.required and not isinstance(w, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
                w.attrs.setdefault("aria-required", "true")

    # Fields that must be kept exactly as typed (passwords are never touched either).
    raw_fields = ()

    def _clean_fields(self):
        """After normal validation, store plain text only: no HTML/script tags, no hidden characters."""
        super()._clean_fields()
        for name, value in list(self.cleaned_data.items()):
            field = self.fields.get(name)
            if (isinstance(value, str) and field is not None and name not in self.raw_fields
                    and not isinstance(field.widget, forms.PasswordInput)):
                self.cleaned_data[name] = clean_text(value)

    def full_clean(self):
        """Mark fields with errors so they get a red border (Bootstrap .is-invalid)."""
        super().full_clean()
        for name in self.errors:
            if name in self.fields:
                w = self.fields[name].widget
                w.attrs["class"] = (w.attrs.get("class", "") + " is-invalid").strip()
                w.attrs["aria-invalid"] = "true"


class LoginForm(BootstrapFormMixin, AuthenticationForm):
    """Staff login. Agents whose application isn't approved yet get a clear explanation
    (only after the correct password is given, so this can't be used to probe usernames)."""

    MAX_FAILURES = 5          # wrong passwords allowed ...
    LOCK_SECONDS = 15 * 60    # ... per username and connection in 15 minutes
    MAX_FAILURES_PER_IP = 20  # and in total from one connection (stops trying many usernames)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Username or email"
        self.error_messages["invalid_login"] = ("Please enter a correct username or email and password. "
                                                "Note that the password is case-sensitive.")

    def _attempt_key(self):
        """One lock per ACCOUNT, whether they type the username or the email (so the limit can't be doubled)."""
        login = (self.cleaned_data.get("username") or "").strip().lower()[:150]
        field = "email__iexact" if "@" in login else "username__iexact"
        pk = User.objects.filter(**{field: login}).values_list("pk", flat=True).first() if login else None
        return f"user-{pk}" if pk else login

    def clean(self):
        username_key = self._attempt_key()
        if self.request is not None and (
                rate_limit_exceeded(self.request, "login", self.MAX_FAILURES, username_key)
                or rate_limit_exceeded(self.request, "login-ip", self.MAX_FAILURES_PER_IP)):
            from .login_alerts import alert_failure
            alert_failure(self.request, self.cleaned_data.get("username", ""),
                          reason="Too many failed attempts: sign-in blocked for 15 minutes", lockout=True)
            raise forms.ValidationError("Too many failed login attempts. Please wait 15 minutes and try again.",
                                        code="locked")
        try:
            data = super().clean()
        except forms.ValidationError:
            if self.request is not None:
                hit_rate_limit(self.request, "login", self.MAX_FAILURES, self.LOCK_SECONDS, username_key)
                hit_rate_limit(self.request, "login-ip", self.MAX_FAILURES_PER_IP, self.LOCK_SECONDS)
            self._explain_inactive_agent()
            self._offer_sign_up()
            raise
        if self.request is not None:
            reset_rate_limit(self.request, "login", username_key)
        return data

    signup_email = None  # set when the typed email has no account: the view then opens the sign-up form

    def _offer_sign_up(self):
        """An email address that no account uses yet: remember it so the person can sign up with it."""
        login = (self.cleaned_data.get("username") or "").strip().lower()
        if "@" not in login or len(login) > 254:
            return
        try:
            validate_email(login)
        except forms.ValidationError:
            return
        if User.objects.filter(email__iexact=login).exists() or User.objects.filter(username__iexact=login).exists():
            return
        self.signup_email = login
        raise forms.ValidationError("No account uses this email yet.", code="no_account")

    def _explain_inactive_agent(self):
        """Correct password but locked account: say why (pending or rejected application)."""
        username, password = self.cleaned_data.get("username"), self.cleaned_data.get("password")
        login = (username or "").strip()
        user = None
        if login and "@" in login:
            user = User.objects.filter(email__iexact=login).first()
        if login and user is None:
            user = User.objects.filter(username__iexact=login).first()
        profile = getattr(user, "agent_profile", None) if user else None
        if user and profile and not user.is_active and password and user.check_password(password):
            if profile.application_status == AgentProfile.Application.PENDING:
                raise forms.ValidationError(
                    "Your agent application is waiting for approval by the administrator. "
                    "You'll be able to log in as soon as it is approved.", code="pending")
            if profile.application_status == AgentProfile.Application.REJECTED:
                reason = f" Reason: {profile.rejection_reason}" if profile.rejection_reason else ""
                raise forms.ValidationError(
                    f"Your agent application was not approved.{reason} "
                    "Contact us on WhatsApp if you have questions.", code="rejected")


class AgentApplicationForm(BootstrapFormMixin, UserCreationForm):
    """Public form: a real estate agent applies for an account. The account stays locked until approved."""

    agency_name = forms.CharField(required=False, label="Agency / company (optional)")
    license_number = forms.CharField(required=False, label="Licence or registration number (optional)")
    years_experience = forms.IntegerField(min_value=0, max_value=60, initial=0, label="Years of experience")
    application_note = forms.CharField(required=False, label="Tell us about yourself (optional)",
                                       widget=forms.Textarea(attrs={"rows": 3, "maxlength": 1000,
                                                                    "placeholder": "Areas you cover, the kind of properties you sell..."}))
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}))
    agree = forms.BooleanField(label="I confirm these details are correct and I agree to follow Theo Property's rules.")

    class Meta:
        model = User
        fields = ("first_name", "last_name", "phone", "email", "username")
        labels = {"phone": "Phone (WhatsApp)", "username": "Choose a username"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("first_name", "last_name", "phone", "email"):
            self.fields[name].required = True
        self.fields["phone"].widget.attrs["placeholder"] = "07XX XXX XXX"

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_website(self):
        if self.cleaned_data.get("website"):  # honeypot: only bots fill this in
            raise forms.ValidationError("Application rejected.")
        return ""

    def save(self, commit=True):
        from django.utils import timezone
        user = super().save(commit=False)
        user.role = User.Role.AGENT
        user.is_active = False  # locked until the super admin approves
        user.save()
        profile = user.agent_profile
        profile.agency_name = self.cleaned_data["agency_name"]
        profile.license_number = self.cleaned_data["license_number"]
        profile.years_experience = self.cleaned_data["years_experience"]
        profile.application_note = self.cleaned_data["application_note"]
        profile.application_status = AgentProfile.Application.PENDING
        profile.applied_at = timezone.now()
        profile.can_publish_directly = False
        profile.save()
        return user


class OwnPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    """Current (or initial) password first, then a new one that must be different."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.user.must_change_password:
            self.fields["old_password"].label = "Initial password (given by the administrator)"

    def clean_new_password1(self):
        new = self.cleaned_data.get("new_password1")
        if new and self.user.check_password(new):
            raise forms.ValidationError("Choose a new password, different from the one you were given.")
        return new


class ResetPasswordByEmailForm(BootstrapFormMixin, PasswordResetForm):
    """'Forgot password?': the person types the email on their account and gets a reset link."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].label = "Email on your account"
        self.fields["email"].widget.attrs["placeholder"] = "e.g. aline@email.com"


class ChooseNewPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """From the emailed reset link: the person chooses their own password, so it is no longer 'initial'."""

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = False
        if commit:
            user.save()
        return user


class InitialPasswordForm(BootstrapFormMixin, SetPasswordForm):
    """An administrator sets an initial password; the person must change it at their next sign-in."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Initial password"
        self.fields["new_password1"].help_text = ("Give it to the person privately. They must change it the first "
                                                  "time they sign in.")
        self.fields["new_password2"].label = "Repeat initial password"

    def save(self, commit=True):
        user = super().save(commit=False)
        user.must_change_password = True
        if commit:
            user.save()
        return user


class AdminCreateForm(BootstrapFormMixin, UserCreationForm):
    """Super admin creates another administrator, with an initial password."""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "email", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _label_initial_password(self)
        for name in ("first_name", "last_name", "email"):
            self.fields[name].required = True

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.ADMIN
        user.must_change_password = True
        user.save()
        return user


def _label_initial_password(form):
    form.fields["password1"].label = "Initial password"
    form.fields["password1"].help_text = ("Give it to the person privately. They sign in with it once and must "
                                          "then choose their own password.")
    form.fields["password2"].label = "Repeat initial password"
    form.fields["email"].required = True
    form.fields["email"].help_text = ("They can sign in with this email or the username. Their account details, "
                                      "notifications and password reset links are sent here.")
    form.fields["username"].help_text = "Letters, digits and @ . + - _ only."


class AgentReviewForm(BootstrapFormMixin, forms.Form):
    rejection_reason = forms.CharField(required=False, max_length=255, label="Reason (shown to the applicant)")


class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "district", "photo", "bio")
        widgets = {"bio": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"email": "You can sign in with this email and receive password reset links here."}

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another account already uses this email.")
        return email


class AgentCreateForm(BootstrapFormMixin, UserCreationForm):
    """Used by administrators to register agents."""

    agency_name = forms.CharField(required=False)
    license_number = forms.CharField(required=False)
    can_publish_directly = forms.BooleanField(required=False)
    can_manage_appointments = forms.BooleanField(required=False, initial=True)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "email", "phone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _label_initial_password(self)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.AGENT
        user.must_change_password = True  # signs in once with the given password, then chooses their own
        user.save()
        profile = user.agent_profile
        profile.agency_name = self.cleaned_data["agency_name"]
        profile.license_number = self.cleaned_data["license_number"]
        profile.can_publish_directly = self.cleaned_data["can_publish_directly"]
        profile.can_manage_appointments = self.cleaned_data["can_manage_appointments"]
        profile.save()
        return user


class AgentProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = AgentProfile
        fields = ("agency_name", "license_number", "years_experience", "is_verified",
                  "can_publish_directly", "can_manage_appointments")


class UserRoleForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("role", "is_active")

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if actor is not None and not actor.is_superuser:  # only the super admin makes administrators
            self.fields["role"].choices = [c for c in self.fields["role"].choices if c[0] != User.Role.ADMIN]
