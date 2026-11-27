"""
Load demo data:  python manage.py seed_demo
Creates categories, staff accounts (admin + 2 agents), sample properties with owner contacts
(2 unclaimed owner submissions), an inquiry, an appointment and blog posts.
Safe to run more than once.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import User
from appointments.models import Appointment
from blog.models import Post
from inquiries.models import Inquiry
from properties.models import Category, Property
from properties.ranking import refresh_scores

DEMO_PASSWORD = "TheoDemo#2026"

CATEGORIES = [
    ("Residential", "fa-house"), ("Apartments", "fa-building"), ("Land & Plots", "fa-map"),
    ("Commercial", "fa-shop"), ("Offices", "fa-briefcase"), ("Luxury", "fa-gem"),
]

PROPERTIES = [
    dict(title="Modern 4-bedroom house in Kibagabaga", property_type="house", purpose="sale", price=120_000_000,
         district="Gasabo", sector="Kimironko", village="Kibagabaga", bedrooms=4, bathrooms=3, parking_spaces=2,
         size_sqm=350, lat="-1.9325", lng="30.1131", cat="Residential", featured=True,
         amenities="Garden, Security, Water tank, Solar water heater, Servant quarter"),
    dict(title="Furnished 2-bedroom apartment in Kimihurura", property_type="apartment", purpose="rent", price=900_000,
         rent_period="month", district="Gasabo", sector="Kimihurura", bedrooms=2, bathrooms=2, parking_spaces=1,
         size_sqm=110, lat="-1.9540", lng="30.0930", cat="Apartments", featured=True, furnished=True,
         amenities="Elevator, Backup generator, Gym, Wi-Fi, 24h security"),
    dict(title="Residential plot with view in Rebero", property_type="land", purpose="sale", price=35_000_000,
         district="Kicukiro", sector="Gatenga", village="Rebero", size_sqm=600, lat="-1.9905", lng="30.0702",
         cat="Land & Plots", featured=True, amenities="Title deed, Road access, Electricity nearby"),
    dict(title="Commercial building on KN 4 Ave, CBD", property_type="commercial", purpose="sale", price=850_000_000,
         district="Nyarugenge", sector="Nyarugenge", size_sqm=1800, parking_spaces=20, bathrooms=12,
         lat="-1.9479", lng="30.0588", cat="Commercial", amenities="Elevator, Parking, Generator, Fibre internet"),
    dict(title="Open-plan office space in Kacyiru", property_type="office", purpose="rent", price=2_500,
         currency="USD", rent_period="month", district="Gasabo", sector="Kacyiru", size_sqm=240, bathrooms=2,
         parking_spaces=4, lat="-1.9395", lng="30.0870", cat="Offices", amenities="Reception, Meeting room, Backup power"),
    dict(title="Luxury villa with pool in Nyarutarama", property_type="villa", purpose="sale", price=480_000_000,
         district="Gasabo", sector="Remera", village="Nyarutarama", bedrooms=6, bathrooms=6, parking_spaces=4,
         size_sqm=780, lat="-1.9440", lng="30.1050", cat="Luxury", featured=True, furnished=True,
         amenities="Swimming pool, Garden, CCTV, Staff quarters, Rooftop terrace"),
    dict(title="Family house near Lake Kivu", property_type="house", purpose="sale", price=95_000_000,
         district="Rubavu", sector="Gisenyi", bedrooms=3, bathrooms=2, parking_spaces=2, size_sqm=280,
         lat="-1.6990", lng="29.2560", cat="Residential", amenities="Lake view, Garden, Fence"),
    dict(title="Affordable 3-bedroom house in Musanze town", property_type="house", purpose="rent", price=350_000,
         rent_period="month", district="Musanze", sector="Muhoza", bedrooms=3, bathrooms=1, parking_spaces=1,
         size_sqm=160, lat="-1.4990", lng="29.6340", cat="Residential", amenities="Water, Electricity, Fence"),
    dict(title="Studio apartment near University of Rwanda, Huye", property_type="apartment", purpose="rent",
         price=150_000, rent_period="month", district="Huye", sector="Ngoma", bedrooms=1, bathrooms=1, size_sqm=40,
         lat="-2.6060", lng="29.7400", cat="Apartments", furnished=True, amenities="Wi-Fi, Water, Security"),
    dict(title="Agricultural land in Bugesera", property_type="land", purpose="sale", price=18_000_000,
         district="Bugesera", sector="Nyamata", size_sqm=5000, lat="-2.1490", lng="30.0920",
         cat="Land & Plots", amenities="Title deed, Near new airport road"),
]

# Small local markets (several similar listings in the same sector), for search, similar listings and trending.
MARKET_PROPERTIES = [
    # Houses for sale in Kimironko (~340,000 RWF/m²)
    dict(title="4-bedroom house in Kimironko", property_type="house", purpose="sale", price=110_000_000, size_sqm=320,
         bedrooms=4, bathrooms=3, sector="Kimironko"),
    dict(title="5-bedroom house in Kimironko", property_type="house", purpose="sale", price=135_000_000, size_sqm=400,
         bedrooms=5, bathrooms=4, sector="Kimironko"),
    dict(title="3-bedroom house in Kimironko", property_type="house", purpose="sale", price=85_000_000, size_sqm=250,
         bedrooms=3, bathrooms=2, sector="Kimironko"),
    dict(title="Spacious family house in Kimironko – owner relocating", property_type="house", purpose="sale",
         price=88_000_000, size_sqm=360, bedrooms=4, bathrooms=3, sector="Kimironko"),
    # Apartments for rent in Kimihurura (~8,500 RWF/m² per month)
    dict(title="3-bedroom apartment in Kimihurura", property_type="apartment", purpose="rent", price=1_200_000,
         rent_period="month", size_sqm=140, bedrooms=3, bathrooms=2, sector="Kimihurura"),
    dict(title="2-bedroom apartment with city view, Kimihurura", property_type="apartment", purpose="rent",
         price=950_000, rent_period="month", size_sqm=105, bedrooms=2, bathrooms=2, sector="Kimihurura"),
    dict(title="Modern 2-bedroom apartment in Kimihurura", property_type="apartment", purpose="rent",
         price=650_000, rent_period="month", size_sqm=115, bedrooms=2, bathrooms=2, sector="Kimihurura"),
    # Plots for sale in Gatenga (~60,000 RWF/m²)
    dict(title="500 m² plot in Gatenga", property_type="plot", purpose="sale", price=30_000_000, size_sqm=500,
         sector="Gatenga"),
    dict(title="450 m² plot in Gatenga", property_type="plot", purpose="sale", price=27_000_000, size_sqm=450,
         sector="Gatenga"),
    dict(title="700 m² plot in Gatenga", property_type="plot", purpose="sale", price=40_000_000, size_sqm=700,
         sector="Gatenga"),
    dict(title="Corner plot in Gatenga – quick sale", property_type="plot", purpose="sale", price=26_000_000,
         size_sqm=600, sector="Gatenga"),
]

POSTS = [
    ("How to verify a land title in Rwanda before you buy", "advice",
     "Before paying for a plot, check the UPI (Unique Parcel Identifier) with the land registry, confirm the seller's "
     "identity matches the title, and look for mortgages or caveats on the parcel.\n\nAlways sign the sale agreement "
     "in front of a notary and complete the transfer at the land office."),
    ("Kigali rental market: what tenants should expect", "news",
     "Demand for 2- and 3-bedroom apartments in Kimihurura, Kacyiru and Kicukiro remains strong. Furnished units "
     "command a premium, while new developments in Gahanga and Kanombe offer more affordable options."),
    ("Five things investors look for in a commercial property", "invest",
     "Location and access, parking, reliable power and water, flexible floor plans, and a clear title. "
     "Buildings near transport corridors and the CBD continue to attract stable tenants."),
]


class Command(BaseCommand):
    help = "Load demo data for Theo Property"

    def user(self, username, role, first, last, **extra):
        u, created = User.objects.get_or_create(
            username=username,
            defaults=dict(email=f"{username}@theoproperty.test", role=role, first_name=first, last_name=last, **extra))
        if created:
            u.set_password(DEMO_PASSWORD)
            u.save()
        return u

    def handle(self, *args, **opts):
        cats = {}
        for name, icon in CATEGORIES:
            cats[name], _ = Category.objects.get_or_create(name=name, defaults={"icon": icon})

        admin = self.user("admin", User.Role.ADMIN, "Theo", "Admin", is_staff=True, is_superuser=True)
        agent = self.user("agent", User.Role.AGENT, "Aline", "Uwase", phone="+250788111222")
        agent.agent_profile.agency_name = "Theo Property Agency"
        agent.agent_profile.is_verified = True
        agent.agent_profile.can_publish_directly = True
        agent.agent_profile.save()
        agent2 = self.user("agent2", User.Role.AGENT, "Eric", "Nkurunziza", phone="+250788777888")
        owners = [("Jean Habimana", "0788333444", "jean.owner@example.com"),
                  ("Claudine Mukeshimana", "0722444555", ""),
                  ("Patrick Niyonzima", "0733666777", "patrick@example.com")]

        if not Property.objects.exists():
            for i, d in enumerate(PROPERTIES):
                owner_name, owner_phone, owner_email = owners[i % len(owners)]
                submitted = i >= 8  # last two arrive as public owner submissions, not yet claimed
                Property.objects.create(
                    title=d["title"],
                    description=(f"{d['title']}. Located in {d.get('sector', '')}, {d['district']} district. "
                                 "Contact our agent to arrange a viewing. Documents available on request."),
                    category=cats[d["cat"]], property_type=d["property_type"], purpose=d["purpose"],
                    price=Decimal(d["price"]), currency=d.get("currency", "RWF"), rent_period=d.get("rent_period", ""),
                    district=d["district"], sector=d.get("sector", ""), village=d.get("village", ""),
                    address=f"{d.get('sector', '')}, {d['district']}, Rwanda",
                    latitude=Decimal(d["lat"]), longitude=Decimal(d["lng"]),
                    bedrooms=d.get("bedrooms", 0), bathrooms=d.get("bathrooms", 0),
                    parking_spaces=d.get("parking_spaces", 0), size_sqm=d.get("size_sqm"),
                    furnished=d.get("furnished", False), amenities=d.get("amenities", ""),
                    owner_name=owner_name, owner_phone=owner_phone, owner_email=owner_email,
                    source=Property.Source.WEBSITE if submitted else Property.Source.DASHBOARD,
                    agent=None if submitted else (agent if i % 2 == 0 else agent2),
                    created_by=None if submitted else agent,
                    approval_status=Property.Approval.PENDING if submitted else Property.Approval.APPROVED,
                    is_featured=d.get("featured", False), views=(i * 37) % 200,
                )
            first = Property.objects.order_by("created_at").first()
            Inquiry.objects.create(property=first, name="Grace Mukamana", email="grace@example.com",
                                   phone="0788555666", assigned_agent=first.agent,
                                   message="Hello, is the price negotiable? I'd like to visit this weekend.")
            Appointment.objects.create(property=first, name="Grace Mukamana", phone="0788555666",
                                       email="grace@example.com", agent=first.agent,
                                       scheduled_for=timezone.now() + timedelta(days=3, hours=2),
                                       note="Saturday morning works best.")

        category_for = {"house": "Residential", "apartment": "Apartments", "plot": "Land & Plots"}
        for i, d in enumerate(MARKET_PROPERTIES):
            d = dict(d)
            title = d.pop("title")
            Property.objects.get_or_create(title=title, defaults=dict(
                description=f"{title}. Contact our agent for a viewing.", category=cats[category_for[d["property_type"]]],
                price=Decimal(d.pop("price")), district="Gasabo" if d["sector"] != "Gatenga" else "Kicukiro",
                owner_name="Demo Owner", owner_phone=f"07880000{i:02d}", agent=agent if i % 2 else agent2,
                created_by=agent, approval_status=Property.Approval.APPROVED, **d))

        # Demo hot deal: in real use an admin picks hot deals (and how long they last) in the dashboard
        Property.objects.filter(title__startswith="Spacious family house in Kimironko", hot_deal_until__isnull=True)             .update(hot_deal_until=timezone.now() + timedelta(days=14))
        refresh_scores()

        for title, cat, body in POSTS:
            Post.objects.get_or_create(title=title, defaults={"category": cat, "body": body, "author": admin,
                                                              "excerpt": body.split(".")[0] + "."})

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. Staff logins: admin, agent, agent2. Password: {DEMO_PASSWORD}"))
