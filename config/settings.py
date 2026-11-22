"""
Theo Property - Django settings.
All secrets and environment-specific values come from environment variables (.env).
"""
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    # Local apps
    "core",
    "accounts",
    "properties",
    "inquiries",
    "appointments",
    "favorites",
    "payments",
    "notifications",
    "blog",
    "pages",
    "dashboard",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.NoCachePrivatePagesMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
    "core.middleware.HousekeepingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: PostgreSQL via DATABASE_URL, e.g.
#   DATABASE_URL=postgres://theo:password@localhost:5432/theo_property
# Falls back to SQLite when DATABASE_URL is not set (quick local development).
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}
# Every request runs in one database transaction: if anything fails half-way, nothing half-saved is kept.
DATABASES["default"]["ATOMIC_REQUESTS"] = True

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:after_login"
AUTHENTICATION_BACKENDS = ["accounts.backends.EmailOrUsernameBackend"]  # sign in with username or email
LOGOUT_REDIRECT_URL = "pages:home"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Kigali"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Upload limits (secure image uploads)
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
MAX_IMAGE_SIZE_MB = 5

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 12,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "200/hour", "user": "2000/hour"},
    # Clean JSON errors, never a stack trace (api/exceptions.py)
    "EXCEPTION_HANDLER": "api.exceptions.api_exception_handler",
}

# Email (Gmail / Brevo / SendGrid via SMTP).
# Real sending starts automatically once EMAIL_HOST_PASSWORD is set; until then emails print in the terminal.
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "").replace(" ", "")
if EMAIL_HOST_PASSWORD.upper().startswith("YOUR_"):  # the placeholder from the instructions, not a real password
    EMAIL_HOST_PASSWORD = ""
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 15
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND") or (
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST_PASSWORD
    else "django.core.mail.backends.console.EmailBackend")
# Gmail only sends "from" the account you log in with.
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL") or (
    f"Theo Property <{EMAIL_HOST_USER}>" if EMAIL_HOST_USER else "Theo Property <no-reply@theoproperty.rw>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Sign in with Google (staff only: agents/admins whose account email matches their Google email).
# The button appears once both the client ID and a real client secret are set.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
if GOOGLE_OAUTH_CLIENT_SECRET.upper().startswith(("PASTE_", "YOUR_")):  # placeholder, not a real secret
    GOOGLE_OAUTH_CLIENT_SECRET = ""
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
GOOGLE_LOGIN_ENABLED = bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET)

# Inbox that receives support messages (also editable in Dashboard > Settings > Email)
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")
# Sign-in alerts (accounts/login_alerts.py): every successful or refused sign-in is emailed to this
# administrator address AND to the account owner's own email.
# Set it in .env (kept private); falls back to the Gmail account the site sends from.
LOGIN_ALERT_EMAIL = os.getenv("LOGIN_ALERT_EMAIL") or EMAIL_HOST_USER
LOGIN_ALERTS_ENABLED = env_bool("LOGIN_ALERTS_ENABLED", True)
# Send in the background so sign-in stays fast (but in order during `manage.py test`).
LOGIN_ALERTS_ASYNC = env_bool("LOGIN_ALERTS_ASYNC", "test" not in sys.argv[1:2])

# Listing promotion (see properties/ranking.py)
HOT_DEAL_MIN_PERCENT = 10        # at least 10% below similar listings
HOT_DEAL_MAX_PERCENT = 60        # more than 60% below is treated as a data error, not a deal
HOT_DEAL_MIN_COMPARABLES = 3     # need at least 3 similar listings nearby (same sector, else same district)
HOT_DEAL_ALLOW_NATIONWIDE = False  # True = also compare against the whole country when an area has too few
NEW_LISTING_DAYS = 7             # "New" badge for a week
TRENDING_COUNT = 6               # how many listings get the "Trending" badge
INQUIRY_RETENTION_DAYS = 30      # conversations are deleted this many days after their last message

# External services
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
SITE_WHATSAPP_NUMBER = os.getenv("SITE_WHATSAPP_NUMBER", "250789567752")
SITE_NAME = "Theo Property"
# The website's public address, used for buttons and links inside emails (no trailing slash).
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")

# Cloudinary (optional): set CLOUDINARY_URL and `pip install django-cloudinary-storage`
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")
if CLOUDINARY_URL:
    INSTALLED_APPS += ["cloudinary_storage", "cloudinary"]
    STORAGES = {
        "default": {"BACKEND": "cloudinary_storage.storage.MediaCloudinaryStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

# Mobile money (future integration - credentials come from the provider after approval)
MTN_MOMO = {
    "BASE_URL": os.getenv("MTN_MOMO_BASE_URL", "https://sandbox.momodeveloper.mtn.com"),
    "SUBSCRIPTION_KEY": os.getenv("MTN_MOMO_SUBSCRIPTION_KEY", ""),
    "API_USER": os.getenv("MTN_MOMO_API_USER", ""),
    "API_KEY": os.getenv("MTN_MOMO_API_KEY", ""),
    "TARGET_ENV": os.getenv("MTN_MOMO_TARGET_ENV", "sandbox"),
}
AIRTEL_MONEY = {
    "BASE_URL": os.getenv("AIRTEL_BASE_URL", "https://openapiuat.airtel.africa"),
    "CLIENT_ID": os.getenv("AIRTEL_CLIENT_ID", ""),
    "CLIENT_SECRET": os.getenv("AIRTEL_CLIENT_SECRET", ""),
}

# Production security
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
# Limit what one request may send (stops huge form posts filling memory/the database)
DATA_UPLOAD_MAX_NUMBER_FIELDS = 400
DATA_UPLOAD_MAX_NUMBER_FILES = 25
# Only set this to True when the site runs behind your own proxy/load balancer (Nginx, Render, Railway...),
# otherwise visitors could fake their IP address to get around the spam and login limits.
TRUST_X_FORWARDED_FOR = env_bool("TRUST_X_FORWARDED_FOR", False)

# Logging: errors and security warnings are written to logs/ (and the terminal), never shown to visitors.
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "errors_file": {"class": "logging.handlers.RotatingFileHandler", "filename": LOG_DIR / "errors.log",
                        "maxBytes": 5 * 1024 * 1024, "backupCount": 5, "level": "ERROR",
                        "formatter": "plain", "encoding": "utf-8", "delay": True},
        "security_file": {"class": "logging.handlers.RotatingFileHandler", "filename": LOG_DIR / "security.log",
                          "maxBytes": 5 * 1024 * 1024, "backupCount": 5, "level": "WARNING",
                          "formatter": "plain", "encoding": "utf-8", "delay": True},
        "mail_admins": {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR",
                        "filters": ["require_debug_false"]},
    },
    "root": {"handlers": ["console", "errors_file"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console", "errors_file", "mail_admins"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console", "security_file"], "level": "WARNING", "propagate": False},
        "theo.security": {"handlers": ["console", "security_file"], "level": "WARNING", "propagate": False},
    },
}
ADMINS = [("Theo Property", os.getenv("ADMIN_ERROR_EMAIL"))] if os.getenv("ADMIN_ERROR_EMAIL") else []
