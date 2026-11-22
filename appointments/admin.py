from django.contrib import admin

from .models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("property", "client", "agent", "scheduled_for", "status")
    list_filter = ("status",)
