from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from .models import Post


def post_list(request):
    posts = Post.objects.filter(is_published=True).select_related("author")
    cat = request.GET.get("category")
    if cat:
        posts = posts.filter(category=cat)
    page = Paginator(posts, 6).get_page(request.GET.get("page"))
    return render(request, "blog/list.html", {"page_obj": page, "categories": Post.Category.choices, "current": cat})


def post_detail(request, slug):
    post = get_object_or_404(Post, slug=slug, is_published=True)
    recent = Post.objects.filter(is_published=True).exclude(pk=post.pk)[:4]
    return render(request, "blog/detail.html", {"post": post, "recent": recent})
