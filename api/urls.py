from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("properties", views.PropertyViewSet, basename="property")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("inquiries", views.InquiryViewSet, basename="inquiry")
router.register("appointments", views.AppointmentViewSet, basename="appointment")
router.register("favorites", views.FavoriteViewSet, basename="favorite")

app_name = "api"

urlpatterns = [
    path("", include(router.urls)),
    path("auth/token/", obtain_auth_token, name="token"),
]
