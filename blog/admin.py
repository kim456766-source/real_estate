from django.contrib import admin

from .models import Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "author", "is_published", "created_at")
    list_filter = ("category", "is_published")
    prepopulated_fields = {"slug": ("title",)}
