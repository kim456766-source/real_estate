from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


def new_meeting_code():
    """Meeting codes are no longer used; this stays only because old migrations (0006, 0007) import it."""
    import secrets
    return "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))


class Inquiry(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In Progress"
        RESPONDED = "responded", "Responded"
        CLOSED = "closed", "Closed"

    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="inquiries")
    client = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="inquiries")
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)  # no longer asked on the website form (phone is); kept for older/API inquiries
    phone = models.CharField(max_length=20, blank=True)
    message = models.TextField(max_length=2000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="assigned_inquiries", limit_choices_to={"role": "agent"})
    response = models.TextField(blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    # Private to the client and the agent. The client's side opens only on the device that sent it:
    # a random key kept in that browser (cookie), stored here hashed. (Not the IP address: on mobile
    # networks many people share one IP, and a phone's IP changes between Wi-Fi and mobile data.)
    device_key = models.CharField(max_length=64, blank=True, db_index=True, editable=False)
    client_ip = models.GenericIPAddressField(null=True, blank=True, editable=False)
    device_info = models.CharField(max_length=120, blank=True, editable=False)  # e.g. "Android phone · Chrome"

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "inquiries"

    def __str__(self):
        return f"Inquiry #{self.pk} on {self.property.reference}"


class InquiryMessage(models.Model):
    """One message in the conversation after the client's first inquiry (client ↔ agent/admin)."""

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="thread")
    from_client = models.BooleanField(default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"{'Client' if self.from_client else 'Agent'} message on inquiry #{self.inquiry_id}"


class ContactMessage(models.Model):
    """General messages from the Contact Us page."""

    name = models.CharField(max_length=120)
    email = models.EmailField()
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=3000)
    is_handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject
