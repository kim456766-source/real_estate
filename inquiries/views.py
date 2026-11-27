from django.conf import settings
from django.contrib import messages
from django.db.models import Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import SiteSetting
from core.security import client_ip, hit_rate_limit
from notifications.services import email_support, notify, notify_admins
from properties.models import Property

from .device import current_key, describe, ensure_key, listing_text, remember_device, whatsapp_link
from .forms import ChatStartForm, ClientReplyForm, InquiryForm
from .models import Inquiry, InquiryMessage


PER_HOUR = 10  # per connection; stops bots filling the database
SEEN_KEY = "seen_replies"  # {inquiry pk: last agent message id the client has read}


def my_inquiries(request):
    """This client's conversations: the ones sent from THIS device (see inquiries/device.py), plus the
    ones on their account if they are signed in. Nobody else can see them; no code is needed."""
    key = current_key(request)
    q = Q(device_key=key) if key else Q(pk__in=[])
    if request.user.is_authenticated:
        q |= Q(client=request.user)
    return (Inquiry.objects.filter(q).select_related("property", "assigned_agent")
            .prefetch_related("property__images", "thread__author").order_by("-updated_at"))


def unread_count(request):
    """Conversations with an agent message the client hasn't read yet (for the badge; reveals no content)."""
    if not current_key(request) and not request.user.is_authenticated:
        return 0
    seen = request.session.get(SEEN_KEY, {})
    latest = (InquiryMessage.objects.filter(inquiry__in=my_inquiries(request).values("pk"), from_client=False)
              .values("inquiry_id").annotate(last=Max("pk")))
    return sum(1 for row in latest if row["last"] > seen.get(str(row["inquiry_id"]), 0))


def is_staff_user(user):
    return user.is_authenticated and (user.is_admin_role or user.is_agent)


def can_answer(user, inquiry):
    """Admins answer every conversation; agents the ones for their listings."""
    if not is_staff_user(user):
        return False
    return user.is_admin_role or user.pk in (inquiry.assigned_agent_id, inquiry.property.agent_id)


STAFF_USE_DASHBOARD = ("You are signed in as an agent or admin, so you can't write as a client. Answer clients from "
                       "Dashboard > Inquiries (or sign out to ask as a client).")


def add_agent_reply(inquiry, user, body):
    """An agent / admin answers: saved as THEIR message (never as the client's)."""
    InquiryMessage.objects.create(inquiry=inquiry, from_client=False, author=user, body=body)
    inquiry.response = body
    inquiry.responded_at = inquiry.responded_at or timezone.now()
    inquiry.status = Inquiry.Status.RESPONDED
    inquiry.save(update_fields=["response", "responded_at", "status", "updated_at"])


def agent_whatsapp_url(request, prop, message="", name=""):
    """WhatsApp chat with the property's agent (or our customer service), pre-filled with the
    client's message and the listing's link."""
    agent = prop.contact_person
    phone = agent.phone if agent and agent.is_active and agent.phone else SiteSetting.load().whatsapp
    url = request.build_absolute_uri(prop.get_absolute_url())
    return whatsapp_link(phone, listing_text(prop, url, message, name))


def conversations(request):
    """'My conversations' (Agent replies): one tab per conversation sent from this device."""
    tabs = list(my_inquiries(request))
    seen = request.session.get(SEEN_KEY, {})
    for t in tabs:
        agent_ids = [m.pk for m in t.thread.all() if not m.from_client]
        t.has_unread = bool(agent_ids) and max(agent_ids) > seen.get(str(t.pk), 0)
    wanted = request.GET.get("c", "")
    active = next((t for t in tabs if str(t.pk) == wanted), tabs[0] if tabs else None)
    if active:
        agent_ids = [m.pk for m in active.thread.all() if not m.from_client]
        if agent_ids:
            request.session[SEEN_KEY] = {**seen, str(active.pk): max(agent_ids)}
            active.has_unread = False
        active.whatsapp_url = agent_whatsapp_url(request, active.property)
    return render(request, "inquiries/conversations.html", {
        "tabs": tabs, "active": active, "reply_form": ClientReplyForm(),
        "as_agent": bool(active) and can_answer(request.user, active),
        "retention_days": settings.INQUIRY_RETENTION_DAYS})


