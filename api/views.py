from django.db import IntegrityError
from rest_framework import mixins, permissions, serializers, viewsets
from rest_framework.authtoken.views import obtain_auth_token  # noqa: F401  (re-exported in urls)

from appointments.models import Appointment
from favorites.models import Favorite
from inquiries.models import Inquiry
from properties.filters import PropertyFilter
from properties.models import Category, Property

from .serializers import (AppointmentSerializer, CategorySerializer, FavoriteSerializer, InquirySerializer,
                          PropertyDetailSerializer, PropertyListSerializer)


class PropertyViewSet(viewsets.ReadOnlyModelViewSet):
    """Public, read-only listing API. Supports all website filters: ?purpose=rent&district=Gasabo&min_price=..."""

    filterset_class = PropertyFilter
    search_fields = ["title", "description", "district", "sector"]
    ordering_fields = ["price", "created_at", "size_sqm", "views"]
    lookup_field = "slug"

    def get_queryset(self):
        return (Property.objects.filter(approval_status=Property.Approval.APPROVED)
                .exclude(availability__in=Property.TAKEN)  # sold / rented: not on offer any more
                .select_related("category", "agent").prefetch_related("images"))

    def get_serializer_class(self):
        return PropertyDetailSerializer if self.action == "retrieve" else PropertyListSerializer


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    pagination_class = None


class InquiryViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Anyone may submit an inquiry; authenticated users can list their own."""

    serializer_class = InquirySerializer
    filter_backends = []

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return Inquiry.objects.filter(client=self.request.user)

    def perform_create(self, serializer):
        prop = serializer.validated_data["property"]
        client = self.request.user if self.request.user.is_authenticated else None
        serializer.save(client=client, assigned_agent=prop.agent)


class AppointmentViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Anyone can request a visit with name + phone; authenticated users can list their own."""

    serializer_class = AppointmentSerializer
    filter_backends = []

    def get_permissions(self):
        if self.action == "create":
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return Appointment.objects.filter(client=self.request.user)

    def perform_create(self, serializer):
        prop = serializer.validated_data["property"]
        client = self.request.user if self.request.user.is_authenticated else None
        serializer.save(client=client, agent=prop.agent)


class FavoriteViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.DestroyModelMixin,
                      viewsets.GenericViewSet):
    serializer_class = FavoriteSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = []

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user).select_related("property")

    def perform_create(self, serializer):
        try:
            serializer.save(user=self.request.user)
        except IntegrityError:
            raise serializers.ValidationError({"property": "Already in favorites."})
