from .models import PropertyImage


def save_property_images(prop, files):
    """Attach uploaded images; the first image becomes the cover if there isn't one."""
    has_cover = prop.images.filter(is_cover=True).exists()
    start = prop.images.count()
    for i, f in enumerate(files):
        PropertyImage.objects.create(property=prop, image=f, is_cover=(not has_cover and i == 0), order=start + i)


def digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


def same_phone(a, b):
    """Compare Rwandan numbers written as 07..., 2507..., +250 7..."""
    a, b = digits(a), digits(b)
    return bool(a) and bool(b) and a[-9:] == b[-9:]
