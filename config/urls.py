from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts import google

urlpatterns = [
    path("admin/", admin.site.urls),
    # Sign in with Google (staff only). The callback address must match the one set in Google Cloud.
    path("auth/google/", google.google_start, name="google_login"),
    path("auth/google/callback/", google.google_callback, name="google_callback"),
    path("", include("pages.urls")),
    path("accounts/", include("accounts.urls")),
    path("properties/", include("properties.urls")),
    path("inquiries/", include("inquiries.urls")),
    path("appointments/", include("appointments.urls")),
    path("favorites/", include("favorites.urls")),
    path("notifications/", include("notifications.urls")),
    path("blog/", include("blog.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("api/v1/", include("api.urls")),
    path("api-auth/", include("rest_framework.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
