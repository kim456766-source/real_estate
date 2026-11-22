import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from core.models import TimeStampedModel


class Payment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank Transfer"
        MTN_MOMO = "mtn_momo", "MTN Mobile Money"
        AIRTEL = "airtel", "Airtel Money"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class Purpose(models.TextChoices):
        PURCHASE = "purchase", "Property Purchase / Deposit"
        RENT = "rent", "Rent Payment"
        LISTING_FEE = "listing_fee", "Listing Fee"
        OTHER = "other", "Other"

    MOBILE_METHODS = {Method.MTN_MOMO, Method.AIRTEL}

    reference = models.CharField(max_length=20, unique=True, editable=False)
    client = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="payments")
    payer_name = models.CharField(max_length=120, default="")
    payer_email = models.EmailField(blank=True)
    property = models.ForeignKey("properties.Property", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(1)])
    currency = models.CharField(max_length=3, default="RWF")
    method = models.CharField(max_length=10, choices=Method.choices)
    purpose = models.CharField(max_length=12, choices=Purpose.choices, default=Purpose.OTHER)
    payer_phone = models.CharField(max_length=20, blank=True, help_text="Mobile money number (2507XXXXXXXX)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Provider data - filled only from the payment provider's response
    provider_transaction_id = models.CharField(max_length=100, blank=True)
    provider_confirmed = models.BooleanField(default=False, editable=False)
    provider_response = models.JSONField(default=dict, blank=True, editable=False)
    # Manual verification (cash / bank) by staff
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="verified_payments")
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="recorded_payments")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = "PAY-" + uuid.uuid4().hex[:10].upper()
        self.full_clean_status()
        super().save(*args, **kwargs)

    def full_clean_status(self):
        """A payment may only be successful when it has been confirmed."""
        if self.status != self.Status.SUCCESSFUL:
            return
        if self.method in self.MOBILE_METHODS and not self.provider_confirmed:
            raise ValidationError("Mobile money payments become successful only after provider confirmation.")
        if self.method not in self.MOBILE_METHODS and not self.verified_by_id:
            raise ValidationError("Cash/bank payments must be verified by a staff member.")

    def __str__(self):
        return f"{self.reference} {self.amount:,.0f} {self.currency}"
