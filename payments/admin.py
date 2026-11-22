from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("reference", "client", "property", "amount", "currency", "method", "status", "created_at")
    list_filter = ("status", "method", "purpose")
    search_fields = ("reference", "client__username", "provider_transaction_id")
    readonly_fields = ("reference", "provider_confirmed", "provider_response")
