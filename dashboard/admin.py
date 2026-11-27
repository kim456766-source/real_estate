from django.contrib import admin

from .models import Document, ProblemReport


@admin.register(ProblemReport)
class ProblemReportAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind", "status", "property", "name", "contact", "handled_by")
    list_filter = ("status", "kind", "created_at")
    search_fields = ("message", "name", "contact", "property__reference")
    date_hierarchy = "created_at"


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "property", "visibility", "file_name", "uploaded_by", "created_at")
    list_filter = ("category", "visibility")
    search_fields = ("title", "file_name", "property__reference")
    exclude = ("data",)  # open documents from the dashboard (Documents), not here