def responsible_agent(inquiry):
    """The agent who must receive this conversation: the one the inquiry is assigned to, else the
    property's agent. Deactivated agents are skipped (admins get it instead)."""
    for agent in (inquiry.assigned_agent, inquiry.property.agent):
        if agent and agent.is_active:
            return agent
    return None


def deliver_to_agent(request, inquiry, title, msg, client_text="", first=True):
    """Send the client's message to the corresponding agent; if no agent can take it, to the admins.
    The email shows the property, who wrote, their phone, their exact words and a button to answer."""
    link = f"/dashboard/inquiries/{inquiry.pk}/#reply"
    prop = inquiry.property
    details = [("Property", f"{prop.title} ({prop.reference})"), ("Location", prop.location_display),
               ("Price", f"{prop.price:,.0f} {prop.currency}" + (f" / {prop.rent_period}" if prop.rent_period else "")),
               ("Client", inquiry.name), ("WhatsApp / phone", inquiry.phone), ("Email", inquiry.email),
               ("Sent from", inquiry.device_info)]
    intro = (f"{inquiry.name} is asking about one of your properties." if first
             else f"{inquiry.name} replied in your conversation about this property.")
    note = ("The client was also taken to WhatsApp with this message and the listing's link, so check your WhatsApp. "
            "You can answer there, or from the dashboard: your reply appears in the client's \"Agent replies\" "
            "on the phone or computer they used." if first else
            "Your reply from the dashboard appears in the client's \"Agent replies\" on the phone or computer they used.")
    agent = responsible_agent(inquiry)
    if agent:
        if inquiry.assigned_agent_id != agent.pk:
            Inquiry.objects.filter(pk=inquiry.pk).update(assigned_agent=agent)
            inquiry.assigned_agent = agent
        notify(agent, title, msg, link, email=True, category="New inquiry" if first else "Client reply",
               intro=intro, details=details, quote=client_text, quote_label=f"{inquiry.name} wrote",
               button=("Open the conversation", link), note=note,
               reason="You received this because you are the agent for this property.")
    else:
        notify_admins(title, msg, link)
        email_support(title, f"{intro} No active agent is assigned to this property, so please assign one or answer.",
                      reply_to=inquiry.email or None, link=request.build_absolute_uri(link), details=details,
                      quote=client_text, quote_label=f"{inquiry.name} wrote", category="New inquiry")
    return agent


@require_POST
def client_reply(request, pk):
    """The client answers the agent on 'My conversations'. Only from the device (or account) that sent
    the conversation, so nobody else can write into it."""
    back = reverse("inquiries:conversations") + f"?c={pk}#reply"
    inquiry = get_object_or_404(my_inquiries(request).select_related("property"), pk=pk)
    if is_staff_user(request.user):
        if not can_answer(request.user, inquiry):
            messages.error(request, STAFF_USE_DASHBOARD)
            return redirect(back)
        form = ClientReplyForm(request.POST)
        if form.is_valid():
            add_agent_reply(inquiry, request.user, form.cleaned_data["body"])
            messages.success(request, "Your reply was sent to the client (as the agent).")
        return redirect(back)
    if hit_rate_limit(request, "inquiry_reply", 30):
        messages.error(request, "You have sent too many messages in a short time. Please try again later.")
        return redirect(back)
    form = ClientReplyForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please write a message before sending.")
        return redirect(back)
    body = form.cleaned_data["body"]
    InquiryMessage.objects.create(inquiry=inquiry, from_client=True,
                                  author=request.user if request.user.is_authenticated else None, body=body)
    inquiry.status = Inquiry.Status.IN_PROGRESS  # back on the agent's to-do list
    inquiry.save(update_fields=["status", "updated_at"])
    prop = inquiry.property
    msg = f"{inquiry.name} replied about {prop.reference} – {prop.title}: \"{body[:300]}\""
    agent = deliver_to_agent(request, inquiry, "New message from a client", msg, client_text=body, first=False)
    messages.success(request, f"Your reply has been sent to {agent.get_full_name() or agent}." if agent
                     else "Your reply has been sent to our team.")
    return redirect(back)


