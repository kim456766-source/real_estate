from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from appointments.forms import validate_future
from appointments.models import Appointment
from favorites.models import Favorite
from inquiries.models import Inquiry
from core.security import clean_text
from properties.models import Category, Property, PropertyImage


class CleanTextMixin:
    """Store plain text only: strips HTML/script tags and hidden characters from every text field sent."""

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        return {k: clean_text(v) if isinstance(v, str) else v for k, v in values.items()}


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "icon"]


class PropertyImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyImage
        fields = ["id", "image", "caption", "is_cover"]


class PropertyListSerializer(serializers.ModelSerializer):
    cover_image = serializers.SerializerMethodField()
    url = serializers.CharField(source="get_absolute_url", read_only=True)

    class Meta:
        model = Property
        fields = ["id", "reference", "title", "slug", "url", "property_type", "purpose", "price", "currency",
                  "district", "sector", "bedrooms", "bathrooms", "parking_spaces", "size_sqm",
                  "latitude", "longitude", "availability", "is_featured", "cover_image"]

    def get_cover_image(self, obj):
        img = obj.cover_image
        if not img:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(img.image.url) if request else img.image.url


class PropertyDetailSerializer(PropertyListSerializer):
    images = PropertyImageSerializer(many=True, read_only=True)
    category = CategorySerializer(read_only=True)
    agent_name = serializers.CharField(source="agent.__str__", read_only=True, default=None)

    class Meta(PropertyListSerializer.Meta):
        fields = PropertyListSerializer.Meta.fields + [
            "description", "category", "village", "address", "year_built", "furnished", "amenities",
            "video_url", "agent_name", "images", "views", "created_at", "updated_at"]


class InquirySerializer(CleanTextMixin, serializers.ModelSerializer):
    class Meta:
        model = Inquiry
        fields = ["id", "property", "name", "email", "phone", "message", "status", "created_at"]
        read_only_fields = ["status", "created_at"]

    def validate_property(self, prop):
        if not prop.is_public:
            raise serializers.ValidationError("Property not available.")
        if prop.is_taken:
            raise serializers.ValidationError(f"This property has already been {prop.get_availability_display().lower()}.")
        return prop


class AppointmentSerializer(CleanTextMixin, serializers.ModelSerializer):
    class Meta:
        model = Appointment
        fields = ["id", "property", "name", "phone", "email", "scheduled_for", "note", "status", "created_at"]
        read_only_fields = ["status", "created_at"]
        extra_kwargs = {"name": {"required": True, "allow_blank": False},
                        "phone": {"required": True, "allow_blank": False}}

    def validate_scheduled_for(self, value):
        try:
            validate_future(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages[0])
        return value

    def validate_property(self, prop):
        if not prop.is_public:
            raise serializers.ValidationError("Property not available.")
        return prop


class FavoriteSerializer(serializers.ModelSerializer):
    property_detail = PropertyListSerializer(source="property", read_only=True)

    class Meta:
        model = Favorite
        fields = ["id", "property", "property_detail", "created_at"]
        read_only_fields = ["created_at"]
