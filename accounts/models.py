from django.contrib.auth.models import AbstractUser
from django.db import models

from core.utils import validate_image_file


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Administrator"
        AGENT = "agent", "Real Estate Agent"
        OWNER = "owner", "Property Owner"
        CLIENT = "client", "Client"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT, db_index=True)
    phone = models.CharField(max_length=20, blank=True)
    photo = models.ImageField(upload_to="users/", blank=True, validators=[validate_image_file])
    district = models.CharField(max_length=60, blank=True)
    bio = models.TextField(blank=True)
    # Set when an administrator creates the account or resets the password: the person signs in with
    # that initial password once and must then choose their own before using the dashboard.
    must_change_password = models.BooleanField(default=False)

    @property
    def is_admin_role(self):
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def is_agent(self):
        return self.role == self.Role.AGENT

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def uses_dashboard(self):
        return self.is_superuser or self.role in (self.Role.ADMIN, self.Role.AGENT, self.Role.OWNER)

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = self.Role.ADMIN
        if self.email:
            self.email = self.email.strip().lower()  # one spelling, so signing in / resetting by email always matches
        super().save(*args, **kwargs)

    def __str__(self):
        return self.get_full_name() or self.username


class AgentProfile(models.Model):
    class Application(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="agent_profile")
    # Self-registered agents start "pending" (account locked) until the super admin decides.
    # Agents created by an admin are approved straight away.
    application_status = models.CharField(max_length=10, choices=Application.choices,
                                          default=Application.APPROVED, db_index=True)
    application_note = models.TextField("Why do you want to join?", blank=True, max_length=1000)
    rejection_reason = models.CharField(max_length=255, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="reviewed_agent_applications")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    agency_name = models.CharField(max_length=120, blank=True)
    license_number = models.CharField(max_length=60, blank=True)
    years_experience = models.PositiveSmallIntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    # Permissions the admin can grant to an agent
    can_publish_directly = models.BooleanField(default=False, help_text="Listings skip admin approval")
    can_manage_appointments = models.BooleanField(default=True)

    def __str__(self):
        return f"Agent: {self.user}"


class OwnerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="owner_profile")
    national_id = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=200, blank=True)
    preferred_agent = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="preferred_by_owners", limit_choices_to={"role": "agent"})

    def __str__(self):
        return f"Owner: {self.user}"
