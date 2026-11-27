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
    """First message from the "Agent replies" chat box: a name, a phone OR email (optional) and the question."""
    contact = forms.CharField(required=False, max_length=120)

    class Meta:
        model = Inquiry
        fields = ["name", "message"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Please enter your name.")
        return name

    def clean_contact(self):
        value = self.cleaned_data.get("contact", "").strip()
        if not value:
            return ""
        if "@" in value:
            forms.EmailField().clean(value)  # "Enter a valid email address."
            return value
        if not whatsapp_number(value):
            raise forms.ValidationError("Enter a valid phone number (e.g. 0788 123 456) or an email address.")
        return value

    def save(self, commit=True):
        inquiry = super().save(commit=False)
        contact = self.cleaned_data.get("contact", "")
        if "@" in contact:
            inquiry.email = contact
        else:
            inquiry.phone = contact
        if commit:
            inquiry.save()
        return inquiry


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
