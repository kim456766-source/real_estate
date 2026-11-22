from django import forms

from accounts.forms import BootstrapFormMixin

from .models import Payment


class PaymentForm(BootstrapFormMixin, forms.ModelForm):
    """Record a payment. Status is not editable here: it is set by verification only."""

    paid_at = forms.DateTimeField(required=False,
                                  widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))

    class Meta:
        model = Payment
        fields = ["payer_name", "payer_phone", "payer_email", "property", "amount", "currency", "method", "purpose",
                  "provider_transaction_id", "paid_at", "notes"]
        labels = {"payer_name": "Client name", "payer_phone": "Client phone", "payer_email": "Client email"}
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payer_name"].required = True
        self.fields["payer_phone"].help_text = "Mobile money number for MoMo / Airtel (2507XXXXXXXX)"
        self.fields["provider_transaction_id"].label = "Transaction / receipt number"
