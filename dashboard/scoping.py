"""Role-based data scoping: what each dashboard user is allowed to see and do."""
from django.db.models import Q

from appointments.models import Appointment
from inquiries.models import Inquiry
from properties.models import Property


def properties_for(user):
    """Admins monitor every property. Each agent only sees the listings assigned to them."""
    qs = Property.objects.select_related("agent", "category", "created_by").prefetch_related("images")
    if user.is_admin_role:
        return qs
    if user.is_agent:
        return qs.filter(agent=user)
    if user.is_owner:
        return qs.filter(Q(owner=user) | Q(created_by=user))
    return qs.none()


def inquiries_for(user):
    qs = Inquiry.objects.select_related("property", "assigned_agent")
    if user.is_admin_role:
        return qs
    if user.is_agent:
        # Private: only requests for properties this agent handles (or that an admin gave them).
        # Other agents never see these clients, even though they can all see the listings.
        return qs.filter(Q(assigned_agent=user) | Q(property__agent=user))
    if user.is_owner:
        return qs.filter(property__owner=user)
    return qs.none()


def appointments_for(user):
    qs = Appointment.objects.select_related("property", "agent")
    if user.is_admin_role:
        return qs
    if user.is_agent:
        return qs.filter(Q(agent=user) | Q(property__agent=user))
    if user.is_owner:
        return qs.filter(property__owner=user)
    return qs.none()


def can_edit_property(user, prop):
    if user.is_admin_role:
        return True
    if user.is_agent:
        return prop.agent_id == user.pk  # only their own listings
    return user in (prop.owner, prop.created_by)


def can_publish(user, prop):
    if user.is_admin_role:
        return True
    profile = getattr(user, "agent_profile", None) if user.is_agent else None
    return bool(profile and profile.can_publish_directly and prop.agent_id == user.pk)


def can_manage_appointments(user):
    if user.is_admin_role:
        return True
    return user.is_agent and getattr(getattr(user, "agent_profile", None), "can_manage_appointments", False)
