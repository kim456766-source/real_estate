from django.conf import settings
from django.db import models


class Document(models.Model):
    """Private file library (contracts, title deeds, IDs, forms...). Files are stored in the database,
    not on a public photo service, so only signed-in staff with access can open them.
    Admins see every document; agents see documents shared with all staff and the ones attached to
    their own listings."""

    class Category(models.TextChoices):
        CONTRACT = "contract", "Contract / agreement"
        TITLE = "title", "Title deed / land document"
        ID = "id", "ID / passport"
        PAYMENT = "payment", "Receipt / payment proof"
        FORM = "form", "Form / template"
        PLAN = "plan", "Plan / map"
        OTHER = "other", "Other"

    class Visibility(models.TextChoices):
        ADMINS = "admins", "Admins only"
        STAFF = "staff", "Admins and agents"

    MAX_BYTES = 10 * 1024 * 1024
    ALLOWED = {
        ".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls": "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain", ".csv": "text/csv",
    }

    title = models.CharField(max_length=150)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.OTHER, db_index=True)
    property = models.ForeignKey("properties.Property", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="documents", help_text="Optional: the listing this document is about")
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.ADMINS)
    notes = models.CharField(max_length=300, blank=True)
    file_name = models.CharField(max_length=200, editable=False)
    content_type = models.CharField(max_length=120, editable=False)
    size = models.PositiveIntegerField(editable=False)
    data = models.BinaryField(editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def extension(self):  # (plain method: the "property" field hides the @property decorator here)
        return self.file_name.rsplit(".", 1)[-1].upper() if "." in self.file_name else ""


class ProblemReport(models.Model):
    """'Report a problem': from visitors (on a listing or the website) or staff (Help page).
    Admins see them all under Reports > Problem reports."""

    class Kind(models.TextChoices):
        SCAM = "scam", "Fake listing / scam"
        WRONG = "wrong", "Wrong information (price, location, photos)"
        TAKEN = "taken", "Already sold or rented"
        AGENT = "agent", "Problem with an agent"
        OFFENSIVE = "offensive", "Offensive or inappropriate content"
        WEBSITE = "website", "Website not working"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    property = models.ForeignKey("properties.Property", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="problem_reports")
    message = models.TextField(max_length=2000)
    name = models.CharField(max_length=120, blank=True)
    contact = models.CharField("Phone or email", max_length=120, blank=True)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    page = models.CharField(max_length=200, blank=True)
    device_info = models.CharField(max_length=120, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_note = models.CharField(max_length=300, blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} ({self.get_status_display()})"
