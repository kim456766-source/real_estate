"""Recompute hot deals and trending.  python manage.py update_scores
(Runs automatically too; schedule it hourly in production if you like.)"""
from django.core.management.base import BaseCommand

from properties.models import Property
from properties.ranking import refresh_scores


class Command(BaseCommand):
    help = "Recompute hot deals and trending scores for all properties"

    def handle(self, *args, **opts):
        n = refresh_scores()
        hot = Property.objects.filter(is_hot_deal=True).count()
        trending = Property.objects.filter(is_trending=True).count()
        self.stdout.write(self.style.SUCCESS(f"Scored {n} properties: {hot} hot deals, {trending} trending."))
