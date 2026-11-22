from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Appointment(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        CONFIRMED = "confirmed", "Confirmed"
        RESCHEDULED = "rescheduled", "Rescheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="appointments")
    # Visitors book without an account; `client` is only set if a logged-in user books
    client = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="appointments")
    name = models.CharField(max_length=120, default="")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, default="")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="agent_appointments", limit_choices_to={"role": "agent"})
    scheduled_for = models.DateTimeField(db_index=True)
    note = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    staff_note = models.TextField(blank=True)

    class Meta:
        ordering = ["scheduled_for"]

    def __str__(self):
        return f"Visit {self.property.reference} on {self.scheduled_for:%Y-%m-%d %H:%M}"
