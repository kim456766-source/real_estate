from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import ChooseNewPasswordForm, ResetPasswordByEmailForm

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("agent/apply/", views.agent_register, name="agent_register"),
    path("agent/apply/thank-you/", views.agent_register_done, name="agent_register_done"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("welcome/", views.after_login, name="after_login"),
    path("profile/", views.profile, name="profile"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    path("password-reset/", auth_views.PasswordResetView.as_view(
        template_name="accounts/password_reset.html", form_class=ResetPasswordByEmailForm,
        email_template_name="accounts/password_reset_email.txt",
        html_email_template_name="accounts/password_reset_email.html",  # branded, like every other email
        subject_template_name="accounts/password_reset_subject.txt",
        extra_email_context={"brand": settings.SITE_NAME, "site_url": settings.SITE_URL,
                             "site_url_display": settings.SITE_URL.split("://", 1)[-1]},
        success_url=reverse_lazy("accounts:password_reset_done")), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html", form_class=ChooseNewPasswordForm,
        success_url=reverse_lazy("accounts:login")), name="password_reset_confirm"),
]
