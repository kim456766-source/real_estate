"""End-to-end tests for Theo Property. Run: python manage.py test"""
import re
import shutil
import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO, StringIO

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import User
from appointments.models import Appointment
from inquiries.models import Inquiry, InquiryMessage
from notifications.models import Notification
from payments.models import Payment
from properties.models import Property, PropertyImage

PW = "TheoDemo#2026"
SUBMIT_HOUSE = "/properties/submit/?type=house&purpose=sale"


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

    def test_public_header_has_no_dashboard(self):
        dash = reverse("dashboard:home")
        for user in (None, self.admin, self.agent):
            if user:
                self.login(user)
            r = self.client.get(reverse("pages:home"))
            html = r.content.decode()
            header, after = html.split("</nav>", 1)
            self.assertNotIn(f'href="{dash}"', header, user)
            # "Sign In" sits top-right (no mention of "staff"); the owner call-to-action sits below the header
            self.assertIn("My account" if user else "Sign In", header)
            self.assertNotIn("Staff", header)
            self.assertNotIn("Submit your property", header)
            self.assertIn("Property owner?", after.split("<main>")[0])
            self.assertIn("Submit your property", after.split("<main>")[0])
        self.assertRegex(r.content.decode(), r"style\.css\?v=\d+")
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
        self.assertRedirects(r, reverse("accounts:after_login"), fetch_redirect_response=False)

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
                             {"name": "Visitor", "email": "v@example.com", "phone": "0788000111",
                              "message": "Is it available?"})
        self.assertEqual(r.status_code, 302)
        inq = Inquiry.objects.get(phone="0788000111")
        self.assertEqual(inq.assigned_agent, self.agent)
        self.assertTrue(Notification.objects.filter(user=self.agent, title="New property inquiry").exists())

    def test_inquiry_skips_deactivated_agent(self):
        """If the listing's agent is deactivated, the message goes to the admins instead of being lost."""
        User.objects.filter(pk=self.agent.pk).update(is_active=False)
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"name": "Visitor", "email": "v@example.com", "phone": "0788000111", "message": "Hello?"})
        self.assertFalse(Notification.objects.filter(user=self.agent, title="New property inquiry").exists())
        self.assertTrue(Notification.objects.filter(user=self.admin, title="New property inquiry").exists())

    def test_public_questions_and_answers(self):
        url = reverse("inquiries:submit", args=[self.prop.slug])
        # the form no longer asks for an email; phone is required
        r = self.client.post(url, {"name": "No Phone", "message": "Hi"})
        self.assertFalse(Inquiry.objects.filter(name="No Phone").exists())
        self.client.post(url, {"name": "Claudine Mukamana", "phone": "0788555444",
                               "message": "Is water available all year?"})
        Inquiry.objects.create(property=self.prop, name="Old Client", phone="0788999000",
                               message="Secret budget question")  # older conversations stay private
        pub = Inquiry.objects.get(phone="0788555444")
        self.assertTrue(pub.is_public)
        # the meeting code pops up once (like an OTP) right after sending, then never again for the client
        first = self.client.get(self.prop.get_absolute_url())
        self.assertContains(first, 'id="meetingCodeModal"')
        self.assertContains(first, pub.meeting_code)
        self.assertNotIn("email", first.context["inquiry_form"].fields)
        again = self.client.get(self.prop.get_absolute_url())
        self.assertNotContains(again, pub.meeting_code)
        self.assertNotContains(again, "Request a visit")
        InquiryMessage.objects.create(inquiry=pub, author=self.agent, body="Yes, there is a water tank.")
        stranger = self.client_class()
        page = stranger.get(self.prop.get_absolute_url())
        self.assertContains(page, "Is water available all year?")
        self.assertContains(page, "Yes, there is a water tank.")
        self.assertContains(page, "C. ·")  # initial only
        for secret in ("Claudine", "Mukamana", "0788555444", "Secret budget question", pub.meeting_code):
            self.assertNotContains(page, secret)
        # the agent can always see it in the dashboard, to check what the client tells them
        agent = self.client_class()
        agent.force_login(self.agent)
        self.assertContains(agent.get(reverse("dashboard:inquiry_detail", args=[pub.pk])), pub.meeting_code)
        other = Inquiry.objects.get(name="Old Client")
        self.assertNotEqual(other.meeting_code, pub.meeting_code)
        # agent can take it down again
        self.login(self.agent)
        self.client.post(reverse("dashboard:inquiry_detail", args=[pub.pk]), {"status": pub.status})
        self.assertNotContains(stranger.get(self.prop.get_absolute_url()), "Is water available all year?")

    # ---------- sold / rented, and old conversations ----------
    def test_marking_property_taken(self):
        prop = self.prop
        prop.agent = self.agent
        prop.save()
        self.client.post(reverse("inquiries:submit", args=[prop.slug]),
                         {"name": "Early Bird", "phone": "0788000444", "message": "Still available?"})
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
        self.assertNotContains(page, "Send inquiry")
        # new questions are refused; the open conversation was closed with a note to the client
        self.client.post(reverse("inquiries:submit", args=[prop.slug]),
                         {"name": "Late", "phone": "0788000555", "message": "Can I see it?"})
        self.assertFalse(Inquiry.objects.filter(phone="0788000555").exists())
        inq.refresh_from_db()
        self.assertEqual(inq.status, "closed")
        self.assertTrue(inq.thread.filter(from_client=False, body__contains="has now been").exists())
        # available again: back on the website
        agent.post(reverse("dashboard:property_availability", args=[prop.pk, "available"]))
        self.assertIn(prop, list(self.client.get(reverse("properties:list")).context["page_obj"].paginator.object_list))

    def test_old_conversations_deleted_after_a_month(self):
        from django.core.management import call_command
        from inquiries.cleanup import purge_old_inquiries
        old = Inquiry.objects.create(property=self.prop, name="Old", phone="0788000666", message="Hi")
        InquiryMessage.objects.create(inquiry=old, body="Answer")
        recent = Inquiry.objects.create(property=self.prop, name="Recent", phone="0788000777", message="Hi")
        Inquiry.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=31))
        Inquiry.objects.filter(pk=recent.pk).update(updated_at=timezone.now() - timedelta(days=29))
        self.assertGreaterEqual(purge_old_inquiries(), 1)
        self.assertFalse(Inquiry.objects.filter(pk=old.pk).exists())
        self.assertFalse(InquiryMessage.objects.filter(inquiry_id=old.pk).exists())
        self.assertTrue(Inquiry.objects.filter(pk=recent.pk).exists())
        call_command("purge_old_inquiries", stdout=StringIO())  # the manual command also works
        # and it runs by itself on normal traffic
        Inquiry.objects.filter(pk=recent.pk).update(updated_at=timezone.now() - timedelta(days=40))
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
            r = c.post(url, {"username": login, "password": "Agent2!Pass99"})
            self.assertEqual(r.status_code, 302, login)
            self.assertEqual(int(c.session["_auth_user_id"]), self.agent2.pk)
        self.assertEqual(self.client_class().post(url, {"username": self.agent2.email, "password": "nope"}).status_code, 200)

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
        # forgot password: a reset link goes to the account's email; choosing a password there clears "initial"
        self.client.logout()
        mail.outbox.clear()
        self.client.post(reverse("accounts:password_reset"), {"email": "ERIC.N@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r"https?://\S+/reset/\S+/", mail.outbox[0].body).group(0)
        path = link.split("testserver", 1)[1]
        r = self.client.get(path, follow=True)
        self.client.post(r.redirect_chain[-1][0] if r.redirect_chain else path,
                         {"new_password1": "Chosen!Pass77", "new_password2": "Chosen!Pass77"})
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

    def test_conversations_page_opens_only_with_the_code(self):
        page = reverse("inquiries:conversations")
        home = self.client.get(reverse("pages:home"))  # the button is there for every visitor
        self.assertContains(home, "messages-float")
        self.assertContains(self.client.get(page), "Continue a conversation")
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"name": "Visitor", "phone": "0788000111", "message": "Is the garden fenced?"})
        inq = Inquiry.objects.get(phone="0788000111")
        InquiryMessage.objects.create(inquiry=inq, body="Yes, fully fenced.")
        # the sender can carry on right away; the badge shows the new reply
        self.assertContains(self.client.get(reverse("pages:home")), 'class="messages-badge">1<')
        r = self.client.get(page)
        self.assertContains(r, "Is the garden fenced?")
        self.assertContains(r, "Yes, fully fenced.")
        self.assertNotContains(self.client.get(reverse("pages:home")), "messages-badge")  # now read
        # "Lock now": the conversation is hidden again and replying is refused until the code is given
        self.client.post(reverse("inquiries:lock", args=[inq.pk]))
        r = self.client.get(page + f"?c={inq.pk}")
        self.assertContains(r, "This conversation is locked")
        self.assertNotContains(r, "Yes, fully fenced.")
        self.client.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "sneaky"})
        self.assertFalse(InquiryMessage.objects.filter(body="sneaky").exists())
        # a wrong code is refused; the right one (any case/spacing) opens it
        self.client.post(reverse("inquiries:open"), {"inquiry": inq.pk, "code": "AAAAAA"})
        self.assertNotContains(self.client.get(page + f"?c={inq.pk}"), "Yes, fully fenced.")
        self.client.post(reverse("inquiries:open"), {"inquiry": inq.pk, "code": f" {inq.meeting_code.lower()} "})
        self.assertContains(self.client.get(page + f"?c={inq.pk}"), "Yes, fully fenced.")
        # a different device: nothing until the code is pasted, then the conversation and replying work
        phone = self.client_class()
        self.assertNotContains(phone.get(page), "Is the garden fenced?")
        phone.post(reverse("inquiries:open"), {"code": inq.meeting_code})
        self.assertContains(phone.get(page), "Yes, fully fenced.")
        phone.post(reverse("inquiries:reply", args=[inq.pk]), {"body": "Great, thanks!"})
        self.assertTrue(InquiryMessage.objects.filter(inquiry=inq, body="Great, thanks!", from_client=True).exists())

    def test_meeting_code_guessing_is_limited(self):
        Inquiry.objects.create(property=self.prop, name="X", phone="0788000333", message="Hi")
        target = Inquiry.objects.get(phone="0788000333")
        guesser = self.client_class()
        for _ in range(12):
            guesser.post(reverse("inquiries:open"), {"code": "ZZZZZZ"})
        guesser.post(reverse("inquiries:open"), {"code": target.meeting_code})  # even the right code: blocked now
        self.assertNotContains(guesser.get(reverse("inquiries:conversations")), "Hi</div>")

    def test_unlock_expires(self):
        from inquiries import views as iv
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"name": "Visitor", "phone": "0788000111", "message": "Still there?"})
        inq = Inquiry.objects.get(phone="0788000111")
        session = self.client.session
        session[iv.UNLOCK_KEY] = {str(inq.pk): session[iv.UNLOCK_KEY][str(inq.pk)] - iv.UNLOCK_SECONDS - 1}
        session.save()
        self.assertContains(self.client.get(reverse("inquiries:conversations") + f"?c={inq.pk}"),
                            "This conversation is locked")

    def test_client_requests_only_visible_to_their_agent(self):
        """A client's inquiry / visit request goes to the property's agent only, never to other agents."""
        self.client.post(reverse("inquiries:submit", args=[self.prop.slug]),
                         {"name": "Private Client", "phone": "0788000111", "email": "pc@example.com",
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
    def test_hot_deals_detected_against_local_market(self):
        hot = Property.objects.get(title__startswith="Spacious family house in Kimironko")
        self.assertTrue(hot.is_hot_deal)
        self.assertGreaterEqual(hot.deal_percent, 10)
        self.assertGreater(hot.market_price, hot.price)
        # normally priced neighbours are not deals
        self.assertFalse(Property.objects.get(title="3-bedroom house in Kimironko").is_hot_deal)
        # Huye studio is cheap, but there are no similar listings in Huye -> no verdict
        huye = Property.objects.get(title__startswith="Studio apartment near University")
        self.assertIsNone(huye.deal_percent)
        self.assertFalse(huye.is_hot_deal)

    def test_price_change_updates_deal_automatically(self):
        p = Property.objects.get(title="3-bedroom house in Kimironko")
        with self.captureOnCommitCallbacks(execute=True):
            p.price = 60_000_000  # ~30% below the area's price per m²
            p.save()
        p.refresh_from_db()
        self.assertTrue(p.is_hot_deal)
        with self.captureOnCommitCallbacks(execute=True):
            p.availability = Property.Availability.SOLD
            p.save()
        p.refresh_from_db()
        self.assertFalse(p.is_hot_deal)  # sold properties aren't promoted

    def test_recommended_order_and_pages(self):
        r = self.client.get(reverse("properties:list"))
        props = list(r.context["page_obj"])
        hot_flags = [p.is_hot_deal for p in props]
        self.assertTrue(hot_flags[0])
        self.assertEqual(hot_flags, sorted(hot_flags, reverse=True))  # all hot deals come first
        self.assertContains(r, "below market")
        # visitor's own sort still wins
        r = self.client.get(reverse("properties:list"), {"sort": "-price"})
        prices = [p.price for p in r.context["page_obj"]]
        self.assertEqual(prices, sorted(prices, reverse=True))
        # hot deals page
        r = self.client.get(reverse("properties:deals"))
        self.assertTrue(all(p.is_hot_deal for p in r.context["page_obj"]))
        self.assertEqual(r.context["page_obj"].paginator.count, Property.objects.filter(is_hot_deal=True).count())
        # home page sections
        r = self.client.get(reverse("pages:home"))
        self.assertTrue(r.context["hot_deals"])
        self.assertTrue(r.context["trending"])
        self.assertContains(r, "Hot deals")
        self.assertContains(r, "Trending now")
        self.assertContains(r, "Just listed")
        hot = Property.objects.filter(is_hot_deal=True).first()
        self.assertContains(self.client.get(hot.get_absolute_url()), "below market")

    def test_featured_boosts_trending(self):
        from properties.ranking import refresh_scores
        p = Property.objects.get(title="450 m² plot in Gatenga")
        before = p.trend_score
        Property.objects.filter(pk=p.pk).update(is_featured=True)
        refresh_scores()
        p.refresh_from_db()
        self.assertAlmostEqual(p.trend_score, before * 2, places=3)
        self.assertTrue(p.is_new)

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
        for name in ("purpose", "title", "price", "description", "sector", "owner_name",
                     "owner_phone", "bedrooms", "bathrooms", "new_images"):
            self.assertIn(name, errors, name)
        self.assertContains(r, "is-invalid")
        # removed fields are no longer on the form
        for gone in ("category", "village", "year_built", "video_url", "availability", "district", "latitude", "longitude"):
            self.assertNotIn(gone, r.context["form"].fields)

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
        self.assertEqual(self.client.post(reverse("dashboard:property_delete", args=[self.prop.pk])).status_code, 403)

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
                         {"name": "Visitor", "email": "v@example.com", "phone": "0788000111", "message": "Hi"})
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
                         {"name": "<script>alert(1)</script>Eric​", "phone": "0788000111",
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
        self.assertIn("_auth_user_id", self.client.session)

    def test_public_forms_are_rate_limited(self):
        data = {"name": "Spam", "phone": "0788000111", "email": "s@example.com", "message": "buy"}
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
