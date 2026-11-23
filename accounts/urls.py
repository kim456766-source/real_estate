from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .forms import ChooseNewPasswordForm

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/code/", views.login_code, name="login_code"),
    path("agent/apply/", views.agent_register, name="agent_register"),
    path("agent/apply/thank-you/", views.agent_register_done, name="agent_register_done"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("welcome/", views.after_login, name="after_login"),
    path("profile/", views.profile, name="profile"),
    path("password/", views.PasswordChangeView.as_view(), name="password_change"),
    # Forgot password: a 6-digit code is emailed, then typed in together with the new password
    path("password-reset/", views.password_reset_request, name="password_reset"),
    path("password-reset/code/", views.password_reset_code, name="password_reset_code"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    # Older emailed reset links keep working until they expire
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html", form_class=ChooseNewPasswordForm,
        success_url=reverse_lazy("accounts:login")), name="password_reset_confirm"),
]
