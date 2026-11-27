from django.contrib import admin

from .models import ContactMessage, Inquiry, InquiryMessage


class InquiryMessageInline(admin.TabularInline):
    """The whole conversation (client and agent messages) under each inquiry."""
    model = InquiryMessage
    extra = 0
    fields = ("created_at", "from_client", "author", "body")
    readonly_fields = ("created_at",)
    ordering = ("created_at", "pk")


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "updated_at", "property", "name", "phone", "assigned_agent", "status",
                    "message_count")
    list_filter = ("status", "assigned_agent", "created_at")
    search_fields = ("name", "email", "phone", "message", "thread__body", "property__reference", "property__title")
    date_hierarchy = "created_at"
    ordering = ("-updated_at",)
    list_select_related = ("property", "assigned_agent")
    readonly_fields = ("created_at", "updated_at", "device_info", "client_ip")
    inlines = [InquiryMessageInline]

    @admin.display(description="Messages")
    def message_count(self, obj):
        return obj.thread.count() + 1


@admin.register(InquiryMessage)
class InquiryMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "inquiry", "from_client", "author", "body")
    list_filter = ("from_client", "created_at")
    search_fields = ("body", "inquiry__name", "inquiry__property__reference")
    date_hierarchy = "created_at"
    list_select_related = ("inquiry__property", "author")


admin.site.register(ContactMessage)
