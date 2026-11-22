"""Single entry point for in-website + email notifications. Emails use the branded layout
(notifications/emails.py): pass `category`, `intro`, `details`, `button`, `quote`, `note` to make
clear what the email is about and what to do next."""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model

from .emails import send_branded
from .models import Notification

logger = logging.getLogger(__name__)


def notify(user, title, message, link="", email=False, *, category="", intro="", details=None, button=None,
           quote="", quote_label="", note="", reason=""):
    """In-website notification (always) + branded email (when email=True).
    `button` = (label, url); without it, `link` becomes an "Open" button."""
    if user is None:
        return None
    note_obj = Notification.objects.create(user=user, title=title, message=message, link=link)
    if email and user.email:
        label, url = button or (("Open in your dashboard", link) if link else (None, None))
        send_branded(user.email, title, category=category, greeting_name=user.first_name or user.username,
                     intro=intro, body=message, details=details, button_label=label, button_url=url,
                     quote=quote, quote_label=quote_label, note=note,
                     reason=reason or f"You received this because you have a {settings.SITE_NAME} account.")
    return note_obj


def email_contact(address, subject, message, *, name="", category="", intro="", details=None, button=None,
                  quote="", quote_label="", note="", reason=""):
    """Email a visitor who has no account (client or property owner)."""
    if not address:
        return
    label, url = button or (None, None)
    send_branded(address, subject, category=category, greeting_name=name, intro=intro, body=message,
                 details=details, button_label=label, button_url=url, quote=quote, quote_label=quote_label,
                 note=note, reason=reason or f"You received this because you contacted {settings.SITE_NAME}.")


def support_inbox():
    from core.models import SiteSetting
    return SiteSetting.load().email or settings.SUPPORT_EMAIL


def email_support(subject, message, reply_to=None, link="", *, details=None, quote="", quote_label="",
                  category="Support"):
    """Send a message to the support inbox. Pressing "Reply" in Gmail answers the visitor directly."""
    to = support_inbox()
    if not to:
        return False
    return send_branded(to, subject, category=category, body=message, details=details, quote=quote,
                        quote_label=quote_label, button_label="Open in dashboard" if link else None,
                        button_url=link, reply_to=reply_to,
                        reason="Sent to the support inbox. Press Reply to answer the visitor directly."
                        if reply_to else "Sent to the support inbox.")


def notify_admins(title, message, link=""):
    User = get_user_model()
    for admin in User.objects.filter(role=User.Role.ADMIN, is_active=True):
        notify(admin, title, message, link)
