from datetime import timedelta

from django import forms
from django.utils import timezone

from accounts.forms import BootstrapFormMixin

from .models import Appointment


def validate_future(value):
    if value < timezone.now() + timedelta(hours=1):
        raise forms.ValidationError("Please choose a time at least one hour from now.")



class AppointmentManageForm(BootstrapFormMixin, forms.ModelForm):
    scheduled_for = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))

    class Meta:
        model = Appointment
        fields = ["status", "scheduled_for", "agent", "staff_note"]
        widgets = {"staff_note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not user.is_admin_role:
            self.fields.pop("agent")
