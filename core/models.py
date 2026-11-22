from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Records important administrative actions."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="audit_logs")
    action = models.CharField(max_length=50)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.user} {self.action}"


class SiteSetting(models.Model):
    """Single-row website settings editable from the dashboard."""

    site_name = models.CharField(max_length=100, default="Theo Property")
    tagline = models.CharField(max_length=200, default="Find your next home in Rwanda")
    email = models.EmailField("Support email", default="ntezielyse@gmail.com",
                              help_text="Shown on the website and receives support messages")
    phone = models.CharField(max_length=30, default="+250 789 567 752")
    whatsapp = models.CharField("WhatsApp number", max_length=20, default="250789567752",
                                help_text="Digits only, with country code, e.g. 250789567752")

    def clean(self):
        from django.core.exceptions import ValidationError
        digits = "".join(c for c in self.whatsapp if c.isdigit())
        if digits.startswith("07") and len(digits) == 10:  # 0789... -> 250789...
            digits = "250" + digits[1:]
        if not (digits.startswith("250") and len(digits) == 12):
            raise ValidationError({"whatsapp": "Use the full number with country code, e.g. 250789567752."})
        self.whatsapp = digits

    @property
    def whatsapp_display(self):
        d = self.whatsapp
        return f"+{d[:3]} {d[3:6]} {d[6:9]} {d[9:]}" if len(d) == 12 else d
    address = models.CharField(max_length=200, default="KG 7 Ave, Kigali, Rwanda")
    about = models.TextField(default="Theo Property connects property owners, agents and clients across Rwanda.")
    facebook = models.URLField(blank=True)
    instagram = models.URLField(blank=True)
    x_twitter = models.URLField(blank=True)

    def __str__(self):
        return "Site settings"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
