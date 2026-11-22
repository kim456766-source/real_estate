"""Security helpers shared by the whole project.

- clean_text():     strip HTML tags and invisible control characters from what people type
- safe_next_url():  only follow ?next= links that stay on this website (no open redirects)
- csv_safe():       stop spreadsheet formulas hidden in names/messages from running in Excel
- hit_rate_limit(): simple per-IP limits for public forms and logins (stops spam and guessing)
- client_ip():      the visitor's IP, without trusting a header anyone can fake
"""
import ipaddress
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.utils.html import strip_tags
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger("theo.security")

# Control characters except tab (\x09), newline (\x0a) and carriage return (\x0d); also NUL bytes,
# which PostgreSQL refuses to store and which are a common trick to hide malicious input.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_TAG_LIKE = re.compile(r"<\s*/?\s*[a-zA-Z!]")


def clean_text(value):
    """Plain text only: no HTML/script tags, no invisible characters. Keeps line breaks."""
    if not isinstance(value, str):
        return value
    value = _CONTROL_CHARS.sub("", value)
    if _TAG_LIKE.search(value):  # only run strip_tags when something looks like a tag ("price < 5M" is kept)
        value = strip_tags(value)
    return value.strip()


def safe_next_url(request, fallback="/"):
    """Return the ?next= / POST next value only if it points to this site; otherwise `fallback`."""
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    if nxt:
        logger.warning("Blocked unsafe redirect to %r from %s", nxt[:200], client_ip(request))
    return fallback


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


_PLAIN_NUMBER = re.compile(r"^[+-][\d\s().-]*$")  # "+250 788 123 456" is a phone number, not a formula


def csv_safe(value):
    """Prefix values that Excel/Sheets would run as a formula (CSV / formula injection)."""
    if isinstance(value, str) and value.startswith(_FORMULA_START) and not _PLAIN_NUMBER.match(value):
        return "'" + value
    return value


class SafeCsvWriter:
    """csv.writer that neutralises formulas in every cell."""

    def __init__(self, writer):
        self._w = writer

    def writerow(self, row):
        return self._w.writerow([v if v is None or isinstance(v, (int, float)) else csv_safe(str(v)) for v in row])


def client_ip(request):
    """Visitor IP. X-Forwarded-For is only trusted behind our own proxy (TRUST_X_FORWARDED_FOR=True)."""
    ip = request.META.get("REMOTE_ADDR", "")
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return "0.0.0.0"


def hit_rate_limit(request, scope, limit, window=3600, key_extra=""):
    """Count one attempt; return True when the visitor is over `limit` attempts in `window` seconds."""
    key = f"rl:{scope}:{client_ip(request)}:{key_extra}"[:240]
    try:
        cache.add(key, 0, window)
        count = cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, window)
        count = 1
    except Exception:  # cache down: never block real users because of it
        logger.exception("Rate-limit cache unavailable")
        return False
    if count > limit:
        logger.warning("Rate limit '%s' hit by %s", scope, client_ip(request))
        return True
    return False


def rate_limit_exceeded(request, scope, limit, key_extra=""):
    """Check (without counting) whether the visitor already used up `limit` attempts."""
    try:
        return (cache.get(f"rl:{scope}:{client_ip(request)}:{key_extra}"[:240]) or 0) >= limit
    except Exception:
        logger.exception("Rate-limit cache unavailable")
        return False


def reset_rate_limit(request, scope, key_extra=""):
    try:
        cache.delete(f"rl:{scope}:{client_ip(request)}:{key_extra}"[:240])
    except Exception:
        logger.exception("Rate-limit cache unavailable")
