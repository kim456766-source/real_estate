from django.contrib import admin

from .models import ContactMessage, Inquiry


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("id", "property", "name", "email", "status", "assigned_agent", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "email", "property__reference")


admin.site.register(ContactMessage)
