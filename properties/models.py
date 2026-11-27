import logging
import os
import uuid
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from core.models import TimeStampedModel
from core.utils import validate_image_file


class Category(models.Model):
    """Property categories: Residential, Commercial, Land, ... (managed in Settings)."""

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=70, unique=True)
    icon = models.CharField(max_length=40, default="fa-house", help_text="Font Awesome icon class")
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Property(TimeStampedModel):
    class PropertyType(models.TextChoices):
        HOUSE = "house", "House"
        APARTMENT = "apartment", "Apartment"
        PLOT = "plot", "Plot"
        LAND = "land", "Land"
        COMMERCIAL = "commercial", "Commercial Building"
        OFFICE = "office", "Office"
        VILLA = "villa", "Villa"

    class Purpose(models.TextChoices):
        SALE = "sale", "For Sale"
        RENT = "rent", "For Rent"

    class Availability(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        SOLD = "sold", "Sold"
        RENTED = "rented", "Rented"

    TAKEN = ("sold", "rented")  # no longer on offer: hidden from listings, no new questions

    class Approval(models.TextChoices):
        PENDING = "pending", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        INACTIVE = "inactive", "Deactivated"

    class Currency(models.TextChoices):
        RWF = "RWF", "RWF"
        USD = "USD", "USD"

    reference = models.CharField(max_length=12, unique=True, editable=False)
    title = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, editable=False)
    description = models.TextField()
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="properties")
    property_type = models.CharField(max_length=20, choices=PropertyType.choices, db_index=True)
    purpose = models.CharField(max_length=10, choices=Purpose.choices, db_index=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.RWF)
    rent_period = models.CharField(max_length=20, blank=True, help_text="e.g. month, night (rentals only)")

    # Location (Rwanda administrative units)
    district = models.CharField(max_length=60, blank=True, db_index=True)  # optional; older listings only
    sector = models.CharField(max_length=60, blank=True)
    village = models.CharField(max_length=60, blank=True)
    address = models.CharField(max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                   validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
                                    validators=[MinValueValidator(-180), MaxValueValidator(180)])

    bedrooms = models.PositiveSmallIntegerField(default=0)
    bathrooms = models.PositiveSmallIntegerField(default=0)
    parking_spaces = models.PositiveSmallIntegerField(default=0)
    size_sqm = models.PositiveIntegerField("Size (m²)", null=True, blank=True)
    year_built = models.PositiveSmallIntegerField(null=True, blank=True)
    furnished = models.BooleanField(default=False)
    amenities = models.CharField(max_length=300, blank=True, help_text="Comma separated, e.g. Garden, Pool, Security")
    video_url = models.URLField(blank=True)

    class Source(models.TextChoices):
        WEBSITE = "website", "Owner submission (website)"
        DASHBOARD = "dashboard", "Added by staff"

    # Owner contact details: owners don't need an account
    owner_name = models.CharField(max_length=120, blank=True)
    owner_phone = models.CharField(max_length=20, blank=True)
    owner_email = models.EmailField(blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.DASHBOARD, db_index=True)

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="owned_properties")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="agent_properties", limit_choices_to={"role": "agent"})
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name="created_properties")

    availability = models.CharField(max_length=10, choices=Availability.choices, default=Availability.AVAILABLE,
                                    db_index=True)
    approval_status = models.CharField(max_length=10, choices=Approval.choices, default=Approval.PENDING,
                                       db_index=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    is_featured = models.BooleanField(default=False)
    views = models.PositiveIntegerField(default=0)

    # Hot deal: chosen by an administrator (not computed from prices; prices are set by the agent, owner or
    # admin and never discounted). It shows at the top of the website until this date and time.
    hot_deal_until = models.DateTimeField("Hot deal until", null=True, blank=True, db_index=True)
    # When the listing was (re)posted: "New" badge, ordering and the 30-day "check this listing" reminder.
    # "Repost" sets it to now, so an older listing that is still on offer comes back as new.
    listed_at = models.DateTimeField(default=timezone.now, db_index=True, editable=False)
    reminder_sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Computed automatically by properties/ranking.py (never edited by hand)
    trend_score = models.FloatField(default=0, db_index=True, editable=False)
    is_trending = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "properties"
        indexes = [
            models.Index(fields=["approval_status", "availability"]),
            models.Index(fields=["purpose", "property_type"]),
            models.Index(fields=["price"]),
        ]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = "TP-" + uuid.uuid4().hex[:8].upper()
        if not self.slug:
            self.slug = f"{slugify(self.title)[:150]}-{self.reference.lower()}"
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("properties:detail", args=[self.slug])

    @property
    def cover_image(self):
        images = list(self.images.all())
        for img in images:
            if img.is_cover:
                return img
        return images[0] if images else None

    @property
    def is_taken(self):
        return self.availability in self.TAKEN

    @property
    def taken_status(self):
        """What 'taken' means for this listing: sold (for sale) or rented (for rent)."""
        return self.Availability.RENTED if self.purpose == self.Purpose.RENT else self.Availability.SOLD

    @property
    def location_display(self):
        return ", ".join(p for p in [self.sector, self.district, "Rwanda"] if p)

    @property
    def amenity_list(self):
        return [a.strip() for a in self.amenities.split(",") if a.strip()]

    LAND_TYPES = ("land", "plot")

    @property
    def is_new(self):
        return bool(self.listed_at) and self.listed_at >= timezone.now() - timedelta(days=settings.NEW_LISTING_DAYS)

    @property
    def is_hot_deal(self):
        """Picked by an administrator, still within its time, and still on offer."""
        return (bool(self.hot_deal_until) and self.hot_deal_until > timezone.now()
                and self.availability == self.Availability.AVAILABLE)

    @classmethod
    def hot_deal_q(cls):
        return models.Q(hot_deal_until__gt=timezone.now(), availability=cls.Availability.AVAILABLE)

    @property
    def listed_days(self):
        return (timezone.now() - self.listed_at).days if self.listed_at else 0

    @property
    def is_land(self):
        """Plots and land have no rooms."""
        return self.property_type in self.LAND_TYPES

    @property
    def is_public(self):
        return self.approval_status == self.Approval.APPROVED

    @property
    def contact_person(self):
        """Clients contact the agent advertising the property (owner details stay private)."""
        return self.agent

    @property
    def owner_display(self):
        return self.owner_name or (str(self.owner) if self.owner else "")

    def __str__(self):
        return f"{self.reference} - {self.title}"


class PropertyImage(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="properties/%Y/%m/", validators=[validate_image_file])
    caption = models.CharField(max_length=120, blank=True)
    is_cover = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_cover", "order", "id"]

    def __str__(self):
        return f"Image for {self.property.reference}"

    MAX_SIDE = 1600

    def save(self, *args, **kwargs):
        if self.image and not getattr(self.image, "_committed", True):
            self._normalize()
        super().save(*args, **kwargs)

    def _normalize(self):
        """Turn phone photos upright (EXIF), shrink huge ones and save as web-friendly JPEG."""
        from PIL import Image, ImageOps
        from core.utils import MAX_JPEG_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_JPEG_PIXELS
        try:
            self.image.seek(0)
            img = Image.open(self.image)
            # Big phone JPEGs: decode at 1/2, 1/4 or 1/8 size straight away (uses a fraction of the memory)
            img.draft("RGB", (self.MAX_SIDE, self.MAX_SIDE))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((self.MAX_SIDE, self.MAX_SIDE))
            if img.mode != "RGB":
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True, progressive=True)
        except Exception:  # unreadable image: keep the original, the validator already checked it
            logging.getLogger("theo.uploads").warning("Could not normalise image %s", self.image.name, exc_info=True)
            return
        name = os.path.splitext(os.path.basename(self.image.name))[0] + ".jpg"
        self.image = ContentFile(out.getvalue(), name=name)
