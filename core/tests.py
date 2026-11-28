"""End-to-end tests for Theo Property. Run: python manage.py test"""
import os
import re
import shutil
from urllib.parse import unquote
import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO, StringIO

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import EmailCode, User
from appointments.models import Appointment
from inquiries.models import Inquiry, InquiryMessage
from notifications.models import Notification
from payments.models import Payment
from properties.models import Property, PropertyImage

PW = "TheoDemo#2026"
SUBMIT_HOUSE = "/properties/submit/?type=house&purpose=sale"


def emailed_code(kind="sign-in"):
    """The 6-digit code from the newest '<code> is your sign-in / password reset code' email."""
    msg = [m for m in mail.outbox if f"is your {kind} code" in m.subject][-1]
    return re.search(r"(\d{6}) is your", msg.subject).group(1)


def timezone_now_minus(seconds):
    from datetime import timedelta
    from django.utils import timezone
    return timezone.now() - timedelta(seconds=seconds)


def finish_sign_in(client):
    """Step 2 of signing in: type the emailed code."""
    return client.post(reverse("accounts:login_code"), {"code": emailed_code("sign-in")})


def make_image(name="photo.jpg"):
    buf = BytesIO()
    Image.new("RGB", (40, 30), "green").save(buf, "JPEG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/jpeg")


def submission_data(**extra):
    data = {"owner_name": "Jeanne Owner", "owner_phone": "0788 123 456", "owner_email": "jeanne@example.com",
            "title": "3-bedroom house in Kanombe", "description": "Good condition, title deed ready.",
            "property_type": "house", "purpose": "sale", "price": "65000000", "currency": "RWF",
            "sector": "Kanombe", "bedrooms": 3, "bathrooms": 2, "parking_spaces": 1,
            "agree": "on", "new_images": [make_image("a.jpg"), make_image("b.jpg")]}
    data.update(extra)
    return data


