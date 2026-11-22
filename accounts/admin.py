from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import AgentProfile, OwnerProfile, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (("Theo Property", {"fields": ("role", "phone", "photo", "district", "bio")}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("Theo Property", {"fields": ("email", "role", "phone")}),)


admin.site.register(AgentProfile)
admin.site.register(OwnerProfile)