@require_POST
def submit_inquiry(request, slug):
    """The client's message is saved (private: only this device and the agent see it), the agent is
    notified, and the client is taken straight to WhatsApp with their message and the listing's link."""
    prop = get_object_or_404(Property, slug=slug, approval_status=Property.Approval.APPROVED)
    ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if prop.is_taken:
        text = (f"This property has already been {prop.get_availability_display().lower()}. "
                "Have a look at similar properties below.")
        if ajax:
            return JsonResponse({"ok": False, "error": text}, status=400)
        messages.info(request, text)
        return redirect(prop.get_absolute_url() + "#similar")
    if hit_rate_limit(request, "inquiry", PER_HOUR):
        text = "You have sent too many messages in a short time. Please try again later or contact us on WhatsApp."
        if ajax:
            return JsonResponse({"ok": False, "error": text}, status=429)
        messages.error(request, text)
        return redirect(prop.get_absolute_url() + "#contact")
    form = InquiryForm(request.POST)
    if not form.is_valid():
        errors = " ".join(e for errs in form.errors.values() for e in errs)
        if ajax:
            return JsonResponse({"ok": False, "error": f"Your message was not sent. {errors}"}, status=400)
        messages.error(request, f"Your message was not sent. {errors}")
        return redirect(prop.get_absolute_url() + "#contact")
    inquiry = form.save(commit=False)
    inquiry.property = prop
    inquiry.assigned_agent = prop.agent
    inquiry.device_key = ensure_key(request)
    inquiry.client_ip = client_ip(request)
    inquiry.device_info = describe(request)
    if request.user.is_authenticated:
        inquiry.client = request.user
        inquiry.email = request.user.email or ""  # account holders still get replies by email
    inquiry.save()
    msg = (f"{inquiry.name} ({inquiry.phone or inquiry.email}) asked about {prop.reference} - {prop.title} "
           f"({prop.location_display}): \"{inquiry.message[:120]}\"")
    agent = deliver_to_agent(request, inquiry, "New property inquiry", msg, client_text=inquiry.message)
    who = (agent.get_full_name() or str(agent)) if agent else "our team"
    notice = (f"Your message was sent to {who}. It is saved under \"Agent replies\" on this phone or computer, "
              "where only you and the agent can see it.")
    wa = agent_whatsapp_url(request, prop, inquiry.message, inquiry.name)
    if ajax:  # the page opens WhatsApp itself (app on phones, WhatsApp Web on computers)
        return remember_device(request, JsonResponse({"ok": True, "whatsapp": wa, "notice": notice}))
    messages.success(request, notice)
    return remember_device(request, redirect(wa or prop.get_absolute_url() + "#contact"))


# ---------- "Agent replies" chat box (floating panel) ----------
CHAT_PER_HOUR = 30


def device_conversation(request, prop):
    """This device's (or account's) latest open conversation about `prop`, if any."""
    return my_inquiries(request).filter(property=prop).exclude(status=Inquiry.Status.CLOSED).first()


def _chat_json(inquiry, as_agent=False):
    """The conversation for the chat box. `as_agent`: seen by the agent (their messages on the right)."""
    agent = inquiry.assigned_agent
    client_label = inquiry.name if as_agent else "You"
    msgs = [{"id": 0, "mine": not as_agent, "who": client_label, "text": inquiry.message,
             "time": timezone.localtime(inquiry.created_at).strftime("%d %b %H:%M")}]
    for m in inquiry.thread.select_related("author").order_by("created_at", "pk"):
        staff_name = (m.author.get_full_name() or m.author.username) if m.author else "Agent"
        msgs.append({"id": m.pk, "mine": m.from_client != as_agent,
                     "who": client_label if m.from_client else staff_name,
                     "text": m.body, "time": timezone.localtime(m.created_at).strftime("%d %b %H:%M")})
    return {"ok": True, "conversation": inquiry.pk, "closed": inquiry.status == Inquiry.Status.CLOSED,
            "agent": (agent.get_full_name() or agent.username) if agent else "", "messages": msgs}


