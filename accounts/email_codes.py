"""One-time 6-digit codes sent by email (sign-in verification and password reset).

Safety:
  * Only an HMAC of the code is stored, never the code itself.
  * A code works once, for CODE_MINUTES minutes, and only the newest code of each kind counts.
  * MAX_ATTEMPTS wrong tries burn the code; a new code can be requested at most every
    RESEND_SECONDS seconds and MAX_PER_HOUR times an hour per account.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from notifications.emails import send_branded

from .models import EmailCode

log = logging.getLogger("theo.security")

CODE_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
MAX_PER_HOUR = 5

# results of issue_code()
SENT, TOO_SOON, TOO_MANY, FAILED = "sent", "too_soon", "too_many", "failed"
# results of check_code()
OK, WRONG, EXPIRED, LOCKED = "ok", "wrong", "expired", "locked"

TEXT = {
    EmailCode.Purpose.LOGIN: {
        "subject": "{code} is your sign-in code",
        "heading": "Your sign-in code",
        "intro": "Someone (hopefully you) entered your password correctly. Enter this code to finish signing in.",
        "note": "If this wasn't you, your password may be known to someone else: change it now and tell the administrator.",
    },
    EmailCode.Purpose.RESET: {
        "subject": "{code} is your password reset code",
        "heading": "Reset your password",
        "intro": "Enter this code on the website to choose a new password.",
        "note": "If you didn't ask to reset your password, ignore this email: your password stays the same.",
    },
}


def _hash(user, purpose, code):
    msg = f"{user.pk}:{purpose}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def mask_email(email):
    """e.g. elyse@gmail.com -> e***e@gmail.com (shown on screen, so the address isn't fully revealed)."""
    name, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    shown = name[0] + "***" + (name[-1] if len(name) > 2 else "")
    return f"{shown}@{domain}"


def issue_code(user, purpose):
    """Create a new code, email it, and return SENT / TOO_SOON / TOO_MANY / FAILED."""
    now = timezone.now()
    recent = EmailCode.objects.filter(user=user, purpose=purpose, created_at__gte=now - timedelta(hours=1))
    latest = recent.first()
    if latest and (now - latest.created_at).total_seconds() < RESEND_SECONDS and latest.used_at is None:
        return TOO_SOON
    if recent.count() >= MAX_PER_HOUR:
        log.warning("Too many %s codes requested for %s", purpose, user.username)
        return TOO_MANY

    EmailCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)  # old ones stop working
    code = f"{secrets.randbelow(10 ** 6):06d}"
    record = EmailCode.objects.create(user=user, purpose=purpose, code_hash=_hash(user, purpose, code),
                                      expires_at=now + timedelta(minutes=CODE_MINUTES))
    text = TEXT[purpose]
    sent = send_branded(
        user.email, text["subject"].format(code=code), heading=text["heading"], category="Security",
        preheader=f"Your code: {code}. It expires in {CODE_MINUTES} minutes.",
        greeting_name=user.first_name or user.username, intro=text["intro"],
        details=[("Your code", f"{code[:3]} {code[3:]}"), ("Expires in", f"{CODE_MINUTES} minutes")],
        note=text["note"] + " Never share this code: our team will never ask you for it.",
        reason=f"You received this because of a request on your {settings.SITE_NAME} account.")
    if not sent:
        record.delete()
        return FAILED
    return SENT


def check_code(user, purpose, code):
    """Check a typed code and return OK / WRONG / EXPIRED / LOCKED. A correct code is used up."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    record = (EmailCode.objects.select_for_update()
              .filter(user=user, purpose=purpose, used_at__isnull=True).first())
    if record is None or record.expires_at <= timezone.now():
        return EXPIRED
    if record.attempts >= MAX_ATTEMPTS:
        return LOCKED
    EmailCode.objects.filter(pk=record.pk).update(attempts=F("attempts") + 1)
    if len(code) == 6 and hmac.compare_digest(record.code_hash, _hash(user, purpose, code)):
        EmailCode.objects.filter(pk=record.pk).update(used_at=timezone.now())
        return OK
    if record.attempts + 1 >= MAX_ATTEMPTS:
        log.warning("Too many wrong %s codes for %s", purpose, user.username)
        return LOCKED
    return WRONG
