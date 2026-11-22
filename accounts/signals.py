from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db.models.signals import post_save
from django.dispatch import receiver

from . import login_alerts
from .models import AgentProfile, OwnerProfile, User


@receiver(user_logged_in)
def alert_on_login(sender, request, user, **kwargs):
    """Email the administrator and the account owner about every successful sign-in."""
    if request is not None:  # skip logins made in code without a real visitor
        login_alerts.alert_success(request, user)


@receiver(user_login_failed)
def alert_on_failed_login(sender, credentials, request=None, **kwargs):
    """Email the administrator (and the account owner, when the account exists) about refused sign-ins."""
    if request is not None:
        login_alerts.alert_failure(request, credentials.get("username", ""), request.POST.get("password", ""))


@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    """Create the matching profile whenever a user has (or gets) the agent/owner role."""
    if instance.role == User.Role.AGENT:
        AgentProfile.objects.get_or_create(user=instance)
    elif instance.role == User.Role.OWNER:
        OwnerProfile.objects.get_or_create(user=instance)
