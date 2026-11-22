from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.security import safe_next_url
from properties.models import Property

from . import session


def favorite_list(request):
    ids = session.favorite_ids(request)
    props = (Property.objects.filter(pk__in=ids, approval_status=Property.Approval.APPROVED)
             .select_related("category").prefetch_related("images"))
    return render(request, "favorites/list.html", {"properties": props})


@require_POST
def toggle_favorite(request, pk):
    """Works with Fetch (returns JSON) and with a plain form post (redirects back)."""
    prop = get_object_or_404(Property, pk=pk, approval_status=Property.Approval.APPROVED)
    added, count = session.toggle(request, prop.pk)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"favorited": added, "count": count})
    return redirect(safe_next_url(request, prop.get_absolute_url()))
