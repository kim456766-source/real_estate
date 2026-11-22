"""Favorites stored in the visitor's session: no account needed."""

SESSION_KEY = "favorites"
MAX_FAVORITES = 100


def favorite_ids(request):
    return set(request.session.get(SESSION_KEY, []))


def toggle(request, property_id):
    ids = list(request.session.get(SESSION_KEY, []))
    if property_id in ids:
        ids.remove(property_id)
        added = False
    else:
        ids = ([property_id] + ids)[:MAX_FAVORITES]
        added = True
    request.session[SESSION_KEY] = ids
    return added, len(ids)
