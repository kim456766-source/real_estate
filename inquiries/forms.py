from django import forms

from accounts.forms import BootstrapFormMixin

from .device import whatsapp_number
from .models import ContactMessage, Inquiry


class InquiryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Inquiry
        fields = ["name", "phone", "message"]
        labels = {"phone": "Your WhatsApp number"}
        widgets = {"message": forms.Textarea(attrs={"rows": 4}),
                   "phone": forms.TextInput(attrs={"placeholder": "07XX XXX XXX", "inputmode": "tel",
                                                   "autocomplete": "tel"})}

    on_whatsapp = forms.BooleanField(
        required=True, label="This number is registered on WhatsApp",
        error_messages={"required": "Please confirm that your number is registered on WhatsApp: "
                                    "the agent will answer you there."})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True  # the agent chats with the client on WhatsApp on this number
        self.fields["phone"].help_text = "After you press Send, WhatsApp opens with your message and this listing's link."

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        if not whatsapp_number(phone):
            raise forms.ValidationError("Enter a valid phone number, e.g. 0788 123 456 or +250 788 123 456.")
        return phone


class ChatStartForm(forms.ModelForm):
    """First message from the "Agent replies" chat box: the client's name (so the agent knows who is
    writing) and the question. No phone or email is asked: the conversation stays on the client's device."""

    class Meta:
        model = Inquiry
        fields = ["name", "message"]
        error_messages = {"name": {"required": "Please write your name, so the agent knows who is asking."}}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if len(name) < 2:
            raise forms.ValidationError("Please write your name, so the agent knows who is asking.")
        return name[:120]


class InquiryResponseForm(BootstrapFormMixin, forms.ModelForm):
    reply = forms.CharField(required=False, max_length=2000, label="Reply to the client",
                            widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write your message…"}))

    class Meta:
        model = Inquiry
        fields = ["reply", "status", "assigned_agent"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user and not user.is_admin_role:
            self.fields.pop("assigned_agent")


class ClientReplyForm(forms.Form):
    body = forms.CharField(max_length=2000, strip=True,
                           widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Write a reply…"}))


class ContactForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ["name", "email", "subject", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 5})}
