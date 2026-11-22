import secrets

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out over the phone


def new_meeting_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


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
    # Shown on the property page as public Q&A (first name only, never phone/email). New website inquiries
    # are public; agents/admins can hide or show them. Older conversations stay private.
    is_public = models.BooleanField(default=False, db_index=True)
    # Private proof of identity: shown only to the client (their "Agent replies" panel) and to staff.
    # Before meeting, the agent asks for it, so nobody can pretend to be this client.
    # It is also the key the client pastes to open the conversation on "My conversations", so it is unique.
    meeting_code = models.CharField(max_length=6, default=new_meeting_code, editable=False, unique=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "inquiries"

    def __str__(self):
        return f"Inquiry #{self.pk} on {self.property.reference}"

    def save(self, *args, **kwargs):
        if not self.pk:  # (very rare) clash with an existing code: pick another
            while Inquiry.objects.filter(meeting_code=self.meeting_code).exists():
                self.meeting_code = new_meeting_code()
        super().save(*args, **kwargs)

    def public_name(self):  # (a plain method: the "property" field shadows the @property decorator here)
        """What other visitors see: an initial only ("K."), so there is no name to pretend to be."""
        name = (self.name or "").strip()
        return f"{name[0].upper()}." if name else "A client"


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
