"""Sign-in alerts: the administrator AND the account owner are emailed about every sign-in."""
import re
import time
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from notifications.models import Notification

from .models import AgentProfile, User

PW = "Str0ng!Pass2026"
ADMIN_INBOX = "boss@example.com"
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36"


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", LOGIN_ALERT_EMAIL=ADMIN_INBOX,
                   LOGIN_EMAIL_CODE=False,  # these tests are about the alerts; the code step is tested in core/tests.py
                   LOGIN_ALERTS_ENABLED=True, LOGIN_ALERTS_ASYNC=False)
class LoginAlertTests(TestCase):
    def setUp(self):
        cache.clear()
        self.agent = User.objects.create_user("agent1", "agent1@example.com", PW, role=User.Role.AGENT,
                                              first_name="Aline")
        AgentProfile.objects.filter(user=self.agent).update(application_status=AgentProfile.Application.APPROVED)
        mail.outbox = []

    def login(self, username, password):
        return self.client.post(reverse("accounts:login"), {"username": username, "password": password},
                                HTTP_USER_AGENT=UA)

    def recipients(self):
        return sorted(addr for m in mail.outbox for addr in m.to)

    def test_successful_login_emails_admin_and_owner(self):
        self.assertEqual(self.login("agent1", PW).status_code, 302)
        self.assertEqual(self.recipients(), sorted([ADMIN_INBOX, "agent1@example.com"]))
        owner_mail = next(m for m in mail.outbox if m.to == ["agent1@example.com"])
        self.assertIn("New sign-in", owner_mail.subject)
        self.assertIn("Chrome on Android", owner_mail.body)
        self.assertNotIn(PW, owner_mail.body)
        self.assertTrue(Notification.objects.filter(user=self.agent, title__icontains="sign-in").exists())

    def test_login_with_email_address_also_alerts(self):
        self.login("AGENT1@example.com", PW)
        self.assertEqual(self.recipients(), sorted([ADMIN_INBOX, "agent1@example.com"]))

    def test_wrong_password_emails_admin_and_owner(self):
        self.assertEqual(self.login("agent1", "wrong-password").status_code, 200)
        self.assertEqual(self.recipients(), sorted([ADMIN_INBOX, "agent1@example.com"]))
        for m in mail.outbox:
            self.assertIn("Wrong password", m.body)
            self.assertNotIn("wrong-password", m.body)  # the typed password is never sent

    def test_unknown_email_security_alert_only_goes_to_admin(self):
        self.login("stranger@elsewhere.com", "whatever")
        alerts = [m for m in mail.outbox if "sign-in" in m.subject.lower()]
        self.assertEqual([m.to for m in alerts], [[ADMIN_INBOX]])  # no security alert to a typed address
        self.assertIn("No account", alerts[0].body)

    def test_unknown_email_gets_sign_up_link_that_works_on_another_device(self):
        self.login("phone.user@example.com", "whatever")
        invite = next(m for m in mail.outbox if m.to == ["phone.user@example.com"])
        self.assertIn("Finish creating your account", invite.subject)
        link = re.search(r"(/accounts/agent/apply/\?invite=\S+)", invite.body).group(1)
        phone = self.client_class()  # a different device: no session from the login attempt
        self.assertContains(phone.get(link), 'value="phone.user@example.com"')

    def test_sign_up_link_sent_once_per_day(self):
        self.login("once@example.com", "a")
        self.login("once@example.com", "b")
        self.assertEqual(sum(m.to == ["once@example.com"] for m in mail.outbox), 1)

    def test_tampered_or_expired_link_is_ignored(self):
        from . import views
        bad = views.invite_link("victim@example.com")[:-3] + "xyz"
        self.assertNotContains(self.client.get(bad), "victim@example.com")
        with mock.patch("django.core.signing.time.time", return_value=time.time() - 8 * 24 * 3600):
            old = views.invite_link("late@example.com")
        page = self.client.get(old, follow=True)
        self.assertContains(page, "expired")
        self.assertNotContains(page, 'value="late@example.com"')

    @override_settings(GOOGLE_LOGIN_ENABLED=True)
    def test_sign_up_page_offers_google(self):
        self.assertContains(self.client.get(reverse("accounts:agent_register")), "Sign up with Google")

    def test_rejected_agent_application_alerts_both_with_reason(self):
        self.agent.is_active = False
        self.agent.save()
        AgentProfile.objects.filter(user=self.agent).update(application_status=AgentProfile.Application.REJECTED)
        self.login("agent1", PW)
        self.assertEqual(self.recipients(), sorted([ADMIN_INBOX, "agent1@example.com"]))
        self.assertIn("rejected", mail.outbox[0].body)

    def test_admin_logging_into_own_account_gets_one_email(self):
        User.objects.create_user("boss", ADMIN_INBOX, PW, role=User.Role.ADMIN)
        self.login("boss", PW)
        self.assertEqual(self.recipients(), [ADMIN_INBOX])

    def test_failure_emails_are_capped(self):
        for _ in range(12):  # includes attempts after the 15-minute lock kicks in
            self.login("agent1", "wrong")
        # 5 refusals + 1 "blocked" notice, each to admin and owner; the other 6 attempts send nothing
        self.assertEqual(len(mail.outbox), 12)
        self.assertEqual(sum("blocked" in m.body for m in mail.outbox), 2)

    def test_unknown_email_goes_to_sign_up_with_email_filled_in(self):
        r = self.login("New.Agent@Example.com", "anything")
        self.assertRedirects(r, reverse("accounts:agent_register"), fetch_redirect_response=False)
        page = self.client.get(reverse("accounts:agent_register"))
        self.assertContains(page, 'value="new.agent@example.com"')
        self.assertContains(page, "doesn&#x27;t have an account yet")
        self.assertNotIn("anything", page.content.decode())  # the typed password is never carried over
        self.assertFalse(User.objects.filter(email__iexact="new.agent@example.com").exists())  # nothing created yet

    def test_sign_up_after_redirect_creates_pending_account(self):
        self.login("fresh@example.com", "anything")
        self.client.post(reverse("accounts:agent_register"), {
            "first_name": "Fresh", "last_name": "Agent", "phone": "0788000111", "email": "fresh@example.com",
            "username": "fresh", "password1": PW, "password2": PW, "years_experience": 2, "agree": "on"})
        user = User.objects.get(email="fresh@example.com")
        self.assertFalse(user.is_active)  # still needs the administrator's approval
        self.assertEqual(user.agent_profile.application_status, AgentProfile.Application.PENDING)
        self.assertNotIn("signup_prefill", self.client.session)

    def test_wrong_password_on_existing_email_does_not_go_to_sign_up(self):
        r = self.login("agent1@example.com", "wrong")
        self.assertEqual(r.status_code, 200)  # stays on the login page with the normal error
        self.assertContains(r, "Please enter a correct username or email and password")

    def test_unknown_plain_username_stays_on_login(self):
        self.assertEqual(self.login("nobody-here", "x").status_code, 200)

    @override_settings(LOGIN_ALERTS_ENABLED=False)
    def test_can_be_switched_off(self):
        self.login("agent1", PW)
        self.assertEqual(mail.outbox, [])