@require_POST
def chat_send(request, slug):
    """Send from the chat box. The first message starts a conversation (just the message: no name, phone
    or email needed); later ones continue it. Answers with the whole conversation as JSON, so it shows straight away."""
    prop = get_object_or_404(Property, slug=slug, approval_status=Property.Approval.APPROVED)
    if prop.is_taken:
        return JsonResponse({"ok": False, "error": "This property is no longer available."}, status=400)
    if hit_rate_limit(request, "chat", CHAT_PER_HOUR):
        return JsonResponse({"ok": False, "error": "You have sent too many messages. Please try again later."},
                            status=429)
    inquiry = device_conversation(request, prop)
    if is_staff_user(request.user):
        if not inquiry or not can_answer(request.user, inquiry):
            return JsonResponse({"ok": False, "error": STAFF_USE_DASHBOARD}, status=403)
        form = ClientReplyForm({"body": request.POST.get("message", "")})
        if not form.is_valid():
            return JsonResponse({"ok": False, "error": "Please write a message."}, status=400)
        add_agent_reply(inquiry, request.user, form.cleaned_data["body"])
        return JsonResponse(_chat_json(inquiry, as_agent=True))
    if inquiry:
        form = ClientReplyForm({"body": request.POST.get("message", "")})
        if not form.is_valid():
            return JsonResponse({"ok": False, "error": "Please write a message."}, status=400)
        body = form.cleaned_data["body"]
        InquiryMessage.objects.create(inquiry=inquiry, from_client=True, body=body,
                                      author=request.user if request.user.is_authenticated else None)
        inquiry.status = Inquiry.Status.IN_PROGRESS
        inquiry.save(update_fields=["status", "updated_at"])
        deliver_to_agent(request, inquiry, "New message from a client",
                         f"{inquiry.name} replied about {prop.reference} - {prop.title}: \"{body[:300]}\"",
                         client_text=body, first=False)
        return JsonResponse(_chat_json(inquiry))
    form = ChatStartForm(request.POST)
    if not form.is_valid():
        errors = " ".join(e for errs in form.errors.values() for e in errs)
        return JsonResponse({"ok": False, "error": errors}, status=400)
    inquiry = form.save(commit=False)
    inquiry.property = prop
    inquiry.assigned_agent = prop.agent
    inquiry.device_key = ensure_key(request)
    inquiry.client_ip = client_ip(request)
    inquiry.device_info = describe(request)
    if request.user.is_authenticated:  # signed in: their name and email are known already
        inquiry.client = request.user
        inquiry.name = request.user.get_full_name() or request.user.username
        inquiry.email = request.user.email or ""
    inquiry.save()
    deliver_to_agent(request, inquiry, "New property inquiry",
                     f"{inquiry.name} ({inquiry.phone or inquiry.email or inquiry.device_info or 'website chat'}) asked about "
                     f"{prop.reference} - {prop.title}: \"{inquiry.message[:120]}\"", client_text=inquiry.message)
    return remember_device(request, JsonResponse(_chat_json(inquiry)))


def chat_poll(request, pk):
    """New replies for the open chat box (checked every few seconds while it is open)."""
    inquiry = get_object_or_404(my_inquiries(request).select_related("property"), pk=pk)
    data = _chat_json(inquiry, as_agent=can_answer(request.user, inquiry))
    # The client is looking at it: the agent's messages count as read
    agent_ids = [m["id"] for m in data["messages"] if not m["mine"]]
    if agent_ids:
        seen = request.session.get(SEEN_KEY, {})
        if seen.get(str(pk), 0) < max(agent_ids):
            request.session[SEEN_KEY] = {**seen, str(pk): max(agent_ids)}
    return JsonResponse(data)


def unread(request):
    """How many conversations on this device have a reply the client hasn't read (for the badge)."""
    return JsonResponse({"unread": unread_count(request)})
