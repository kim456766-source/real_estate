"""Branded emails: every message the site sends uses the same clear layout
(templates/emails/branded.html + a plain-text copy):

  [ Theo Property ............................ CATEGORY ]
  Headline                     -> what this email is about, in one line
  Hello <name>,
  Intro (bold)                 -> the one-sentence summary
  Body                         -> the details in words
  "Quote" box                  -> e.g. the client's message or the agent's reply
  Details table                -> key facts (username, property, price, phone ...)
  [ Button ]                   -> the next step (Sign in, Open conversation, View property ...)
  Note (yellow)                -> anything important (e.g. "never share your password")
  Footer                       -> why they got it + how to contact us
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

log = logging.getLogger(__name__)


def absolute(url):
    """'/dashboard/' -> 'https://site/dashboard/' (emails need full addresses)."""
    if not url:
        return ""
    return url if url.startswith(("http://", "https://")) else f"{settings.SITE_URL}{url if url.startswith('/') else '/' + url}"


def email_context(**ctx):
    from core.models import SiteSetting
    site = SiteSetting.load()
    site_url = settings.SITE_URL
    for key in ("category", "greeting_name", "intro", "body", "quote", "quote_label", "button_label", "button_url",
                "note", "reason", "preheader"):
        ctx[key] = ctx.get(key) or ""  # every part is optional; empty ones are simply not shown
    ctx.setdefault("site_name", site.site_name or settings.SITE_NAME)
    ctx.setdefault("support_phone", site.phone)
    ctx.setdefault("site_url", site_url)
    ctx.setdefault("site_url_display", site_url.split("://", 1)[-1])
    # Email apps can only show the logo from a public https address (not from localhost)
    ctx.setdefault("logo_url", f"{site_url}/static/img/logo.png" if site_url.startswith("https://") else "")
    if ctx.get("button_url"):
        ctx["button_url"] = absolute(ctx["button_url"])
    ctx["details"] = [(k, v) for k, v in (ctx.get("details") or []) if v not in (None, "")]
    return ctx


def send_branded(to, subject, *, heading=None, reply_to=None, fail_silently=True, **ctx):
    """Send one branded email. `to` = address or list. Returns True if handed to the mail server."""
    recipients = [to] if isinstance(to, str) else [t for t in (to or []) if t]
    if not recipients:
        return False
    ctx = email_context(heading=heading or subject, **ctx)
    text = render_to_string("emails/branded.txt", ctx)
    html = render_to_string("emails/branded.html", ctx)
    msg = EmailMultiAlternatives(f"[{ctx['site_name']}] {subject}", text, settings.DEFAULT_FROM_EMAIL, recipients,
                                 reply_to=[reply_to] if reply_to else None)
    msg.attach_alternative(html, "text/html")
    try:
        return bool(msg.send())
    except Exception:
        if not fail_silently:
            raise
        log.exception("Failed to send email '%s' to %s", subject, recipients)
        return False
