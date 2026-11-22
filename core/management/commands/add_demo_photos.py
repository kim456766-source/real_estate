"""
Attach demo photos to properties that have none:

    python manage.py add_demo_photos <folder>

Files in <folder> are matched by name prefix: house*, apt*, com*, land*.
Each image is re-encoded with Pillow (strips metadata, max 1200 px wide) before saving.
Properties that already have photos are never changed.
"""
from io import BytesIO
from itertools import cycle
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from PIL import Image

from properties.models import Property, PropertyImage

PREFIX_FOR_TYPE = {
    "house": "house", "villa": "house", "apartment": "apt",
    "commercial": "com", "office": "com", "plot": "land", "land": "land",
}
PHOTOS_PER_PROPERTY = 3


def clean_jpeg(path):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1200, 1200))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


class Command(BaseCommand):
    help = "Attach demo photos (by type) to properties without photos"

    def add_arguments(self, parser):
        parser.add_argument("folder")

    def handle(self, folder, **opts):
        folder = Path(folder)
        if not folder.is_dir():
            raise CommandError(f"Folder not found: {folder}")
        pools = {}
        for prefix in set(PREFIX_FOR_TYPE.values()):
            files = sorted(folder.glob(f"{prefix}*.jpg"), key=lambda f: f.name)
            if files:
                pools[prefix] = cycle([(f.stem, clean_jpeg(f)) for f in files])
        interiors = pools.get("apt")

        added = 0
        for prop in Property.objects.filter(images__isnull=True).order_by("pk"):
            pool = pools.get(PREFIX_FOR_TYPE.get(prop.property_type, "house"))
            if not pool:
                continue
            chosen = [next(pool)]
            # Houses/apartments also get interior shots; other types get more of the same kind
            extra = interiors if prop.property_type in ("house", "villa", "apartment") and interiors else pool
            chosen += [next(extra) for _ in range(PHOTOS_PER_PROPERTY - 1)]
            for order, (stem, data) in enumerate(chosen):
                PropertyImage.objects.create(
                    property=prop, is_cover=(order == 0), order=order,
                    image=ContentFile(data, name=f"{prop.reference.lower()}-{order + 1}-{stem}.jpg"))
            added += 1
            self.stdout.write(f"  {prop.reference}  {prop.get_property_type_display():<12} {prop.title[:50]}")
        self.stdout.write(self.style.SUCCESS(f"Added photos to {added} properties."))
