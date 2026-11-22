import time

from django.conf import settings
from django.contrib import messages
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.security import hit_rate_limit
from notifications.services import email_support, notify, notify_admins
from properties.models import Property

from .forms import ClientReplyForm, InquiryForm
from .models import Inquiry, InquiryMessage


PER_HOUR = 10  # per connection; stops bots filling the database
SESSION_KEY = "my_inquiries"  # conversations this browser knows about (shown as tabs, locked until the code is given)
SESSION_MAX = 20
NEW_CODE_KEY = "new_meeting_code"
UNLOCK_KEY = "unlocked_inquiries"  # {inquiry pk: time of last activity}
SEEN_KEY = "seen_replies"          # {inquiry pk: last agent message id the client has read}
UNLOCK_SECONDS = 2 * 60 * 60       # an opened conversation locks again after 2 hours without activity
CODE_TRIES_PER_HOUR = 10           # stops anyone guessing codes


def my_inquiries(request):
    """Conversations this visitor has (tabs): sent from this browser, opened here with a code, or on
    their account. Knowing one is not enough to read it: it must be unlocked with its meeting code."""
    q = Q(pk__in=request.session.get(SESSION_KEY, []))
    if request.user.is_authenticated:
        q |= Q(client=request.user)
    return (Inquiry.objects.filter(q).select_related("property", "assigned_agent")
            .prefetch_related("property__images", "thread__author").order_by("-updated_at"))


def _remember(request, pk):
    request.session[SESSION_KEY] = ([pk] + [i for i in request.session.get(SESSION_KEY, []) if i != pk])[:SESSION_MAX]


def unlocked_ids(request):
    """Conversations opened with their code on this browser and still active (expired ones are dropped)."""
    now = time.time()
    stamps = {k: t for k, t in request.session.get(UNLOCK_KEY, {}).items() if now - t < UNLOCK_SECONDS}
    if stamps != request.session.get(UNLOCK_KEY, {}):
        request.session[UNLOCK_KEY] = stamps
    return {int(k) for k in stamps}


def unlock(request, pk):
    stamps = dict(request.session.get(UNLOCK_KEY, {}))
    stamps[str(pk)] = time.time()
    request.session[UNLOCK_KEY] = stamps
    _remember(request, pk)


def normalize_code(raw):
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())[:12]


def unread_count(request):
    """Conversations with an agent message the client hasn't read yet (for the badge; reveals no content)."""
    seen = request.session.get(SEEN_KEY, {})
    latest = (InquiryMessage.objects.filter(inquiry__in=my_inquiries(request).values("pk"), from_client=False)
              .values("inquiry_id").annotate(last=Max("pk")))
    return sum(1 for row in latest if row["last"] > seen.get(str(row["inquiry_id"]), 0))


def conversations(request):
    """'My conversations': one tab per conversation. Each opens only with its meeting code."""
    tabs = list(my_inquiries(request))
    opened = unlocked_ids(request)
    seen = request.session.get(SEEN_KEY, {})
    for t in tabs:
        t.is_unlocked = t.pk in opened
        agent_ids = [m.pk for m in t.thread.all() if not m.from_client]
        t.has_unread = bool(agent_ids) and max(agent_ids) > seen.get(str(t.pk), 0)
    wanted = request.GET.get("c", "")
    active = next((t for t in tabs if str(t.pk) == wanted), None)
    if active is None and wanted != "open" and tabs:
        active = tabs[0]
    if active and active.is_unlocked:
        unlock(request, active.pk)  # activity keeps it open
        agent_ids = [m.pk for m in active.thread.all() if not m.from_client]
        if agent_ids:
            request.session[SEEN_KEY] = {**seen, str(active.pk): max(agent_ids)}
            active.has_unread = False
    return render(request, "inquiries/conversations.html", {
        "tabs": tabs, "active": active, "reply_form": ClientReplyForm(), "unlock_hours": UNLOCK_SECONDS // 3600,
        "retention_days": settings.INQUIRY_RETENTION_DAYS})


