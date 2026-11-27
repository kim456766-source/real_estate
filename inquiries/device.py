"""Which device a client is using, so their conversations open only there (no code to type).

The first time a visitor sends a message, their browser gets a random device key (a long-lived
cookie that scripts can't read). The inquiry stores only a hash of it. Later, the same browser on the
same phone or computer sees its conversations straight away; any other device sees nothing.

We do not use the IP address to decide: on mobile networks (MTN, Airtel) many customers share one
public IP, so strangers would see each other's messages, and a phone's IP changes when it moves
between Wi-Fi and mobile data, so the real client would lose their chat. The IP and the type of
device are only recorded, so the agent can see where the message came from.
"""
import hashlib
import secrets
from urllib.parse import quote

from django.conf import settings

COOKIE = "tp_device"
COOKIE_AGE = 2 * 365 * 24 * 60 * 60  # two years


def _hash(token):
    return hashlib.sha256(f"{settings.SECRET_KEY}:{token}".encode()).hexdigest()


def current_key(request):
    """Hashed key of this device, or "" if it never sent a message from this browser."""
    token = request.COOKIES.get(COOKIE, "")
    if not token and getattr(request, "_new_device_token", None):
        token = request._new_device_token
    return _hash(token) if 20 <= len(token) <= 100 else ""


def ensure_key(request):
    """Key of this device, creating one if needed (remember_device() then stores it in the browser)."""
    if not current_key(request):
        request._new_device_token = secrets.token_urlsafe(32)
    return current_key(request)


def remember_device(request, response):
    token = getattr(request, "_new_device_token", None)
    if token:
        response.set_cookie(COOKIE, token, max_age=COOKIE_AGE, httponly=True, samesite="Lax",
                            secure=settings.SESSION_COOKIE_SECURE)
    return response


def describe(request):
    """Short, readable description of the device, e.g. 'Android phone · Chrome'."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    low = ua.lower()
    if "iphone" in low:
        kind = "iPhone"
    elif "ipad" in low:
        kind = "iPad"
    elif "android" in low:
        kind = "Android phone" if "mobile" in low else "Android tablet"
    elif "windows" in low:
        kind = "Windows computer"
    elif "mac os" in low or "macintosh" in low:
        kind = "Mac"
    elif "linux" in low:
        kind = "Linux computer"
    else:
        kind = "Unknown device"
    for name, marker in (("Edge", "edg/"), ("Opera", "opr/"), ("Samsung Internet", "samsungbrowser"),
                         ("Chrome", "chrome/"), ("Firefox", "firefox/"), ("Safari", "safari/")):
        if marker in low:
            return f"{kind} · {name}"
    return kind


# ---------- WhatsApp ----------
def whatsapp_number(phone):
    """International digits for wa.me: '0788 123 456' / '+250788123456' -> '250788123456'."""
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    if d.startswith("00"):
        d = d[2:]
    if len(d) == 10 and d.startswith("07"):
        d = "250" + d[1:]
    elif len(d) == 9 and d.startswith("7"):
        d = "250" + d
    return d if 10 <= len(d) <= 15 else ""


def whatsapp_link(phone, text):
    number = whatsapp_number(phone)
    return f"https://wa.me/{number}?text={quote(text)}" if number else ""


def listing_text(prop, url, message="", name=""):
    """The WhatsApp message: the client's words + the listing (title, reference, price, link)."""
    price = f"{prop.price:,.0f} {prop.currency}" + (f" / {prop.rent_period}" if prop.rent_period else "")
    lines = []
    if name:
        lines.append(f"Hello, my name is {name}.")
    lines.append(message.strip() if message else f"Hello, I am interested in this property.")
    lines += ["", f"Property: {prop.title} ({prop.reference})", f"Price: {price}",
              f"Location: {prop.location_display}", url]
    return "\n".join(lines)
