import logging

from django.db import DatabaseError

from .models import PropertyImage

log = logging.getLogger("theo.uploads")

PHOTO_UPLOAD_FAILED = ("The photos could not be uploaded right now, so nothing was saved. "
                       "Please try again in a moment. If it keeps happening, contact the site administrator.")


class PhotoUploadError(Exception):
    """The photo storage (Cloudinary) refused or failed an upload."""


def save_property_images(prop, files):
    """Attach uploaded images; the first image becomes the cover if there isn't one.

    Raises PhotoUploadError (after logging the real cause) when the photo storage fails, so the
    caller can roll back and show a clear message instead of an error page."""
    has_cover = prop.images.filter(is_cover=True).exists()
    start = prop.images.count()
    for i, f in enumerate(files):
        try:
            PropertyImage.objects.create(property=prop, image=f, is_cover=(not has_cover and i == 0), order=start + i)
        except DatabaseError:
            raise
        except Exception as exc:
            log.exception("Photo upload failed for %s (%s)", prop.reference, getattr(f, "name", "?"))
            raise PhotoUploadError(str(exc)) from exc


def digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


def same_phone(a, b):
    """Compare Rwandan numbers written as 07..., 2507..., +250 7..."""
    a, b = digits(a), digits(b)
    return bool(a) and bool(b) and a[-9:] == b[-9:]
