from django import forms

from accounts.forms import BootstrapFormMixin

from .models import ContactMessage, Inquiry


class InquiryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Inquiry
        fields = ["name", "phone", "message"]
        widgets = {"message": forms.Textarea(attrs={"rows": 4}),
                   "phone": forms.TextInput(attrs={"placeholder": "07XX XXX XXX", "inputmode": "tel"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].required = True  # the agent calls / WhatsApps the client on this number


class InquiryResponseForm(BootstrapFormMixin, forms.ModelForm):
    reply = forms.CharField(required=False, max_length=2000, label="Reply to the client",
                            widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write your message…"}))

    class Meta:
        model = Inquiry
        fields = ["reply", "status", "assigned_agent", "is_public"]
        labels = {"is_public": "Show this conversation on the property page (client's first name only)"}

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
