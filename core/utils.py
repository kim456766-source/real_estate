from django.conf import settings
from django.core.exceptions import ValidationError

from .models import AuditLog
from .security import client_ip  # noqa: F401  (kept here for existing imports)

ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "MPO"}  # MPO = JPEG from some phone cameras
MAX_JPEG_PIXELS = 260_000_000   # JPEG: up to 200+ MP phone photos (decoded at reduced size, see PropertyImage)
MAX_IMAGE_PIXELS = 40_000_000   # PNG / WEBP are decoded in full, so keep these to normal photo sizes


def validate_image_file(f):
    """Reject oversized, renamed or fake images by opening the real file content with Pillow."""
    from PIL import Image, UnidentifiedImageError

    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError("Only JPG, PNG and WEBP images are allowed.")
    limit = settings.MAX_IMAGE_SIZE_MB
    if limit and f.size > limit * 1024 * 1024:  # 0 = no limit
        raise ValidationError(f"Image must be smaller than {limit} MB.")
    if getattr(f, "_committed", False):  # already stored earlier; nothing new to check
        return
    Image.MAX_IMAGE_PIXELS = MAX_JPEG_PIXELS  # Pillow's own bomb check; the per-format limit is below
    try:
        pos = f.tell() if hasattr(f, "tell") else 0
        f.seek(0)
        with Image.open(f) as img:
            fmt, (w, h) = img.format, img.size
            img.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError("This file is not a valid image.")
    finally:
        try:
            f.seek(pos)
        except Exception:
            pass
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Only JPG, PNG and WEBP images are allowed.")
    if w * h > (MAX_JPEG_PIXELS if fmt in ("JPEG", "MPO") else MAX_IMAGE_PIXELS):
        raise ValidationError("This image is too large. Please upload a normal photo.")


def log_action(request, action, obj=None, description=""):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        target_type=obj.__class__.__name__ if obj is not None else "",
        target_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        description=description or (str(obj) if obj is not None else ""),
        ip_address=client_ip(request),
    )