@require_POST
def open_with_code(request):
    """The client pastes their meeting code to open (or continue) a conversation, on any device."""
    target = request.POST.get("inquiry", "")
    back = reverse("inquiries:conversations") + (f"?c={target}" if target.isdigit() else "?c=open")
    if hit_rate_limit(request, "meeting_code", CODE_TRIES_PER_HOUR):
        messages.error(request, "Too many attempts. Please wait an hour and try again, or call the agent.")
        return redirect(back)
    code = normalize_code(request.POST.get("code"))
    qs = Inquiry.objects.filter(meeting_code=code) if len(code) == 6 else Inquiry.objects.none()
    if target.isdigit():
        qs = qs.filter(pk=int(target))  # on a tab, the code must belong to THAT conversation
    inquiry = qs.first()
    if not inquiry:
        messages.error(request, "That code doesn't match this conversation. Check it and try again.")
        return redirect(back)
    unlock(request, inquiry.pk)
    messages.success(request, "Conversation opened.")
    return redirect(reverse("inquiries:conversations") + f"?c={inquiry.pk}")


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
               ("Client", inquiry.name), ("Phone", inquiry.phone), ("Email", inquiry.email),
               ("Meeting code", inquiry.meeting_code)]
    intro = (f"{inquiry.name} is asking about one of your properties." if first
             else f"{inquiry.name} replied in your conversation about this property.")
    body = ("Answer from the dashboard: your reply appears in the client's \"Agent replies\" on the website. "
            "You can also call or WhatsApp them on the number below.")
    note = "Before meeting the client, call them and ask for the meeting code shown above. Only the real client has it."
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
def lock_conversation(request, pk):
    """'Lock now' (e.g. on a shared phone): the code is needed again to open it."""
    stamps = dict(request.session.get(UNLOCK_KEY, {}))
    stamps.pop(str(pk), None)
    request.session[UNLOCK_KEY] = stamps
    messages.success(request, "Conversation locked. You'll need your meeting code to open it again.")
    return redirect(reverse("inquiries:conversations") + f"?c={pk}")


@require_POST
def client_reply(request, pk):
    """The client answers the agent on 'My conversations'. Only works while that conversation is
    unlocked with its meeting code on this browser, so nobody else can write into it."""
    back = reverse("inquiries:conversations") + f"?c={pk}#reply"
    if pk not in unlocked_ids(request):
        messages.error(request, "Please paste your meeting code to open this conversation first.")
        return redirect(back)
    inquiry = get_object_or_404(Inquiry.objects.select_related("property"), pk=pk)
    unlock(request, pk)
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
    prop = get_object_or_404(Property, slug=slug, approval_status=Property.Approval.APPROVED)
    if prop.is_taken:
        messages.info(request, f"This property has already been {prop.get_availability_display().lower()}. "
                               "Have a look at similar properties below.")
        return redirect(prop.get_absolute_url() + "#similar")
    if hit_rate_limit(request, "inquiry", PER_HOUR):
        messages.error(request, "You have sent too many messages in a short time. Please try again later "
                                "or contact us on WhatsApp.")
        return redirect(prop.get_absolute_url() + "#contact")
    form = InquiryForm(request.POST)
    if form.is_valid():
        inquiry = form.save(commit=False)
        inquiry.property = prop
        inquiry.assigned_agent = prop.agent
        inquiry.is_public = True  # shown as Q&A on the property page (first name only)
        if request.user.is_authenticated:
            inquiry.client = request.user
            inquiry.email = request.user.email or ""  # account holders still get replies by email
        inquiry.save()
        unlock(request, inquiry.pk)  # the sender can carry on right away; later they need the code
        msg = (f"{inquiry.name} ({inquiry.phone or inquiry.email}) asked about {prop.reference} – {prop.title} "
               f"({prop.location_display}): \"{inquiry.message[:120]}\"")
        agent = deliver_to_agent(request, inquiry, "New property inquiry", msg, client_text=inquiry.message)
        who = (agent.get_full_name() or str(agent)) if agent else "our team"
        messages.success(request, f"Your message has been sent to {who}. Their reply will appear in "
                                  "\"Agent replies\" (bottom right), where you can keep chatting.")
        # Shown ONCE in a pop-up (like an OTP) on the next page, then never again on the client's side
        request.session[NEW_CODE_KEY] = {"code": inquiry.meeting_code, "agent": who}
    else:
        messages.error(request, "Please correct the errors in the inquiry form.")
    return redirect(prop.get_absolute_url() + "#contact")
