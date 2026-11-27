"""Keep trending scores up to date when listings or client interest change (hot deals are picked by admins)."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from appointments.models import Appointment
from inquiries.models import Inquiry

from .models import Property
from .ranking import SCORE_FIELDS, refresh_scores

IGNORED_UPDATES = set(SCORE_FIELDS) | {"views"}


def _schedule_refresh():
    # Run after the change is committed, so the scores see the saved data (and nothing runs on rollback).
    transaction.on_commit(refresh_scores)


OPEN_INQUIRY = [Inquiry.Status.NEW, Inquiry.Status.IN_PROGRESS, Inquiry.Status.RESPONDED]
OPEN_VISIT = [Appointment.Status.REQUESTED, Appointment.Status.CONFIRMED, Appointment.Status.RESCHEDULED]


@receiver(pre_save, sender=Property)
def remember_agent(sender, instance, **kwargs):
    old = Property.objects.filter(pk=instance.pk).values("agent_id", "availability").first() if instance.pk else None
    instance._old_agent_id = old["agent_id"] if old else None
    instance._old_availability = old["availability"] if old else None


TAKEN_NOTE = ("Update: this property has now been {status}. Thank you for your interest! "
              "Have a look at similar properties on our website.")


def close_conversations_when_taken(prop):
    """The property was sold / rented: tell every client with an open conversation and close it,
    so nobody keeps waiting or asking about it."""
    from inquiries.models import InquiryMessage
    open_inquiries = list(prop.inquiries.filter(status__in=OPEN_INQUIRY))
    if not open_inquiries:
        return
    note = TAKEN_NOTE.format(status=prop.get_availability_display().lower())
    InquiryMessage.objects.bulk_create(
        [InquiryMessage(inquiry=i, from_client=False, author=prop.agent, body=note) for i in open_inquiries])
    prop.inquiries.filter(pk__in=[i.pk for i in open_inquiries]).update(status=Inquiry.Status.CLOSED)


def hand_over_requests(prop):
    """Open client requests follow the property's agent, so only that agent sees them."""
    prop.inquiries.filter(status__in=OPEN_INQUIRY).update(assigned_agent=prop.agent)
    prop.appointments.filter(status__in=OPEN_VISIT).update(agent=prop.agent)


@receiver(post_save, sender=Property)
def property_saved(sender, instance, update_fields=None, **kwargs):
    if not kwargs.get("created") and getattr(instance, "_old_agent_id", None) != instance.agent_id:
        hand_over_requests(instance)
    if (not kwargs.get("created") and instance.is_taken
            and getattr(instance, "_old_availability", None) not in Property.TAKEN):
        close_conversations_when_taken(instance)
    if update_fields and set(update_fields) <= IGNORED_UPDATES:
        return
    _schedule_refresh()


@receiver(post_delete, sender=Property)
def property_deleted(sender, **kwargs):
    _schedule_refresh()


@receiver(post_save, sender=Inquiry)
@receiver(post_save, sender=Appointment)
def interest_created(sender, created, **kwargs):
    if created:
        _schedule_refresh()