TEST_MEDIA = tempfile.mkdtemp(prefix="theo-test-media-")  # uploads in tests never touch the real media folder


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", MEDIA_ROOT=TEST_MEDIA)
class TheoPropertyTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        cls.admin = User.objects.get(username="admin")
        cls.agent = User.objects.get(username="agent")
        cls.agent2 = User.objects.get(username="agent2")
        cls.prop = Property.objects.filter(approval_status="approved", agent=cls.agent).first()
        cls.unclaimed = Property.objects.filter(agent__isnull=True).first()

    def setUp(self):
        cache.clear()

    def login(self, user):
        self.client.force_login(user)

    def assign(self, prop, agent):
        """The admin gives a property to an agent (agents can't pick properties themselves)."""
        self.login(self.admin)
        r = self.client.post(reverse("dashboard:property_assign", args=[prop.pk]), {"agent": agent.pk})
        self.assertEqual(r.status_code, 302)
        prop.refresh_from_db()
        self.assertEqual(prop.agent, agent)

    # ---------- public website: no account needed ----------
    def test_public_pages_load_without_login(self):
        for name in ["pages:home", "pages:about", "pages:contact", "properties:list", "properties:buy",
                     "properties:rent", "properties:land", "properties:commercial", "blog:list",
                     "properties:submit", "properties:status", "favorites:list", "accounts:login"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        r = self.client.get(self.prop.get_absolute_url())
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, self.prop.owner_phone)  # owner contact stays private

    def test_health_check_for_uptime_monitor(self):
        r = self.client.get("/healthz/")
        self.assertEqual((r.status_code, r.content), (200, b"ok"))
        self.assertIn("no-store", r["Cache-Control"])

    def test_public_header_has_no_dashboard(self):
        dash = reverse("dashboard:home")
        for user in (None, self.admin, self.agent):
            if user:
                self.login(user)
            r = self.client.get(reverse("pages:home"))
            html = r.content.decode()
            header, after = html.split("</nav>", 1)
            self.assertNotIn(f'href="{dash}"', header, user)
            # Sign In is only at the bottom of the page (footer), never in the header; staff see "My account"
            if user:
                self.assertIn("My account", header)
            else:
                self.assertNotIn("Sign In", header)
                self.assertIn("Sign In", html.split("<footer", 1)[1])
            # the search bar is always shown under the owner bar, with a placeholder
            self.assertNotIn("search-toggle", header)
            self.assertRegex(after.split("<main>")[0], r'id="siteSearch">(?s:.*)placeholder="Search a house')
            self.assertNotIn("Staff", header)
            self.assertNotIn("Submit your property", header)
            self.assertIn("Property owner?", after.split("<main>")[0])
            self.assertIn("Submit your property", after.split("<main>")[0])
        self.assertRegex(r.content.decode(), r"style(\.[0-9a-f]+)?\.css\?v=\d+")  # WhiteNoise may add a content hash
        # the owner bar is hidden on the submission pages themselves
        self.assertNotContains(self.client.get(reverse("properties:submit")), 'class="owner-bar"')

    # ---------- support: WhatsApp + phone (no public email / contact form) ----------
    def test_whatsapp_is_the_support_channel(self):
        for url in (reverse("pages:home"), reverse("pages:contact"), self.prop.get_absolute_url()):
            html = self.client.get(url).content.decode()
            self.assertIn("https://wa.me/250789567752", html, url)
            self.assertNotIn("ntezielyse@gmail.com", html, url)
        r = self.client.get(reverse("pages:contact"))
        self.assertContains(r, "+250 789 567 752")
        self.assertContains(r, 'href="tel:+250789567752"')
        self.assertContains(r, "I want to buy")            # ready-made WhatsApp questions
        self.assertNotContains(r, 'name="subject"')  # no contact form any more (the Sign In pop-up is a different form)

    def test_unclaimed_submission_emails_support(self):
        self.client.post(SUBMIT_HOUSE, submission_data())
        self.assertTrue(any("ntezielyse@gmail.com" in m.to and "submission" in m.subject for m in mail.outbox))

    def test_dashboard_test_email(self):
        self.login(self.admin)
        r = self.client.post(reverse("dashboard:test_email"), follow=True)
        self.assertContains(r, "Test email sent to ntezielyse@gmail.com")
        self.assertTrue(any("Test email" in m.subject for m in mail.outbox))

    def test_whatsapp_number_normalised(self):
        from core.models import SiteSetting
        s = SiteSetting.load()
        s.whatsapp = "0789 567 752"
        s.full_clean()
        self.assertEqual(s.whatsapp, "250789567752")
        s.whatsapp = "12345"
        with self.assertRaises(ValidationError):
            s.full_clean()

    # ---------- agent self-registration, approved only by the super admin ----------
    def apply_as_agent(self, username="newagent", **extra):
        data = {"first_name": "Diane", "last_name": "Uwera", "phone": "0788123123", "email": f"{username}@example.com",
                "username": username, "agency_name": "Uwera Homes", "license_number": "RDB-123",
                "years_experience": "4", "application_note": "I cover Kicukiro.", "agree": "on",
                "password1": "Strong#Pass2026", "password2": "Strong#Pass2026"}
        data.update(extra)
        return self.client.post(reverse("accounts:agent_register"), data)

    def test_agent_application_flow(self):
        self.assertContains(self.client.get(reverse("accounts:login")), "Register as an agent")
        r = self.apply_as_agent()
        self.assertRedirects(r, reverse("accounts:agent_register_done"))
        agent = User.objects.get(username="newagent")
        self.assertEqual((agent.role, agent.is_active), ("agent", False))
        self.assertEqual(agent.agent_profile.application_status, "pending")
        self.assertTrue(Notification.objects.filter(user=self.admin, title="New agent application").exists())

        # can't log in yet; told it's pending (only with the right password)
        r = self.client.post(reverse("accounts:login"), {"username": "newagent", "password": "Strong#Pass2026"})
        self.assertContains(r, "waiting for approval")
        r = self.client.post(reverse("accounts:login"), {"username": "newagent", "password": "wrong-password"})
        self.assertNotContains(r, "waiting for approval")

        # a normal admin (not super admin) can't decide, nor sneak the account active
        plain_admin = User.objects.create_user("admin2", "admin2@example.com", PW, role="admin")
        self.login(plain_admin)
        self.assertEqual(self.client.get(reverse("dashboard:agent_applications")).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:agent_application_decide",
                                                  args=[agent.agent_profile.pk, "approve"])).status_code, 403)
        self.client.post(reverse("dashboard:user_detail", args=[agent.pk]),
                         {"role-role": "agent", "role-is_active": "on", "agent-years_experience": "4",
                          "agent-can_manage_appointments": "on"})
        agent.refresh_from_db()
        self.assertFalse(agent.is_active)

        # super admin approves -> agent can log in
        self.login(self.admin)
        r = self.client.get(reverse("dashboard:agent_applications"))
        self.assertContains(r, "Diane Uwera")
        self.assertContains(r, "RDB-123")
        self.client.post(reverse("dashboard:agent_application_decide", args=[agent.agent_profile.pk, "approve"]))
        agent.refresh_from_db()
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.agent_profile.application_status, "approved")
        self.assertEqual(agent.agent_profile.reviewed_by, self.admin)
        self.client.logout()
        r = self.client.post(reverse("accounts:login"), {"username": "newagent", "password": "Strong#Pass2026"})
        self.assertRedirects(r, reverse("accounts:login_code"), fetch_redirect_response=False)
        self.assertRedirects(finish_sign_in(self.client), reverse("accounts:after_login"), fetch_redirect_response=False)

    def test_agent_application_rejected(self):
        self.apply_as_agent(username="badagent")
        agent = User.objects.get(username="badagent")
        self.login(self.admin)
        self.client.post(reverse("dashboard:agent_application_decide", args=[agent.agent_profile.pk, "reject"]),
                         {"rejection_reason": "Licence could not be verified"})
        agent.refresh_from_db()
        self.assertFalse(agent.is_active)
        self.assertEqual(agent.agent_profile.application_status, "rejected")
        self.client.logout()
        r = self.client.post(reverse("accounts:login"), {"username": "badagent", "password": "Strong#Pass2026"})
        self.assertContains(r, "Licence could not be verified")

    def test_agent_application_validation(self):
        self.apply_as_agent(username="dup", email=self.agent.email)          # email already used
        self.apply_as_agent(username="bot", website="http://spam.example")   # honeypot
        self.apply_as_agent(username="weak", password1="123", password2="123")
        self.assertFalse(User.objects.filter(username__in=["dup", "bot", "weak"]).exists())

    def test_no_public_registration(self):
        self.assertEqual(self.client.get("/accounts/register/").status_code, 404)
        self.assertNotContains(self.client.get(reverse("pages:home")), "Sign up")

    def test_pending_property_hidden_from_public(self):
        self.assertEqual(self.client.get(self.unclaimed.get_absolute_url()).status_code, 404)
        self.assertNotContains(self.client.get(reverse("properties:list")), self.unclaimed.title)
        self.login(self.agent2)
        self.assertEqual(self.client.get(self.unclaimed.get_absolute_url()).status_code, 200)

    def test_search_filters(self):
        r = self.client.get(reverse("properties:list"), {"purpose": "rent", "location": "Kimihurura"})
        for p in r.context["page_obj"]:
            self.assertEqual(p.purpose, "rent"); self.assertIn("Kimihurura", p.location_display + p.address)
        r = self.client.get(reverse("properties:list"), {"min_price": 100_000_000, "sort": "price"})
        prices = [p.price for p in r.context["page_obj"]]
        self.assertTrue(all(x >= 100_000_000 for x in prices))
        self.assertEqual(prices, sorted(prices))

    def test_anonymous_inquiry(self):
        r = self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                             {"on_whatsapp": "on", "name": "Visitor", "email": "v@example.com", "phone": "0788000111",
                              "message": "Is it available?"})
        self.assertEqual(r.status_code, 302)
        inq = Inquiry.objects.get(phone="0788000111")
        self.assertEqual(inq.assigned_agent, self.agent)
        self.assertTrue(Notification.objects.filter(user=self.agent, title="New property inquiry").exists())

    def test_inquiry_skips_deactivated_agent(self):
        """If the listing's agent is deactivated, the message goes to the admins instead of being lost."""
        User.objects.filter(pk=self.agent.pk).update(is_active=False)
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"on_whatsapp": "on", "name": "Visitor", "email": "v@example.com", "phone": "0788000111", "message": "Hello?"})
        self.assertFalse(Notification.objects.filter(user=self.agent, title="New property inquiry").exists())
        self.assertTrue(Notification.objects.filter(user=self.admin, title="New property inquiry").exists())

    def test_inquiry_opens_whatsapp_with_listing_and_stays_private(self):
        url = reverse("inquiries:submit", args=[self.prop.slug])
        # phone is required, and the client must confirm it is on WhatsApp
        self.client.post(url, {"name": "No Phone", "message": "Hi", "on_whatsapp": "on"})
        self.assertFalse(Inquiry.objects.filter(name="No Phone").exists())
        self.client.post(url, {"name": "No Confirm", "phone": "0788555443", "message": "Hi"})
        self.assertFalse(Inquiry.objects.filter(name="No Confirm").exists())
        self.client.post(url, {"name": "Bad Number", "phone": "12", "message": "Hi", "on_whatsapp": "on"})
        self.assertFalse(Inquiry.objects.filter(name="Bad Number").exists())
        page = self.client.get(self.prop.get_absolute_url())
        r = self.client.post(url, {"name": "Claudine Mukamana", "phone": "0788555444", "on_whatsapp": "on",
                                   "message": "Is water available all year?"},
                             HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 14) Mobile Chrome/120.0")
        inq = Inquiry.objects.get(phone="0788555444")
        # straight to the agent's WhatsApp, with the client's words and the listing's link
        self.assertTrue(r["Location"].startswith("https://wa.me/250"), r["Location"])
        text = unquote(r["Location"].split("?text=", 1)[1])
        self.assertIn("Is water available all year?", text)
        self.assertIn(self.prop.reference, text)
        self.assertIn(self.prop.get_absolute_url(), text)
        self.assertIn("Claudine Mukamana", text)
        self.assertEqual(inq.device_info, "Android phone · Chrome")
        self.assertTrue(inq.device_key and inq.client_ip)
        # no code anywhere, and nothing about it on the public property page
        InquiryMessage.objects.create(inquiry=inq, author=self.agent, body="Yes, there is a water tank.")
        stranger = self.client_class()
        public = stranger.get(self.prop.get_absolute_url())
        for secret in ("Claudine", "Is water available all year?", "Yes, there is a water tank.", "0788555444"):
            self.assertNotContains(public, secret)
        # the agent sees it in the dashboard, with a WhatsApp button to the client including the listing
        agent = self.client_class()
        agent.force_login(self.agent)
        detail = agent.get(reverse("dashboard:inquiry_detail", args=[inq.pk]))
        self.assertContains(detail, "https://wa.me/250788555444?text=")
        self.assertContains(detail, "Android phone")
        # the property page's WhatsApp button also carries the listing's link
        self.assertIn(self.prop.get_absolute_url(), unquote(page.context["whatsapp_url"]))

    def test_inquiry_form_answers_the_page_directly(self):
        """With JavaScript, the form gets the WhatsApp link back and opens WhatsApp itself (no wa.me choice page)."""
        url = reverse("inquiries:submit", args=[self.prop.slug])
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        r = self.client.post(url, {"name": "Jo", "phone": "0788555111", "message": "Hi", "on_whatsapp": "on"}, **ajax)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["whatsapp"].startswith("https://wa.me/250"))
        self.assertIn("Agent replies", data["notice"])
        self.assertIn("tp_device", r.cookies)
        bad = self.client.post(url, {"name": "Jo", "message": "Hi"}, **ajax)
        self.assertEqual((bad.status_code, bad.json()["ok"]), (400, False))
        # the property page offers question choices that open "Agent replies" (no form to fill in)
        page = self.client.get(self.prop.get_absolute_url())
        self.assertContains(page, 'data-chat-ask="Is this property still available?"')
        self.assertContains(page, "Write my own question")
        self.assertContains(page, 'id="ask"')  # at the bottom of the listing (left side)
        self.assertLess(page.content.decode().index('id="ask"'), page.content.decode().index('id="contact"'))
        if self.prop.latitude:  # the map sits in the right column, under the agent's card
            html = page.content.decode()
            self.assertLess(html.index('id="contact"'), html.index('id="map"'))
        self.assertNotContains(page, 'name="phone"')
        self.assertNotContains(page, "{#")  # no template notes printed on the page
        self.assertNotContains(self.client.get(reverse("pages:home")), "floating button that opens")

    def test_admin_sees_all_conversations_with_property_and_agent(self):
        a = Inquiry.objects.create(property=self.prop, name="Alice Buyer", message="Is it available?", assigned_agent=self.agent)
        InquiryMessage.objects.create(inquiry=a, author=self.agent, body="Yes, still available.")
        other = Property.objects.filter(approval_status="approved", agent=self.agent2).first()
        Inquiry.objects.create(property=other, name="Bob Renter", message="Viewing on Sunday?", assigned_agent=self.agent2)
        self.login(self.admin)
        url = reverse("dashboard:inquiries")
        r = self.client.get(url)
        for text in ("Alice Buyer", "Bob Renter", self.prop.reference, other.reference):
            self.assertContains(r, text)
        self.assertEqual([i.name for i in self.client.get(url, {"q": "still available"}).context["page_obj"]], ["Alice Buyer"])
        self.assertEqual([i.name for i in self.client.get(url, {"agent": self.agent2.pk}).context["page_obj"]
                          if i.name in ("Alice Buyer", "Bob Renter")], ["Bob Renter"])
        detail = self.client.get(reverse("dashboard:inquiry_detail", args=[a.pk]))
        self.assertContains(detail, "Yes, still available.")
        # agents only see their own
        self.login(self.agent2)
        self.assertNotContains(self.client.get(url), "Alice Buyer")

    def test_whatsapp_number_formats(self):
        from inquiries.device import whatsapp_number
        for raw in ("0788 123 456", "+250 788 123 456", "250788123456", "788123456", "00250788123456"):
            self.assertEqual(whatsapp_number(raw), "250788123456", raw)
        self.assertEqual(whatsapp_number("12"), "")

    # ---------- who may manage a listing (edit / lock / delete) ----------
    def test_staff_bar_on_property_page(self):
        url = self.prop.get_absolute_url()
        edit = reverse("dashboard:property_edit", args=[self.prop.pk])
        self.assertNotContains(self.client.get(url), edit)            # visitors: no staff tools
        self.login(self.agent2)
        self.assertNotContains(self.client.get(url), edit)            # another agent: none
        for user in (self.admin, self.agent):                         # admin and the listing's own agent: yes
            self.login(user)
            r = self.client.get(url)
            self.assertContains(r, edit)
            self.assertContains(r, reverse("dashboard:property_delete", args=[self.prop.pk]))
            self.assertContains(r, reverse("dashboard:property_status", args=[self.prop.pk, "inactive"]))

    def test_agent_manages_only_own_listings(self):
        other = Property.objects.filter(approval_status="approved").exclude(agent=self.agent).exclude(agent=None).first()
        self.login(self.agent)
        for name, args in (("dashboard:property_status", [other.pk, "inactive"]), ("dashboard:property_delete", [other.pk])):
            self.assertEqual(self.client.post(reverse(name, args=args)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("dashboard:property_edit", args=[other.pk])).status_code, 403)
        # their own listing: lock, then delete
        own = self.prop
        self.client.post(reverse("dashboard:property_status", args=[own.pk, "inactive"]))
        own.refresh_from_db()
        self.assertEqual(own.approval_status, "inactive")
        self.client.post(reverse("dashboard:property_delete", args=[own.pk]))
        self.assertFalse(Property.objects.filter(pk=own.pk).exists())

    def test_admin_manages_any_listing(self):
        prop = Property.objects.filter(approval_status="approved").exclude(agent=None).exclude(agent=self.admin).first()
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("dashboard:property_edit", args=[prop.pk])).status_code, 200)
        self.client.post(reverse("dashboard:property_status", args=[prop.pk, "inactive"]))
        prop.refresh_from_db()
        self.assertEqual(prop.approval_status, "inactive")
        self.client.post(reverse("dashboard:property_delete", args=[prop.pk]))
        self.assertFalse(Property.objects.filter(pk=prop.pk).exists())

    # ---------- sold / rented, and old conversations ----------
    def test_marking_property_taken(self):
        prop = self.prop
        prop.agent = self.agent
        prop.save()
        self.client.post(reverse("inquiries:submit", args=[prop.slug]),
                         {"on_whatsapp": "on", "name": "Early Bird", "phone": "0788000444", "message": "Still available?"})
        inq = Inquiry.objects.get(phone="0788000444")
        # only the property's agent (or an admin) may mark it
        other = self.client_class()
        other.force_login(self.agent2)
        status = prop.taken_status
        self.assertEqual(other.post(reverse("dashboard:property_availability", args=[prop.pk, status])).status_code, 403)
        agent = self.client_class()
        agent.force_login(self.agent)
        agent.post(reverse("dashboard:property_availability", args=[prop.pk, status]))
        prop.refresh_from_db()
        self.assertTrue(prop.is_taken)
        # hidden from listings and live search; the page says it's taken and has no message form
        self.assertNotIn(prop, list(self.client.get(reverse("properties:list")).context["page_obj"]))
        self.assertNotIn(prop.reference, str(self.client.get(reverse("properties:suggest"), {"q": prop.reference}).json()))
        page = self.client_class().get(prop.get_absolute_url())
        self.assertContains(page, "no longer available")
        self.assertNotContains(page, "Send on WhatsApp")
        # new questions are refused; the open conversation was closed with a note to the client
        self.client.post(reverse("inquiries:submit", args=[prop.slug]),
                         {"on_whatsapp": "on", "name": "Late", "phone": "0788000555", "message": "Can I see it?"})
        self.assertFalse(Inquiry.objects.filter(phone="0788000555").exists())
        inq.refresh_from_db()
        self.assertEqual(inq.status, "closed")
        self.assertTrue(inq.thread.filter(from_client=False, body__contains="has now been").exists())
        # available again: back on the website
        agent.post(reverse("dashboard:property_availability", args=[prop.pk, "available"]))
        self.assertIn(prop, list(self.client.get(reverse("properties:list")).context["page_obj"].paginator.object_list))

    def test_old_conversations_deleted_after_a_year(self):
        from django.core.management import call_command
        from inquiries.cleanup import purge_old_inquiries
        old = Inquiry.objects.create(property=self.prop, name="Old", phone="0788000666", message="Hi")
        InquiryMessage.objects.create(inquiry=old, body="Answer")
        recent = Inquiry.objects.create(property=self.prop, name="Recent", phone="0788000777", message="Hi")
        Inquiry.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=366))
        Inquiry.objects.filter(pk=recent.pk).update(updated_at=timezone.now() - timedelta(days=300))  # kept: history for admins
        self.assertGreaterEqual(purge_old_inquiries(), 1)
        self.assertFalse(Inquiry.objects.filter(pk=old.pk).exists())
        self.assertFalse(InquiryMessage.objects.filter(inquiry_id=old.pk).exists())
        self.assertTrue(Inquiry.objects.filter(pk=recent.pk).exists())
        call_command("purge_old_inquiries", stdout=StringIO())  # the manual command also works
        # and it runs by itself on normal traffic
        Inquiry.objects.filter(pk=recent.pk).update(updated_at=timezone.now() - timedelta(days=400))
        cache.clear()
        self.client.get(reverse("pages:home"))
        self.assertFalse(Inquiry.objects.filter(pk=recent.pk).exists())

    # ---------- initial passwords and super admin account control ----------
    def test_agent_created_by_admin_must_change_initial_password(self):
        self.login(self.admin)
        self.client.post(reverse("dashboard:agent_create"), {
            "first_name": "New", "last_name": "Agent", "username": "newagent", "email": "new@agent.test",
            "phone": "0788123456", "password1": "Init!Pass2026x", "password2": "Init!Pass2026x",
            "can_manage_appointments": "on"})
        new = User.objects.get(username="newagent")
        self.assertTrue(new.must_change_password)
        # signs in with the initial password: every dashboard page sends them to "choose your own password"
        c = self.client_class()
        self.assertTrue(c.login(username="newagent", password="Init!Pass2026x"))
        r = c.get(reverse("dashboard:home"))
        self.assertRedirects(r, reverse("accounts:password_change"), fetch_redirect_response=False)
        self.assertContains(c.get(reverse("accounts:password_change")), "initial password")
        url = reverse("accounts:password_change")
        # must type the initial password correctly, and choose a different one
        c.post(url, {"old_password": "wrong", "new_password1": "My0wn!Secret9", "new_password2": "My0wn!Secret9"})
        c.post(url, {"old_password": "Init!Pass2026x", "new_password1": "Init!Pass2026x", "new_password2": "Init!Pass2026x"})
        new.refresh_from_db()
        self.assertTrue(new.must_change_password)
        c.post(url, {"old_password": "Init!Pass2026x", "new_password1": "My0wn!Secret9", "new_password2": "My0wn!Secret9"})
        new.refresh_from_db()
        self.assertFalse(new.must_change_password)
        self.assertTrue(new.check_password("My0wn!Secret9"))
        self.assertEqual(c.get(reverse("dashboard:home")).status_code, 200)  # still signed in, dashboard open

    def test_super_admin_controls_accounts(self):
        other_admin = User.objects.create_user("admin2", "a2@test.rw", "Adm1n!Pass99", role=User.Role.ADMIN)
        act = lambda pk, a: reverse("dashboard:user_action", args=[pk, a])
        # a normal administrator: may manage agents, but not administrators, roles, or themselves
        self.login(other_admin)
        self.assertEqual(self.client.post(act(self.agent.pk, "promote")).status_code, 403)
        self.assertEqual(self.client.post(act(self.admin.pk, "deactivate")).status_code, 403)
        self.assertEqual(self.client.post(act(other_admin.pk, "deactivate")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:admin_create")).status_code, 403)
        self.client.post(act(self.agent2.pk, "deactivate"))
        self.agent2.refresh_from_db()
        self.assertFalse(self.agent2.is_active)
        # the super admin: activate, deactivate, promote, demote, any time
        self.login(self.admin)
        self.client.post(act(self.agent2.pk, "activate"))
        self.client.post(act(self.agent2.pk, "promote"))
        self.agent2.refresh_from_db()
        self.assertEqual((self.agent2.is_active, self.agent2.role), (True, "admin"))
        self.client.post(act(self.agent2.pk, "demote"))
        self.agent2.refresh_from_db()
        self.assertEqual(self.agent2.role, "agent")
        self.client.post(act(other_admin.pk, "deactivate"))
        other_admin.refresh_from_db()
        self.assertFalse(other_admin.is_active)
        self.assertEqual(self.client.post(act(self.admin.pk, "deactivate")).status_code, 403)  # never yourself
        # super admin creates an administrator with an initial password
        self.client.post(reverse("dashboard:admin_create"), {
            "first_name": "Grace", "last_name": "Admin", "username": "grace", "email": "grace@test.rw",
            "phone": "", "password1": "Init!Pass2026x", "password2": "Init!Pass2026x"})
        grace = User.objects.get(username="grace")
        self.assertEqual((grace.role, grace.must_change_password), ("admin", True))

    def test_set_initial_password_signs_person_out(self):
        agent_session = self.client_class()
        agent_session.force_login(self.agent2)
        self.login(self.admin)
        self.client.post(reverse("dashboard:user_set_password", args=[self.agent2.pk]),
                         {"new_password1": "Reset!Pass2026", "new_password2": "Reset!Pass2026"})
        self.agent2.refresh_from_db()
        self.assertTrue(self.agent2.must_change_password)
        self.assertTrue(self.agent2.check_password("Reset!Pass2026"))
        r = agent_session.get(reverse("dashboard:home"))  # old session no longer works
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])

    def test_similar_properties_same_area_or_close_price(self):
        from properties.similar import similar_properties
        from properties.views import public_properties
        base = dict(property_type="house", purpose="sale", currency="RWF", description="x",
                    approval_status="approved", bedrooms=3, bathrooms=2)
        main = Property.objects.create(title="Main house", price=100_000_000, sector="Remera", district="Gasabo", **base)
        same_area_close = Property.objects.create(title="Area+price", price=105_000_000, sector="Remera", district="Gasabo", **base)
        same_area_far = Property.objects.create(title="Area only", price=400_000_000, sector="Remera", district="Gasabo", **base)
        far_close = Property.objects.create(title="Price only", price=95_000_000, sector="Musanze", district="Musanze", **base)
        unrelated = Property.objects.create(title="Neither", price=900_000_000, sector="Huye", district="Huye", **base)
        other_type = Property.objects.create(title="Plot", price=100_000_000, sector="Remera", district="Gasabo",
                                             **{**base, "property_type": "plot"})
        result = similar_properties(main, public_properties().filter(pk__in=[
            same_area_close.pk, same_area_far.pk, far_close.pk, unrelated.pk, other_type.pk]))
        self.assertEqual([p.title for p in result], ["Area+price", "Area only", "Price only"])
        self.assertEqual(result[0].similar_reason, "Same area (Remera) · similar price")
        self.assertEqual(result[2].similar_reason, "Similar price")
        self.assertContains(self.client.get(main.get_absolute_url()), "Area+price")

    def test_sign_in_popup(self):
        home = self.client.get(reverse("pages:home"))
        self.assertContains(home, 'id="signInModal"')          # pop-up on every public page for visitors
        self.assertContains(home, 'data-bs-target="#signInModal"')
        login = self.client.get(reverse("accounts:login"))      # the page itself has no duplicate pop-up
        self.assertNotContains(login, 'id="signInModal"')
        # what the pop-up does: a wrong password comes back with the error text it shows
        r = self.client.post(reverse("accounts:login"), {"username": "agent", "password": "wrong-one"})
        self.assertContains(r, "data-login-error")
        self.assertContains(r, "Please enter a correct username or email and password")
        # signed in: no pop-up any more
        self.login(self.agent)
        self.assertNotContains(self.client.get(reverse("pages:home")), 'id="signInModal"')

    def test_sign_in_with_username_or_email(self):
        self.agent2.set_password("Agent2!Pass99")
        self.agent2.save()
        url = reverse("accounts:login")
        for login in (self.agent2.username, self.agent2.email, self.agent2.email.upper(), f"  {self.agent2.email} "):
            c = self.client_class()
            EmailCode.objects.filter(user=self.agent2).delete()  # each loop is a fresh sign-in (no 1-minute wait)
            r = c.post(url, {"username": login, "password": "Agent2!Pass99"})
            self.assertRedirects(r, reverse("accounts:login_code"), fetch_redirect_response=False, msg_prefix=login)
            finish_sign_in(c)
            self.assertEqual(int(c.session["_auth_user_id"]), self.agent2.pk)
        self.assertEqual(self.client_class().post(url, {"username": self.agent2.email, "password": "nope"}).status_code, 200)

    # ---------- emailed 6-digit codes: sign-in verification and password reset ----------
    def test_sign_in_needs_emailed_code(self):
        url = reverse("accounts:login")
        mail.outbox.clear()
        r = self.client.post(url, {"username": "agent", "password": PW, "next": "/dashboard/properties/"})
        self.assertRedirects(r, reverse("accounts:login_code"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)            # password alone is not enough
        code_mail = [m for m in mail.outbox if "is your sign-in code" in m.subject]
        self.assertEqual([m.to for m in code_mail], [[self.agent.email]])
        code = emailed_code()
        self.assertNotIn(code, EmailCode.objects.get(user=self.agent).code_hash)  # only a hash is stored
        page = self.client.get(reverse("accounts:login_code"))
        self.assertContains(page, "6-digit code")
        self.assertNotContains(page, self.agent.email)                     # address shown masked
        wrong = "000000" if code != "000000" else "111111"
        r = self.client.post(reverse("accounts:login_code"), {"code": wrong})
        self.assertContains(r, "not correct")
        self.assertNotIn("_auth_user_id", self.client.session)
        r = self.client.post(reverse("accounts:login_code"), {"code": f"{code[:3]} {code[3:]}"})  # spaces are fine
        self.assertRedirects(r, "/dashboard/properties/", fetch_redirect_response=False)  # ?next= is kept
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.agent.pk)
        # the code can't be used again
        other = self.client_class()
        other.post(url, {"username": "agent", "password": PW})
        self.assertContains(other.post(reverse("accounts:login_code"), {"code": code}), "not correct")

    def test_sign_in_code_locks_after_wrong_tries_and_expires(self):
        from datetime import timedelta
        from django.utils import timezone
        url, code_url = reverse("accounts:login"), reverse("accounts:login_code")
        self.client.post(url, {"username": "agent", "password": PW})
        code = emailed_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.client.post(code_url, {"code": wrong})
        r = self.client.post(code_url, {"code": wrong})                      # 5th wrong try burns the code
        self.assertRedirects(r, url, fetch_redirect_response=False)
        self.assertRedirects(self.client.post(code_url, {"code": code}), url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        # expired code
        EmailCode.objects.filter(user=self.agent).delete()
        self.client.post(url, {"username": "agent", "password": PW})
        EmailCode.objects.filter(user=self.agent).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertContains(self.client.post(code_url, {"code": emailed_code()}), "expired")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sign_in_code_resend_is_limited(self):
        self.client.post(reverse("accounts:login"), {"username": "agent", "password": PW})
        sent = len(mail.outbox)
        r = self.client.post(reverse("accounts:login_code"), {"action": "resend"}, follow=True)
        self.assertContains(r, "We just sent a code")                       # not again within a minute
        self.assertEqual(len(mail.outbox), sent)
        EmailCode.objects.filter(user=self.agent).update(created_at=timezone_now_minus(120))
        r = self.client.post(reverse("accounts:login_code"), {"action": "resend"}, follow=True)
        self.assertContains(r, "A new code is on its way")
        self.assertEqual(len(mail.outbox), sent + 1)
        finish_sign_in(self.client)                                          # the newest code works
        self.assertIn("_auth_user_id", self.client.session)

    def test_sign_in_code_emergency_switch(self):
        with override_settings(LOGIN_EMAIL_CODE=False):
            r = self.client.post(reverse("accounts:login"), {"username": "agent", "password": PW})
        self.assertRedirects(r, reverse("accounts:after_login"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)

    def test_sign_in_code_email_failure_is_explained(self):
        from unittest import mock
        with mock.patch("accounts.email_codes.send_branded", return_value=False):
            r = self.client.post(reverse("accounts:login"), {"username": "agent", "password": PW})
        self.assertContains(r, "email your sign-in code right now")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_reset_with_code(self):
        reset, code_url = reverse("accounts:password_reset"), reverse("accounts:password_reset_code")
        mail.outbox.clear()
        # an unknown email gets exactly the same answer, and nothing is sent
        r = self.client.post(reset, {"email": "nobody@example.com"})
        self.assertRedirects(r, code_url, fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(self.client.post(code_url, {"code": "123456", "new_password1": "New!Pass2026x",
                                                        "new_password2": "New!Pass2026x"}), "not correct")
        # the real account
        self.client.post(reset, {"email": self.agent.email.upper()})
        self.assertEqual(mail.outbox[-1].to, [self.agent.email])
        code = emailed_code("password reset")
        wrong = "000000" if code != "000000" else "111111"
        r = self.client.post(code_url, {"code": wrong, "new_password1": "New!Pass2026x", "new_password2": "New!Pass2026x"})
        self.assertContains(r, "not correct")
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.check_password(PW))                       # unchanged
        r = self.client.post(code_url, {"code": code, "new_password1": "New!Pass2026x", "new_password2": "New!Pass2026x"})
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.check_password("New!Pass2026x"))
        # a weak new password is refused before the code is used up
        self.client.post(reset, {"email": self.agent.email})
        EmailCode.objects.filter(user=self.agent).update(created_at=timezone_now_minus(120))
        self.client.post(code_url, {"action": "resend"})
        r = self.client.post(code_url, {"code": emailed_code("password reset"), "new_password1": "123", "new_password2": "123"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(EmailCode.objects.filter(user=self.agent, purpose="reset", used_at__isnull=True).exists())

    def test_new_account_email_and_password_reset_by_email(self):
        self.login(self.admin)
        mail.outbox.clear()
        self.client.post(reverse("dashboard:agent_create"), {
            "first_name": "Eric", "last_name": "N", "username": "ericn", "email": "Eric.N@Example.com",
            "phone": "", "password1": "Init!Pass2026x", "password2": "Init!Pass2026x"})
        eric = User.objects.get(username="ericn")
        self.assertEqual(eric.email, "eric.n@example.com")  # stored in one spelling
        sent = [m for m in mail.outbox if eric.email in m.to]
        self.assertTrue(sent)
        body = sent[0].body
        self.assertIn("ericn", body)
        self.assertIn("eric.n@example.com", body)
        self.assertNotIn("Init!Pass2026x", body)  # the password is never emailed
        html = sent[0].alternatives[0][0]  # branded HTML version: headline, facts and a button
        for part in ("Your agent account is ready", "Your account", "ericn", "Sign in to your account", "/accounts/login/"):
            self.assertIn(part, html)
        self.assertNotIn("Init!Pass2026x", html)
        # an email already used by another account is refused
        self.client.post(reverse("dashboard:agent_create"), {
            "first_name": "X", "last_name": "Y", "username": "dupe", "email": "ERIC.N@example.com",
            "phone": "", "password1": "Init!Pass2026x", "password2": "Init!Pass2026x"})
        self.assertFalse(User.objects.filter(username="dupe").exists())
        # forgot password: a 6-digit code goes to the account's email; choosing a password with it clears "initial"
        self.client.logout()
        mail.outbox.clear()
        r = self.client.post(reverse("accounts:password_reset"), {"email": "ERIC.N@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_code"), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["eric.n@example.com"])
        self.client.post(reverse("accounts:password_reset_code"),
                         {"code": emailed_code("password reset"), "new_password1": "Chosen!Pass77",
                          "new_password2": "Chosen!Pass77"})
        eric.refresh_from_db()
        self.assertTrue(eric.check_password("Chosen!Pass77"))
        self.assertFalse(eric.must_change_password)

    def _google_sign_in(self, client, email, verified=True, aud="test-client.apps.googleusercontent.com", nonce=None):
        """Go through 'Continue with Google' with Google's reply simulated (no internet needed)."""
        import base64, json, time
        from unittest import mock
        from urllib.parse import parse_qs, urlparse
        start = client.get(reverse("google_login"))
        q = parse_qs(urlparse(start["Location"]).query)
        claims = {"iss": "https://accounts.google.com", "aud": aud, "exp": time.time() + 300,
                  "email": email, "email_verified": verified, "nonce": nonce or q["nonce"][0]}
        b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        token = f"{b64({'alg': 'RS256'})}.{b64(claims)}.sig"
        with mock.patch("accounts.google.exchange_code", return_value={"id_token": token}):
            return client.get(reverse("google_callback"), {"state": q["state"][0], "code": "one-time"})

    def test_google_sign_in_staff_only(self):
        from django.test import override_settings
        with override_settings(GOOGLE_LOGIN_ENABLED=True, GOOGLE_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
                               GOOGLE_OAUTH_CLIENT_SECRET="s", GOOGLE_OAUTH_REDIRECT_URI=""):
            self.assertContains(self.client.get(reverse("pages:home")), "Continue with Google")
            # an agent whose account email matches their Google email gets in
            c = self.client_class()
            r = self._google_sign_in(c, self.agent.email.upper())
            self.assertRedirects(r, reverse("accounts:after_login"), fetch_redirect_response=False)
            self.assertEqual(int(c.session["_auth_user_id"]), self.agent.pk)
            # strangers, unverified emails, wrong app, replayed/forged nonce and inactive staff are refused
            client_user = User.objects.create_user("buyer", "buyer@gmail.com", "x", role=User.Role.CLIENT)
            for kwargs in ({"email": "stranger@gmail.com"}, {"email": client_user.email},
                           {"email": self.agent2.email, "verified": False},
                           {"email": self.agent2.email, "aud": "someone-else"},
                           {"email": self.agent2.email, "nonce": "forged"}):
                c = self.client_class()
                self._google_sign_in(c, **kwargs)
                self.assertNotIn("_auth_user_id", c.session, kwargs)
            User.objects.filter(pk=self.agent2.pk).update(is_active=False)
            c = self.client_class()
            self._google_sign_in(c, self.agent2.email)
            self.assertNotIn("_auth_user_id", c.session)
            self.assertFalse(User.objects.filter(email="stranger@gmail.com").exists())  # never creates accounts
            # a verified Google email with no account is sent to the sign-up form, email filled in
            c = self.client_class()
            r = self._google_sign_in(c, "new.person@gmail.com")
            self.assertRedirects(r, reverse("accounts:agent_register"), fetch_redirect_response=False)
            self.assertContains(c.get(reverse("accounts:agent_register")), 'value="new.person@gmail.com"')
            self.assertNotIn("_auth_user_id", c.session)
            # a callback without the matching start (forged state) is refused
            c = self.client_class()
            c.get(reverse("google_callback"), {"state": "made-up", "code": "x"})
            self.assertNotIn("_auth_user_id", c.session)
        # not configured: no button, and the addresses don't exist
        with override_settings(GOOGLE_LOGIN_ENABLED=False):
            self.assertNotContains(self.client.get(reverse("pages:home")), "Continue with Google")
            self.assertEqual(self.client.get(reverse("google_login")).status_code, 404)

    def test_live_search_suggestions(self):
        url = reverse("properties:suggest")
        self.assertEqual(self.client.get(url, {"q": "a"}).json()["properties"], [])  # too short
        data = self.client.get(url, {"q": "plot gatenga"}).json()
        self.assertTrue(data["properties"])
        for p in data["properties"]:  # every word matches: a plot, in Gatenga
            self.assertIn("gatenga", (p["title"] + p["location"]).lower())
            self.assertIn(p["type"].lower(), ("plot", "land", "plot of land"))
        self.assertIn("Gatenga", [pl["name"] for pl in data["places"]])
        # by reference, and unapproved listings never show up
        self.assertEqual(self.client.get(url, {"q": self.prop.reference}).json()["properties"][0]["reference"],
                         self.prop.reference)
        hidden = Property.objects.exclude(approval_status="approved").first()
        if hidden:
            refs = [p["reference"] for p in self.client.get(url, {"q": hidden.reference}).json()["properties"]]
            self.assertNotIn(hidden.reference, refs)
        # pressing Enter: the results page uses the same multi-word matching
        r = self.client.get(reverse("properties:list"), {"q": "plot gatenga"})
        self.assertTrue(all("gatenga" in (p.title + p.location_display).lower() for p in r.context["page_obj"]))
        self.assertContains(self.client.get(reverse("pages:home")), 'class="live-search')

    def test_property_page_has_sliding_gallery(self):
        r = self.client.get(self.prop.get_absolute_url())
        if self.prop.images.exists():
            self.assertContains(r, 'id="galleryMain"')
            self.assertContains(r, 'id="galleryViewer"')

    def test_conversations_open_only_on_the_sending_device(self):
        page = reverse("inquiries:conversations")
        home = self.client.get(reverse("pages:home"))  # the button is there for every visitor
        self.assertContains(home, "messages-float")
        self.assertContains(self.client.get(page), "No conversations on this device")
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"on_whatsapp": "on", "name": "Visitor", "phone": "0788000111", "message": "Is the garden fenced?"})
        inq = Inquiry.objects.get(phone="0788000111")
        InquiryMessage.objects.create(inquiry=inq, body="Yes, fully fenced.")
        # same device: open straight away (no code), the badge shows the new reply
        self.assertContains(self.client.get(reverse("pages:home")), 'class="messages-badge">1<')
        r = self.client.get(page)
        self.assertContains(r, "Is the garden fenced?")
        self.assertContains(r, "Yes, fully fenced.")
        self.assertNotContains(r, "meeting code")
        self.assertNotContains(self.client.get(reverse("pages:home")), "messages-badge")  # now read
        # still there later, even after the session is gone (the device key is a long-lived cookie)
        self.client.session.flush()
        self.client.cookies.pop(settings.SESSION_COOKIE_NAME, None)
        self.assertContains(self.client.get(page), "Yes, fully fenced.")
        self.client.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "Great, thanks!"})
        self.assertTrue(InquiryMessage.objects.filter(inquiry=inq, body="Great, thanks!", from_client=True).exists())
        # another device (even on the same network / IP) sees nothing and can't write into it
        other = self.client_class()
        r = other.get(page + f"?c={inq.pk}")
        self.assertNotContains(r, "Is the garden fenced?")
        self.assertNotContains(r, "Yes, fully fenced.")
        self.assertEqual(other.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "sneaky"}).status_code, 404)
        # a made-up device cookie doesn't help either
        other.cookies["tp_device"] = "x" * 43
        self.assertNotContains(other.get(page), "Is the garden fenced?")
        self.assertFalse(InquiryMessage.objects.filter(body="sneaky").exists())

    def test_client_requests_only_visible_to_their_agent(self):
        """A client's inquiry / visit request goes to the property's agent only, never to other agents."""
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"on_whatsapp": "on", "name": "Private Client", "phone": "0788000111", "email": "pc@example.com",
                          "message": "I want to buy this."})
        inq = Inquiry.objects.get(name="Private Client")
        appt = Appointment.objects.create(property=self.prop, name="Private Visitor", phone="0788000222",
                                          scheduled_for=timezone.now() + timedelta(days=2))
        self.login(self.agent)
        self.assertContains(self.client.get(reverse("dashboard:inquiries")), "Private Client")
        self.assertContains(self.client.get(reverse("dashboard:appointments")), "Private Visitor")
        self.login(self.agent2)
        for name in ["dashboard:inquiries", "dashboard:appointments", "dashboard:clients"]:
            r = self.client.get(reverse(name))
            self.assertNotContains(r, "Private Client", msg_prefix=name)
            self.assertNotContains(r, "Private Visitor", msg_prefix=name)
        self.assertEqual(self.client.get(reverse("dashboard:inquiry_detail", args=[inq.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:appointment_detail", args=[appt.pk])).status_code, 404)
        # Requests on an unclaimed property: only admins see them, no agent does ...
        self.client.logout()
        early = Inquiry.objects.create(property=self.unclaimed, name="Early Buyer", phone="0788000333",
                                       email="eb@example.com", message="Hi")
        for agent in (self.agent, self.agent2):
            self.login(agent)
            self.assertNotContains(self.client.get(reverse("dashboard:inquiries")), "Early Buyer")
        self.login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard:inquiries")), "Early Buyer")
        # ... until the admin assigns it: then they go to that agent only
        self.assign(self.unclaimed, self.agent2)
        self.login(self.agent2)
        self.assertContains(self.client.get(reverse("dashboard:inquiries")), "Early Buyer")
        self.login(self.agent)
        self.assertNotContains(self.client.get(reverse("dashboard:inquiries")), "Early Buyer")
        # Admin moves the property to another agent: open requests follow the property
        self.unclaimed.refresh_from_db()
        self.unclaimed.agent = self.agent
        self.unclaimed.save()
        early.refresh_from_db()
        self.assertEqual(early.assigned_agent, self.agent)
        self.login(self.agent2)
        self.assertNotContains(self.client.get(reverse("dashboard:inquiries")), "Early Buyer")

    def test_uploaded_photos_are_normalised(self):
        buf = BytesIO()
        Image.new("RGBA", (3000, 4000), "blue").save(buf, "PNG")
        img = PropertyImage.objects.create(property=self.prop,
                                           image=SimpleUploadedFile("tall.png", buf.getvalue(), "image/png"))
        self.assertTrue(img.image.name.endswith(".jpg"))
        with Image.open(img.image.path) as saved:
            self.assertEqual(saved.size, (1200, 1600))
            self.assertEqual(saved.format, "JPEG")

    def test_big_phone_photo_is_accepted(self):
        """No MB limit: a >5 MB, 48-megapixel phone JPEG is accepted and stored as a 1600px JPEG."""
        from core.utils import validate_image_file
        noise = Image.frombytes("RGB", (1600, 1200), os.urandom(1600 * 1200 * 3)).resize((8000, 6000))
        buf = BytesIO()
        noise.save(buf, "JPEG", quality=95)
        self.assertGreater(len(buf.getvalue()), 5 * 1024 * 1024)
        upload = SimpleUploadedFile("big.jpg", buf.getvalue(), "image/jpeg")
        validate_image_file(upload)  # no "smaller than 5 MB" error any more
        img = PropertyImage.objects.create(property=self.prop, image=upload)
        with Image.open(img.image.path) as saved:
            self.assertEqual(max(saved.size), 1600)

    def test_session_favorites(self):
        url = reverse("favorites:toggle", args=[self.prop.pk])
        self.assertTrue(self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()["favorited"])
        self.assertContains(self.client.get(reverse("favorites:list")), self.prop.title)
        self.assertFalse(self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()["favorited"])
        self.assertNotContains(self.client.get(reverse("favorites:list")), self.prop.title)

    # ---------- owners submit without an account ----------
    def test_owner_public_submission(self):
        r = self.client.post(SUBMIT_HOUSE, submission_data())
        self.assertRedirects(r, reverse("properties:submit_done"))
        p = Property.objects.get(title="3-bedroom house in Kanombe")
        self.assertEqual((p.approval_status, p.source, p.agent), ("pending", "website", None))
        self.assertEqual(p.images.count(), 2)
        # the admin is told about it (to assign an agent); agents are not
        self.assertTrue(Notification.objects.filter(user=self.admin, message__contains=p.title).exists())
        for u in (self.agent, self.agent2):
            self.assertFalse(Notification.objects.filter(user=u, message__contains=p.title).exists(), u)
        self.assertTrue(any(p.reference in m.body for m in mail.outbox))  # owner gets reference by email
        # owner checks status with reference + phone in another format
        r = self.client.get(reverse("properties:status"), {"reference": p.reference, "phone": "+250788123456"})
        self.assertEqual(r.context["property"], p)
        r = self.client.get(reverse("properties:status"), {"reference": p.reference, "phone": "0700000000"})
        self.assertIsNone(r.context["property"])

    def test_owner_must_choose_type_first(self):
        r = self.client.get(reverse("properties:submit"))
        self.assertTemplateUsed(r, "properties/submit_choose.html")
        for label in ("House", "Apartment", "Plot", "Land", "Commercial", "For sale", "For rent"):
            self.assertContains(r, label)
        r = self.client.get(SUBMIT_HOUSE)
        self.assertTemplateUsed(r, "properties/submit.html")
        r = self.client.get(SUBMIT_HOUSE + "&change=1")
        self.assertTemplateUsed(r, "properties/submit_choose.html")

    def test_unsupported_types_are_refused(self):
        for query in ("?type=villa&purpose=sale", "?type=office&purpose=rent", "?type=boat&purpose=sale",
                      "?type=house&purpose=lease", "?type=house", ""):
            r = self.client.post("/properties/submit/" + query, submission_data(title=f"Bad {query}"))
            self.assertTemplateUsed(r, "properties/submit_choose.html")
        self.assertFalse(Property.objects.filter(title__startswith="Bad").exists())
        # property_type in the POST body can't override the chosen type
        self.client.post(SUBMIT_HOUSE, submission_data(property_type="villa", purpose="rent"))
        p = Property.objects.get(title="3-bedroom house in Kanombe")
        self.assertEqual((p.property_type, p.purpose), ("house", "sale"))

    def test_form_adapts_to_type(self):
        r = self.client.get("/properties/submit/?type=plot&purpose=sale")
        form = r.context["form"]
        for absent in ("bedrooms", "bathrooms", "parking_spaces", "furnished", "rent_period", "district",
                       "latitude", "longitude"):
            self.assertNotIn(absent, form.fields)
        self.assertTrue(form.fields["size_sqm"].required)
        # a plot without size is rejected; with size it's saved with no rooms
        self.client.post("/properties/submit/?type=plot&purpose=sale",
                         submission_data(title="Plot no size", size_sqm="", bedrooms=5))
        self.assertFalse(Property.objects.filter(title="Plot no size").exists())
        self.client.post("/properties/submit/?type=plot&purpose=sale",
                         submission_data(title="Plot in Rebero", size_sqm="600", bedrooms=5))
        p = Property.objects.get(title="Plot in Rebero")
        self.assertEqual((p.property_type, p.bedrooms, p.size_sqm), ("plot", 0, 600))
        # rentals must say how rent is paid
        self.assertTrue(self.client.get("/properties/submit/?type=apartment&purpose=rent")
                        .context["form"].fields["rent_period"].required)
        self.client.post("/properties/submit/?type=apartment&purpose=rent",
                         submission_data(title="Flat to rent", price="400000", rent_period="month"))
        self.assertEqual(Property.objects.get(title="Flat to rent").rent_period, "month")
        # plots show up on the public "Plots & Land" page once published
        Property.objects.filter(title="Plot in Rebero").update(approval_status="approved")
        self.assertContains(self.client.get(reverse("properties:land")), "Plot in Rebero")

    def test_submission_requires_photo_and_blocks_bots(self):
        self.client.post(SUBMIT_HOUSE, submission_data(new_images=[]))
        self.client.post(SUBMIT_HOUSE, submission_data(website="http://spam"))
        bad = SimpleUploadedFile("evil.php", b"<?php echo 1; ?>", content_type="image/jpeg")
        self.client.post(SUBMIT_HOUSE, submission_data(new_images=[bad]))
        self.assertFalse(Property.objects.filter(title="3-bedroom house in Kanombe").exists())

    def test_submission_rate_limit(self):
        for i in range(6):
            self.client.post(SUBMIT_HOUSE, submission_data(title=f"House {i}"))
        self.assertEqual(Property.objects.filter(title__startswith="House ").count(), 5)

    # ---------- agents: only their own listings; admin monitors and assigns ----------
    def test_agents_only_see_their_own_listings(self):
        self.login(self.agent2)
        r = self.client.get(reverse("dashboard:properties"))
        mine = Property.objects.filter(agent=self.agent2)
        self.assertEqual(r.context["page_obj"].paginator.count, mine.count())
        self.assertTrue(all(p.agent == self.agent2 for p in r.context["page_obj"]))
        self.assertNotContains(r, "All properties")
        self.assertContains(r, "My listings")
        # someone else's listing or an unassigned submission: not visible, not editable
        other = Property.objects.filter(agent=self.agent).first()
        for prop in (other, self.unclaimed):
            self.assertEqual(self.client.get(reverse("dashboard:property_edit", args=[prop.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("dashboard:property_share", args=[prop.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard:properties"), {"assigned": "unclaimed"})
                         .context["page_obj"].paginator.count, 0)
        r = self.client.get(reverse("dashboard:owners"))
        self.assertNotContains(r, other.owner_phone)
        # the admin sees everything, including unassigned submissions
        self.login(self.admin)
        r = self.client.get(reverse("dashboard:properties"))
        self.assertEqual(r.context["page_obj"].paginator.count, Property.objects.count())
        r = self.client.get(reverse("dashboard:properties"), {"assigned": "unclaimed"})
        self.assertContains(r, "Assign to")

    def test_only_admin_assigns_properties(self):
        self.login(self.agent2)
        r = self.client.post(reverse("dashboard:property_assign", args=[self.unclaimed.pk]), {"agent": self.agent2.pk})
        self.assertEqual(r.status_code, 403)
        self.unclaimed.refresh_from_db()
        self.assertIsNone(self.unclaimed.agent)
        self.assign(self.unclaimed, self.agent2)
        self.assertTrue(Notification.objects.filter(user=self.agent2, title="New property assigned to you").exists())
        self.login(self.agent2)
        self.assertEqual(self.client.get(reverse("dashboard:property_edit", args=[self.unclaimed.pk])).status_code, 200)
        self.login(self.agent)
        self.assertEqual(self.client.get(reverse("dashboard:property_edit", args=[self.unclaimed.pk])).status_code, 403)
        # agent2 lacks publish permission -> admin publishes
        self.login(self.agent2)
        r = self.client.post(reverse("dashboard:property_status", args=[self.unclaimed.pk, "approved"]))
        self.assertEqual(r.status_code, 403)
        self.login(self.admin)
        self.client.post(reverse("dashboard:property_status", args=[self.unclaimed.pk, "approved"]))
        self.unclaimed.refresh_from_db()
        self.assertEqual(self.unclaimed.approval_status, "approved")
        # releasing sends it back to the admin
        self.login(self.agent2)
        self.client.post(reverse("dashboard:property_release", args=[self.unclaimed.pk]))
        self.unclaimed.refresh_from_db()
        self.assertIsNone(self.unclaimed.agent)
        self.assertTrue(Notification.objects.filter(user=self.admin, title="Property released").exists())

    def test_agent_with_permission_publishes_own_listing(self):
        self.assign(self.unclaimed, self.agent)  # demo agent has can_publish_directly
        self.login(self.agent)
        self.client.post(reverse("dashboard:property_status", args=[self.unclaimed.pk, "approved"]))
        self.unclaimed.refresh_from_db()
        self.assertEqual(self.unclaimed.approval_status, "approved")

    def test_share_kit_and_photo_zip(self):
        self.client.post(SUBMIT_HOUSE, submission_data())
        p = Property.objects.get(title="3-bedroom house in Kanombe")
        self.assign(p, self.agent2)
        self.login(self.agent2)
        r = self.client.get(reverse("dashboard:property_share", args=[p.pk]))
        self.assertContains(r, "3-bedroom house in Kanombe")
        self.assertContains(r, "wa.me/?text=")
        self.assertNotIn(p.owner_phone, r.context["ad_text"])  # ad shows agent, not owner
        r = self.client.get(reverse("dashboard:property_photos", args=[p.pk]))
        self.assertEqual(r["Content-Type"], "application/zip")
        self.assertEqual(len(zipfile.ZipFile(BytesIO(r.content)).namelist()), 2)

    # ---------- hot deals / trending / new ----------
    def test_admin_picks_hot_deals_for_a_set_time(self):
        old = Property.objects.get(title="3-bedroom house in Kimironko")
        Property.objects.filter(pk=old.pk).update(listed_at=timezone.now() - timedelta(days=75))  # posted 2.5 months ago
        url = reverse("dashboard:property_hot_deal", args=[old.pk])
        # agents can't, admins can
        self.login(self.agent)
        self.assertEqual(self.client.post(url, {"days": "7"}).status_code, 403)
        self.login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard:property_edit", args=[old.pk])), "Make it a hot deal")
        price = old.price
        self.client.post(url, {"days": "7"})
        old.refresh_from_db()
        self.assertTrue(old.is_hot_deal)
        self.assertEqual(old.price, price)  # no discount: the price is never touched
        self.assertAlmostEqual((old.hot_deal_until - timezone.now()).total_seconds(), 7 * 86400, delta=60)
        # shown first on the website despite its age, labelled "Hot deal" (no "% below market")
        r = self.client_class().get(reverse("properties:list"))
        self.assertEqual(list(r.context["page_obj"])[0].is_hot_deal, True)
        self.assertIn(old, list(r.context["page_obj"])[:2])
        self.assertNotContains(r, "below market")
        self.assertIn(old, list(self.client_class().get(reverse("pages:home")).context["hot_deals"]))
        # an end date instead of a number of days
        day = (timezone.localdate() + timedelta(days=20)).isoformat()
        self.client.post(url, {"days": "7", "until": day})
        old.refresh_from_db()
        self.assertEqual(timezone.localtime(old.hot_deal_until).date().isoformat(), day)
        # out-of-range end dates are refused
        self.client.post(url, {"until": (timezone.localdate() - timedelta(days=1)).isoformat()})
        old.refresh_from_db()
        self.assertEqual(timezone.localtime(old.hot_deal_until).date().isoformat(), day)
        # when the time is over, it is an ordinary listing again
        Property.objects.filter(pk=old.pk).update(hot_deal_until=timezone.now() - timedelta(minutes=1))
        old.refresh_from_db()
        self.assertFalse(old.is_hot_deal)
        self.assertNotIn(old, list(self.client_class().get(reverse("properties:deals")).context["page_obj"]))
        # stop now; sold listings are never hot deals
        self.client.post(url, {"days": "7"})
        self.client.post(url, {"action": "stop"})
        old.refresh_from_db()
        self.assertIsNone(old.hot_deal_until)
        self.client.post(url, {"days": "7"})
        Property.objects.filter(pk=old.pk).update(availability=Property.Availability.SOLD)
        old.refresh_from_db()
        self.assertFalse(old.is_hot_deal)

    def test_recommended_order_and_pages(self):
        r = self.client.get(reverse("properties:list"))
        props = list(r.context["page_obj"])
        hot_flags = [p.is_hot_deal for p in props]
        self.assertTrue(hot_flags[0])
        self.assertEqual(hot_flags, sorted(hot_flags, reverse=True))  # all hot deals come first
        self.assertContains(r, "Hot deal")
        # visitor's own sort still wins
        r = self.client.get(reverse("properties:list"), {"sort": "-price"})
        prices = [p.price for p in r.context["page_obj"]]
        self.assertEqual(prices, sorted(prices, reverse=True))
        # hot deals page
        r = self.client.get(reverse("properties:deals"))
        self.assertTrue(all(p.is_hot_deal for p in r.context["page_obj"]))
        self.assertEqual(r.context["page_obj"].paginator.count, Property.objects.filter(Property.hot_deal_q()).count())
        # home page sections
        r = self.client.get(reverse("pages:home"))
        self.assertTrue(r.context["hot_deals"])
        self.assertTrue(r.context["trending"])
        self.assertContains(r, "Hot deals")
        self.assertContains(r, "Trending now")
        self.assertContains(r, "Just listed")
        hot = Property.objects.filter(Property.hot_deal_q()).first()
        self.assertContains(self.client.get(hot.get_absolute_url()), "Hot deal")

    def test_month_old_listing_reminder_and_repost(self):
        from properties.reminders import send_reminders
        prop = self.prop
        Property.objects.filter(pk=prop.pk).update(listed_at=timezone.now() - timedelta(days=31))
        mail.outbox = []
        self.assertGreaterEqual(send_reminders(), 1)
        note = Notification.objects.filter(user=self.agent, title__startswith=f"Check your listing: {prop.reference}")
        self.assertTrue(note.exists())
        self.assertTrue(any(self.agent.email in m.to for m in mail.outbox))
        # not again the next day; again after a week if nothing was done
        n = note.count()
        send_reminders()
        self.assertEqual(note.count(), n)
        Property.objects.filter(pk=prop.pk).update(reminder_sent_at=timezone.now() - timedelta(days=8))
        send_reminders()
        self.assertEqual(note.count(), n + 1)
        # the agent sees it on the dashboard and on the listing, and reposts it
        self.login(self.agent)
        self.assertContains(self.client.get(reverse("dashboard:home")), prop.reference)
        self.assertContains(self.client.get(reverse("dashboard:properties"), {"assigned": "check"}), prop.reference)
        self.assertContains(self.client.get(reverse("dashboard:property_edit", args=[prop.pk])), "Repost (still on offer)")
        self.client.post(reverse("dashboard:property_repost", args=[prop.pk]))
        prop.refresh_from_db()
        self.assertTrue(prop.is_new)
        self.assertNotIn(prop, list(self.client.get(reverse("dashboard:properties"), {"assigned": "check"}).context["page_obj"]))
        send_reminders()
        self.assertEqual(note.count(), n + 1)
        # another agent can't repost it
        self.login(self.agent2)
        self.assertEqual(self.client.post(reverse("dashboard:property_repost", args=[prop.pk])).status_code, 403)
        # listings with no active agent: the admins are reminded
        Property.objects.filter(pk=prop.pk).update(listed_at=timezone.now() - timedelta(days=40), agent=None,
                                                   reminder_sent_at=None)
        send_reminders()
        self.assertTrue(Notification.objects.filter(user=self.admin, title__startswith=f"Check your listing: {prop.reference}").exists())

    def test_featured_boosts_trending(self):
        from properties.ranking import refresh_scores
        p = Property.objects.get(title="450 m² plot in Gatenga")
        before = p.trend_score
        Property.objects.filter(pk=p.pk).update(is_featured=True)
        refresh_scores()
        p.refresh_from_db()
        self.assertAlmostEqual(p.trend_score, before * 2, delta=before * 0.02)  # (age moves on a little while tests run)
        self.assertTrue(p.is_new)

    # ---------- "Agent replies" chat box ----------
    def test_chat_box_on_property_page(self):
        page = self.client.get(self.prop.get_absolute_url())
        for text in ("Ask about this property and get a reply right here.", "Is this property still available?",
                     "Can we schedule a time to view it?", "Is the price negotiable?"):
            self.assertContains(page, text)
        self.assertContains(page, 'placeholder="Write your name"')  # the name is asked...
        self.assertNotContains(page, 'placeholder="Phone or email (optional)"')  # ...but no phone or email
        send = reverse("inquiries:chat_send", args=[self.prop.slug])
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_USER_AGENT": "Mozilla/5.0 (Linux; Android 14) Mobile Chrome/120"}
        self.assertEqual(self.client.post(send, {"name": "Eric", "message": ""}, **ajax).status_code, 400)
        no_name = self.client.post(send, {"name": " ", "message": "Hi"}, **ajax)
        self.assertEqual(no_name.status_code, 400)
        self.assertIn("write your name", no_name.json()["error"])
        r = self.client.post(send, {"name": "  Eric   Mugabo ", "message": "Is this property still available?"}, **ajax)
        data = r.json()
        self.assertTrue(data["ok"])
        inq = Inquiry.objects.get(pk=data["conversation"])
        self.assertEqual((inq.name, inq.phone, inq.email, inq.assigned_agent, inq.device_info),
                         ("Eric Mugabo", "", "", self.agent, "Android phone · Chrome"))
        self.assertTrue(Notification.objects.filter(user=self.agent, title="New property inquiry").exists())
        # the agent answers in the dashboard; the reply shows in the box (polling)
        agent = self.client_class()
        agent.force_login(self.agent)
        agent.post(reverse("dashboard:inquiry_detail", args=[inq.pk]), {"status": "new", "reply": "Yes, it is."})
        poll = self.client.get(reverse("inquiries:chat_poll", args=[inq.pk]), **ajax).json()
        self.assertEqual([m["text"] for m in poll["messages"]], ["Is this property still available?", "Yes, it is."])
        # the next message continues the same conversation; the page shows it when reopened
        data = self.client.post(send, {"message": "Can we schedule a time to view it?"}, **ajax).json()  # no name again
        self.assertEqual(data["conversation"], inq.pk)
        self.assertEqual(len(data["messages"]), 3)
        self.assertContains(self.client.get(self.prop.get_absolute_url()), "Can we schedule a time to view it?")
        other = self.client_class()
        # nobody else can read the conversation
        self.assertEqual(other.get(reverse("inquiries:chat_poll", args=[inq.pk])).status_code, 404)
        # elsewhere the box lists this device's conversations
        self.assertContains(self.client.get(reverse("pages:about")), f"?c={inq.pk}")

    def test_agent_on_the_same_phone_never_writes_as_the_client(self):
        """An agent signed in on the phone that sent a question can't write in the public chat box (it would land
        in that phone's own conversation, not the client's). The box lists the clients to answer in the dashboard."""
        send = reverse("inquiries:chat_send", args=[self.prop.slug])
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        mine = Inquiry.objects.get(pk=self.client.post(send, {"name": "Paul", "message": "Can we schedule a time to view it?"}, **ajax).json()["conversation"])
        other = self.client_class()
        theirs = Inquiry.objects.get(pk=other.post(send, {"name": "Grace", "message": "Can we schedule a time to view it?"},
                                                   HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                                                   HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Edg/120").json()["conversation"])
        self.client.force_login(self.agent)  # same phone, now signed in as the listing's agent
        r = self.client.post(send, {"message": "Tomorrow at 10am"}, **ajax)
        self.assertEqual(r.status_code, 403)
        self.client.post(reverse("inquiries:reply", args=[mine.pk]), {"body": "See you there"})
        self.assertEqual(InquiryMessage.objects.filter(inquiry__in=[mine, theirs]).count(), 0)
        # the chat box lists BOTH clients, each with its own label, linking to the dashboard reply
        page = self.client.get(self.prop.get_absolute_url())
        for inq in (mine, theirs):
            self.assertContains(page, inq.name)
            self.assertContains(page, reverse("dashboard:inquiry_detail", args=[inq.pk]) + "#reply")
        self.assertContains(page, "Needs an answer")
        # answering in the dashboard reaches the right client, who sees it (and the badge counts it)
        self.client.post(reverse("dashboard:inquiry_detail", args=[theirs.pk]), {"status": "new", "reply": "Tomorrow at 10am"})
        poll = other.get(reverse("inquiries:chat_poll", args=[theirs.pk])).json()
        self.assertEqual([(m["mine"], m["text"]) for m in poll["messages"]],
                         [(True, "Can we schedule a time to view it?"), (False, "Tomorrow at 10am")])
        InquiryMessage.objects.create(inquiry=theirs, author=self.agent, body="Bring your ID")
        self.assertEqual(other.get(reverse("inquiries:unread")).json()["unread"], 1)
        self.assertEqual(mine.thread.count(), 0)
        # another agent (not this listing's) sees no clients of this listing
        self.client.force_login(self.agent2)
        self.assertNotContains(self.client.get(self.prop.get_absolute_url()), theirs.name)

    # ---------- admin insights ----------
    def test_active_users_and_analytics(self):
        browser = {"HTTP_USER_AGENT": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile Safari/604.1"}
        visitor = self.client_class()
        visitor.get(reverse("pages:home"), **browser)
        visitor.get(self.prop.get_absolute_url(), **browser)
        self.client_class().get(reverse("pages:home"), HTTP_USER_AGENT="UptimeRobot/2.0")  # monitors not counted
        self.client_class().get("/healthz/", **browser)
        from core.models import SiteVisit
        v = SiteVisit.objects.get(user__isnull=True)
        self.assertEqual((v.hits, v.opens, v.last_path, v.device_kind), (2, 1, self.prop.get_absolute_url(), "Phone"))
        # coming back after more than 30 minutes = the site was opened again
        SiteVisit.objects.filter(pk=v.pk).update(last_seen=timezone.now() - timedelta(minutes=31))
        visitor.get(reverse("pages:home"), **browser)
        v.refresh_from_db()
        self.assertEqual((v.hits, v.opens), (3, 2))
        # a visitor who asked an agent is shown by name on Active users
        visitor.post(reverse("inquiries:submit", args=[self.prop.slug]),
                     {"name": "Diane", "phone": "0788111222", "message": "Hi", "on_whatsapp": "on"}, **browser)
        visitor.get(reverse("pages:about"), **browser)
        agent = self.client_class()
        agent.force_login(self.agent)
        agent.get(reverse("dashboard:home"), **browser)
        # admins only
        self.assertEqual(agent.get(reverse("dashboard:active_users")).status_code, 403)
        self.assertEqual(agent.get(reverse("dashboard:analytics")).status_code, 403)
        self.login(self.admin)
        r = self.client.get(reverse("dashboard:active_users"))
        self.assertContains(r, "iPhone · Safari")
        self.assertContains(r, "Diane")
        self.assertIn(self.agent.pk, r.context["online_ids"])
        self.assertEqual(len(r.context["visitors_online"]), 1)
        r = self.client.get(reverse("dashboard:analytics"), {"days": "7"})
        self.assertEqual(r.context["totals"]["opens"], 2)
        self.assertEqual(r.context["totals"]["clients"], Inquiry.objects.count())  # Diane + the demo client
        self.assertContains(r, "Diane")
        self.assertEqual(self.client.get(reverse("dashboard:export", args=["visits"]))["Content-Type"], "text/csv")

    def test_documents_are_private(self):
        url = reverse("dashboard:documents")
        pdf = SimpleUploadedFile("Sale agreement.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        self.login(self.admin)
        self.client.post(url, {"upload": pdf, "category": "contract", "property": self.prop.pk, "visibility": "admins"})
        secret = self.client.get(url).context["page_obj"][0]
        self.assertEqual((secret.title, secret.extension()), ("Sale agreement", "PDF"))
        r = self.client.get(reverse("dashboard:document_download", args=[secret.pk]))
        self.assertEqual((r.content, r["Content-Type"]), (b"%PDF-1.4 test", "application/pdf"))
        # bad file types are refused
        self.client.post(url, {"upload": SimpleUploadedFile("x.exe", b"MZ"), "category": "other", "visibility": "admins"})
        self.assertEqual(self.client.get(url).context["page_obj"].paginator.count, 1)
        shared = SimpleUploadedFile("Plan.png", b"png", content_type="image/png")
        self.client.post(url, {"upload": shared, "title": "Plot plan", "category": "plan", "property": self.prop.pk,
                               "visibility": "staff"})
        # the listing's agent sees only the shared one; other agents see nothing; visitors are sent to sign in
        self.login(self.agent)
        titles = [d.title for d in self.client.get(url).context["page_obj"]]
        self.assertEqual(titles, ["Plot plan"])
        self.assertEqual(self.client.get(reverse("dashboard:document_download", args=[secret.pk])).status_code, 404)
        self.login(self.agent2)
        self.assertEqual(list(self.client.get(url).context["page_obj"]), [])
        self.client.logout()
        self.assertEqual(self.client.get(reverse("dashboard:document_download", args=[secret.pk])).status_code, 302)

    def test_report_a_problem(self):
        page = self.client.get(self.prop.get_absolute_url())
        self.assertContains(page, f"/report/?property={self.prop.reference}")
        self.assertContains(self.client.get(reverse("pages:home")), "Report a problem")
        self.client.post(reverse("report_problem"), {"property": self.prop.reference, "kind": "scam",
                                                     "message": "The owner asked me to pay first."})
        from dashboard.models import ProblemReport
        report = ProblemReport.objects.get()
        self.assertEqual((report.property, report.status), (self.prop, "open"))
        self.assertTrue(Notification.objects.filter(user=self.admin, title="New problem report").exists())
        # staff report from Help
        self.login(self.agent)
        self.assertContains(self.client.get(reverse("dashboard:help")), "Report a problem")
        self.client.post(reverse("report_problem"), {"kind": "website", "message": "Upload is slow."})
        self.assertEqual(ProblemReport.objects.filter(reporter=self.agent).count(), 1)
        self.assertEqual(self.client.get(reverse("dashboard:problem_reports")).status_code, 403)
        # the admin sees them all and resolves one
        self.login(self.admin)
        r = self.client.get(reverse("dashboard:problem_reports"))
        self.assertContains(r, "The owner asked me to pay first.")
        self.assertContains(r, "Upload is slow.")
        self.client.post(reverse("dashboard:problem_report_action", args=[report.pk, "resolve"]),
                         {"note": "Listing removed."})
        report.refresh_from_db()
        self.assertEqual((report.status, report.admin_note, report.handled_by), ("resolved", "Listing removed.", self.admin))
        r = self.client.get(reverse("dashboard:reports"))
        self.assertContains(r, "At a glance")
        self.assertContains(r, "Open problem reports")
        for kind in ("agents", "problems"):
            self.assertEqual(self.client.get(reverse("dashboard:export", args=[kind]))["Content-Type"], "text/csv")

    # ---------- staff "Add property" form ----------
    def staff_data(self, **extra):
        data = {"property_type": "house", "purpose": "sale", "title": "Staff house", "price": "90000000",
                "currency": "RWF", "description": "Nice house.", "sector": "Kimironko",
                "bedrooms": "3", "bathrooms": "2", "owner_name": "Owner X", "owner_phone": "0788000222",
                "amenity_choices": ["Garden", "CCTV"], "other_amenities": "Rooftop terrace",
                "new_images": [make_image()]}
        data.update(extra)
        return data

    def test_staff_form_required_fields(self):
        self.login(self.admin)
        r = self.client.post(reverse("dashboard:property_create"), {"property_type": "house"})
        self.assertEqual(r.status_code, 200)
        errors = r.context["form"].errors
        for name in ("purpose", "title", "price", "description", "sector", "bedrooms", "bathrooms", "new_images"):
            self.assertIn(name, errors, name)
        for name in ("owner_name", "owner_phone", "owner_email"):  # owner details are optional
            self.assertNotIn(name, errors, name)
        self.assertContains(r, "is-invalid")
        # removed fields are no longer on the form
        for gone in ("category", "village", "year_built", "video_url", "availability", "district", "latitude", "longitude"):
            self.assertNotIn(gone, r.context["form"].fields)

    def test_photo_storage_failure_shows_message_not_error_page(self):
        """If Cloudinary refuses an upload, the form says so and nothing is half-saved."""
        from unittest import mock
        self.login(self.admin)
        prop = self.prop
        url = reverse("dashboard:property_edit", args=[prop.pk])
        data = self.staff_data(title="Changed title", availability="available")
        photos_before = prop.images.count()
        with mock.patch("properties.models.PropertyImage.save", side_effect=RuntimeError("Invalid Signature")):
            r = self.client.post(url, data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "photos could not be uploaded")
        prop.refresh_from_db()
        self.assertNotEqual(prop.title, "Changed title")  # the whole save was undone
        self.assertEqual(prop.images.count(), photos_before)
        # adding a new listing: nothing is created
        with mock.patch("properties.models.PropertyImage.save", side_effect=RuntimeError("Invalid Signature")):
            r = self.client.post(reverse("dashboard:property_create"), self.staff_data(title="Never saved"))
        self.assertContains(r, "photos could not be uploaded")
        self.assertFalse(Property.objects.filter(title="Never saved").exists())

    def test_staff_form_saves_without_owner_details(self):
        self.login(self.admin)
        data = self.staff_data(title="No owner house")
        for name in ("owner_name", "owner_phone"):
            data.pop(name)
        self.client.post(reverse("dashboard:property_create"), data)
        p = Property.objects.get(title="No owner house")
        self.assertEqual((p.owner_name, p.owner_phone), ("", ""))

    def test_staff_form_skips_owner_fields_for_owner_submission(self):
        self.login(self.admin)
        self.client.post(reverse("dashboard:property_create"), self.staff_data(title="Owner sent"))
        p = Property.objects.get(title="Owner sent")
        Property.objects.filter(pk=p.pk).update(source=Property.Source.WEBSITE)
        r = self.client.get(reverse("dashboard:property_edit", args=[p.pk]))
        for name in ("owner_name", "owner_phone", "owner_email"):
            self.assertNotIn(name, r.context["form"].fields)
        data = self.staff_data(title="Owner sent v2", availability="available")
        for name in ("owner_name", "owner_phone", "new_images"):
            data.pop(name)
        self.client.post(reverse("dashboard:property_edit", args=[p.pk]), data)
        p.refresh_from_db()
        self.assertEqual((p.title, p.owner_name, p.owner_phone), ("Owner sent v2", "Owner X", "0788000222"))

    def test_staff_form_saves_with_auto_category_and_amenities(self):
        self.login(self.admin)
        r = self.client.post(reverse("dashboard:property_create"), self.staff_data())
        p = Property.objects.get(title="Staff house")
        self.assertRedirects(r, reverse("dashboard:property_edit", args=[p.pk]))
        self.assertEqual(p.category.slug, "residential")
        self.assertEqual(p.amenities, "Garden, CCTV, Rooftop terrace")
        self.assertEqual((p.approval_status, p.images.count()), ("approved", 1))
        # editing: existing photo satisfies the photo rule; amenities pre-fill
        r = self.client.get(reverse("dashboard:property_edit", args=[p.pk]))
        self.assertEqual(r.context["form"].initial["amenity_choices"], ["Garden", "CCTV"])
        data = self.staff_data(title="Staff house v2", availability="sold")
        data.pop("new_images")
        self.client.post(reverse("dashboard:property_edit", args=[p.pk]), data)
        p.refresh_from_db()
        self.assertEqual((p.title, p.availability), ("Staff house v2", "sold"))

    def test_staff_form_type_rules(self):
        self.login(self.admin)
        # plot: size required, rooms cleared
        r = self.client.post(reverse("dashboard:property_create"),
                             self.staff_data(property_type="plot", title="Staff plot", bedrooms="", bathrooms=""))
        self.assertIn("size_sqm", r.context["form"].errors)
        self.client.post(reverse("dashboard:property_create"),
                         self.staff_data(property_type="plot", title="Staff plot", size_sqm="500", bedrooms="4"))
        p = Property.objects.get(title="Staff plot")
        self.assertEqual((p.bedrooms, p.bathrooms, p.category.slug), (0, 0, "land-plots"))
        # rent: rent period required
        r = self.client.post(reverse("dashboard:property_create"),
                             self.staff_data(purpose="rent", title="Staff rent"))
        self.assertIn("rent_period", r.context["form"].errors)
        # unsupported type refused
        r = self.client.post(reverse("dashboard:property_create"),
                             self.staff_data(property_type="boat", title="Staff boat"))
        self.assertIn("property_type", r.context["form"].errors)

    # ---------- access control ----------
    def test_dashboard_requires_staff_login(self):
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 302)
        legacy = User.objects.create_user("legacy", "l@example.com", PW, role="client")
        self.login(legacy)
        self.assertEqual(self.client.get(reverse("dashboard:home")).status_code, 403)

    def test_agent_blocked_from_admin_areas(self):
        self.login(self.agent)
        for name, kw in [("dashboard:users", {"role": "agent"}), ("dashboard:payments", {}),
                         ("dashboard:settings", {}), ("dashboard:audit", {}),
                         ("dashboard:property_review", {"pk": self.prop.pk})]:
            self.assertEqual(self.client.get(reverse(name, kwargs=kw)).status_code, 403, name)
        # deleting: only their own listings (see test_agent_manages_only_own_listings)
        other = Property.objects.exclude(agent=self.agent).first()
        self.assertEqual(self.client.post(reverse("dashboard:property_delete", args=[other.pk])).status_code, 403)

    def test_all_dashboard_pages(self):
        inq, appt = Inquiry.objects.first(), Appointment.objects.first()
        pages = [("dashboard:home", {}), ("dashboard:properties", {}), ("dashboard:property_create", {}),
                 ("dashboard:property_edit", {"pk": self.prop.pk}), ("dashboard:property_share", {"pk": self.prop.pk}),
                 ("dashboard:clients", {}), ("dashboard:owners", {}), ("dashboard:inquiries", {}),
                 ("dashboard:inquiry_detail", {"pk": inq.pk}), ("dashboard:appointments", {}),
                 ("dashboard:appointment_detail", {"pk": appt.pk}), ("dashboard:reports", {})]
        admin_pages = [("dashboard:property_review", {"pk": self.unclaimed.pk}), ("dashboard:users", {"role": "agent"}),
                       ("dashboard:user_detail", {"pk": self.agent.pk}), ("dashboard:agent_create", {}),
                       ("dashboard:payments", {}), ("dashboard:payment_create", {}), ("dashboard:settings", {}),
                       ("dashboard:messages", {}), ("dashboard:audit", {})]
        for user, urls in ((self.admin, pages + admin_pages), (self.agent, pages)):
            self.login(user)
            for name, kw in urls:
                self.assertEqual(self.client.get(reverse(name, kwargs=kw)).status_code, 200, (user, name))
        self.login(self.admin)
        for kind in ["properties", "inquiries", "appointments", "payments"]:
            self.assertEqual(self.client.get(reverse("dashboard:export", args=[kind]))["Content-Type"], "text/csv")
        r = self.client.get(reverse("dashboard:clients"))
        self.assertContains(r, "Grace Mukamana")
        self.assertContains(self.client.get(reverse("dashboard:owners")), "Jean Habimana")

    def test_agent_replies_to_inquiry_by_email(self):
        inq = Inquiry.objects.create(property=self.prop, name="Bob", email="bob@example.com", message="Hi",
                                     assigned_agent=self.agent)
        self.login(self.agent)
        self.client.post(reverse("dashboard:inquiry_detail", args=[inq.pk]),
                         {"status": "new", "reply": "Yes, it is available."})
        inq.refresh_from_db()
        self.assertEqual(inq.status, "responded")
        self.assertTrue(any("bob@example.com" in m.to for m in mail.outbox))

    def test_client_and_agent_keep_talking(self):
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"on_whatsapp": "on", "name": "Visitor", "email": "v@example.com", "phone": "0788000111", "message": "Hi"})
        inq = Inquiry.objects.get(phone="0788000111")
        visitor = self.client
        # agent replies twice over time
        agent = self.client_class()
        agent.force_login(self.agent)
        for text in ("It is available.", "Viewing is possible on Saturday."):
            agent.post(reverse("dashboard:inquiry_detail", args=[inq.pk]), {"status": inq.status, "reply": text})
        # client answers from the panel, agent is notified and sees it in the thread
        r = visitor.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "Saturday 10am works."})
        self.assertRedirects(r, reverse("inquiries:conversations") + f"?c={inq.pk}#reply", fetch_redirect_response=False)
        page = visitor.get(reverse("inquiries:conversations") + f"?c={inq.pk}")
        for text in ("It is available.", "Viewing is possible on Saturday.", "Saturday 10am works."):
            self.assertContains(page, text)
        self.assertTrue(Notification.objects.filter(user=self.agent, title="New message from a client").exists())
        self.assertContains(agent.get(reverse("dashboard:inquiry_detail", args=[inq.pk])), "Saturday 10am works.")
        inq.refresh_from_db()
        self.assertEqual((inq.status, inq.thread.count()), ("in_progress", 3))
        # nobody else can write into this conversation
        stranger = self.client_class()
        stranger.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "spam"})
        self.assertEqual(InquiryMessage.objects.filter(body="spam").count(), 0)

    def test_agent_confirms_visit(self):
        appt = Appointment.objects.first()
        self.login(self.agent)
        when = (timezone.localtime() + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(reverse("dashboard:appointment_detail", args=[appt.pk]),
                         {"status": "confirmed", "scheduled_for": when, "staff_note": "See you there"})
        appt.refresh_from_db()
        self.assertEqual(appt.status, "confirmed")
        self.assertTrue(any(appt.email in m.to for m in mail.outbox))

    # ---------- payments ----------
    def test_mobile_money_needs_provider_confirmation(self):
        pay = Payment.objects.create(payer_name="Grace", amount=1000, method="mtn_momo")
        pay.status = Payment.Status.SUCCESSFUL
        with self.assertRaises(ValidationError):
            pay.save()

    def test_cash_payment_verified_by_staff(self):
        self.login(self.admin)
        self.client.post(reverse("dashboard:payment_create"), {
            "payer_name": "Grace", "payer_phone": "0788555666", "payer_email": "grace@example.com",
            "amount": "500000", "currency": "RWF", "method": "cash", "purpose": "rent"})
        pay = Payment.objects.get(payer_name="Grace")
        self.assertEqual(pay.status, "pending")
        self.client.post(reverse("dashboard:payment_action", args=[pay.pk, "verify"]))
        pay.refresh_from_db()
        self.assertEqual((pay.status, pay.verified_by), ("successful", self.admin))

    # ---------- REST API ----------
    def test_api(self):
        r = self.client.get("/api/v1/properties/?purpose=sale")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(p["purpose"] == "sale" for p in r.json()["results"]))
        self.assertNotIn(self.unclaimed.slug, [p["slug"] for p in r.json()["results"]])
        r = self.client.post("/api/v1/inquiries/", {"property": self.prop.pk, "name": "API", "email": "a@b.co",
                                                    "message": "hi"})
        self.assertEqual(r.status_code, 201)
        when = (timezone.now() + timedelta(days=2)).isoformat()
        r = self.client.post("/api/v1/appointments/", {"property": self.prop.pk, "name": "App",
                                                       "phone": "0788", "scheduled_for": when})
        self.assertEqual(r.status_code, 201)
        r = self.client.post("/api/v1/auth/token/", {"username": "agent", "password": PW})
        self.assertIn("token", r.json())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", MEDIA_ROOT=TEST_MEDIA)
class SecurityTests(TestCase):
    """Input cleaning, redirects, brute force, uploads, CSV export and error handling."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", stdout=StringIO())
        cls.admin = User.objects.get(username="admin")
        cls.agent = User.objects.get(username="agent")
        cls.prop = Property.objects.filter(approval_status="approved", agent=cls.agent).first()

    def setUp(self):
        cache.clear()

    def test_html_and_scripts_are_not_stored(self):
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"on_whatsapp": "on", "name": "<script>alert(1)</script>Eric​", "phone": "0788000111",
                          "email": "e@example.com", "message": "Is <b>it</b> free? Price < 5M"})
        inq = Inquiry.objects.latest("pk")
        self.assertEqual(inq.name, "alert(1)Eric")
        self.assertEqual(inq.message, "Is it free? Price < 5M")
        r = self.client.post("/api/v1/inquiries/", {"property": self.prop.pk, "name": "<img src=x onerror=alert(1)>Ann",
                                                    "email": "a@b.co", "message": "hi"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Inquiry.objects.latest("pk").name, "Ann")

    def test_sql_injection_attempts_are_harmless(self):
        payload = "' OR 1=1; DROP TABLE properties_property; --"
        for url in (f"/properties/?q={payload}", f"/properties/?location={payload}", f"/properties/?sort={payload}",
                    f"/properties/?min_price={payload}", f"/api/v1/properties/?search={payload}"):
            self.assertIn(self.client.get(url).status_code, (200, 400), url)
        self.login_as(self.admin)
        self.assertEqual(self.client.get(f"/dashboard/properties/?q={payload}").status_code, 200)
        self.assertTrue(Property.objects.exists())

    def login_as(self, user):
        self.client.force_login(user)

    def test_no_redirect_to_other_websites(self):
        self.login_as(self.admin)
        r = self.client.post(reverse("dashboard:property_status", args=[self.prop.pk, "approved"]),
                             {"next": "https://evil.example.com/steal"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(r["Location"].startswith("http"))
        r = self.client.post(reverse("favorites:toggle", args=[self.prop.pk]), {"next": "//evil.example.com"})
        self.assertEqual(r["Location"], self.prop.get_absolute_url())

    def test_login_locked_after_too_many_wrong_passwords(self):
        url = reverse("accounts:login")
        for _ in range(5):
            self.client.post(url, {"username": "agent", "password": "wrong"})
        r = self.client.post(url, {"username": "agent", "password": PW})  # even the right password waits
        self.assertContains(r, "Too many failed login attempts")
        self.assertNotIn("_auth_user_id", self.client.session)
        cache.clear()
        self.client.post(url, {"username": "agent", "password": PW})
        self.assertNotIn("_auth_user_id", self.client.session)  # correct password: now the emailed code
        finish_sign_in(self.client)
        self.assertIn("_auth_user_id", self.client.session)

    def test_public_forms_are_rate_limited(self):
        data = {"name": "Spam", "phone": "0788000111", "email": "s@example.com", "message": "buy", "on_whatsapp": "on"}
        for _ in range(12):
            self.client.post(reverse("inquiries:submit", args=[self.prop.slug]), data)
        self.assertEqual(Inquiry.objects.filter(name="Spam").count(), 10)

    def test_fake_images_rejected(self):
        from core.utils import validate_image_file
        fake = SimpleUploadedFile("house.jpg", b"<?php system($_GET['c']); ?>", content_type="image/jpeg")
        with self.assertRaises(ValidationError):
            validate_image_file(fake)
        with self.assertRaises(ValidationError):
            validate_image_file(SimpleUploadedFile("run.exe", b"MZ", content_type="image/jpeg"))
        validate_image_file(make_image())  # a real photo passes

    def test_csv_export_neutralises_formulas(self):
        Inquiry.objects.create(property=self.prop, name="=HYPERLINK(\"http://evil\",\"x\")", phone="+250 788 000 111",
                               email="x@example.com", message="hi")
        self.login_as(self.admin)
        body = self.client.get(reverse("dashboard:export", args=["inquiries"])).content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertIn("+250 788 000 111", body)  # phone numbers stay normal
        self.assertNotIn("'+250", body)

    def test_spoofed_ip_header_ignored(self):
        from core.security import client_ip
        from django.test import RequestFactory
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.5")
        self.assertEqual(client_ip(req), "10.0.0.5")
        with self.settings(TRUST_X_FORWARDED_FOR=True):
            self.assertEqual(client_ip(req), "1.2.3.4")

    def test_friendly_error_pages(self):
        r = self.client.get("/no-such-page/")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "Page not found", status_code=404)
        from django.test import Client
        csrf_client = Client(enforce_csrf_checks=True)
        r = csrf_client.post(reverse("inquiries:submit", args=[self.prop.slug]), {"name": "x"})
        self.assertContains(r, "Please try again", status_code=403)
        from django.template.loader import render_to_string
        self.assertIn("Sorry, something went wrong", render_to_string("500.html"))

    def test_private_pages_not_kept_by_browser(self):
        """Back after logout must not show dashboard pages: they are never cached."""
        self.client.force_login(self.admin)
        r = self.client.get(reverse("dashboard:home"))
        self.assertIn("no-store", r["Cache-Control"])
        self.assertNotContains(r, 'data-back')  # no back button on the dashboard home
        r = self.client.get(reverse("dashboard:properties"))
        self.assertContains(r, 'data-back')
        r = self.client.post(reverse("accounts:logout"))
        self.assertEqual(r["Clear-Site-Data"], '"cache"')
        r = self.client.get(reverse("dashboard:properties"))  # what "Back" asks for after logout
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("accounts:login"), r["Location"])
        # public pages for visitors stay cacheable (fast) and have a Back link, except the home page
        r = self.client.get(self.prop.get_absolute_url())
        self.assertNotIn("no-store", r.get("Cache-Control", ""))
        self.assertContains(r, f'href="{reverse("properties:list")}" class="btn-back-link" data-back')
        self.assertNotContains(self.client.get(reverse("pages:home")), "btn-back-link")
