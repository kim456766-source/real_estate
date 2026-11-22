from django.contrib import admin

from .models import Category, Property, PropertyImage


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("reference", "title", "property_type", "purpose", "price", "district",
                    "availability", "approval_status", "is_featured")
    list_filter = ("approval_status", "availability", "property_type", "purpose", "district")
    search_fields = ("reference", "title", "district", "sector")
    list_editable = ("approval_status", "is_featured")
    inlines = [PropertyImageInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
