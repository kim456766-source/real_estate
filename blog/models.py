from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from core.models import TimeStampedModel
from core.utils import validate_image_file


class Post(TimeStampedModel):
    class Category(models.TextChoices):
        NEWS = "news", "Market News"
        ADVICE = "advice", "Buying & Renting Advice"
        INVEST = "invest", "Investment"
        COMPANY = "company", "Company Updates"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.NEWS)
    excerpt = models.CharField(max_length=300, blank=True)
    body = models.TextField()
    cover = models.ImageField(upload_to="blog/", blank=True, validators=[validate_image_file])
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="posts")
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "post"
            slug, n = base, 2
            while Post.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("blog:detail", args=[self.slug])

    def __str__(self):
        return self.title
