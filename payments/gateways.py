"""
Payment provider layer.

Each gateway exposes the same two operations:
  request_payment(payment)  -> starts a collection request on the payer's phone
  check_status(payment)     -> asks the provider for the real transaction status

The platform NEVER marks a mobile-money payment successful on its own: only
`check_status` (or a verified provider callback) may set provider_confirmed=True.

To go live, obtain merchant credentials from MTN MoMo (momodeveloper.mtn.com) or
Airtel Money (developers.airtel.africa), put them in .env, `pip install requests`,
and complete the TODO sections below following the provider's documentation.
"""
from django.conf import settings
from django.utils import timezone


class GatewayNotConfigured(Exception):
    pass


class GatewayError(Exception):
    """The provider answered with an error or could not be reached (network down, timeout...)."""


# When the real API calls are added: always pass timeout=GATEWAY_TIMEOUT to requests, and turn
# requests.RequestException / bad JSON into GatewayError so a slow provider never hangs the site.
GATEWAY_TIMEOUT = 15  # seconds


class BaseGateway:
    name = "base"

    def is_configured(self):
        return False

    def request_payment(self, payment):
        raise GatewayNotConfigured(f"{self.name} is not configured yet.")

    def check_status(self, payment):
        raise GatewayNotConfigured(f"{self.name} is not configured yet.")

    @staticmethod
    def apply_provider_result(payment, provider_status, raw):
        """Map a verified provider status onto our payment record."""
        from .models import Payment

        payment.provider_response = raw
        if provider_status == "SUCCESSFUL":
            payment.provider_confirmed = True
            payment.status = Payment.Status.SUCCESSFUL
            payment.paid_at = timezone.now()
        elif provider_status in ("FAILED", "REJECTED", "TIMEOUT"):
            payment.status = Payment.Status.FAILED
        payment.save()
        return payment


class MTNMoMoGateway(BaseGateway):
    """MTN Mobile Money Collections API (requesttopay)."""

    name = "MTN MoMo"

    def is_configured(self):
        c = settings.MTN_MOMO
        return bool(c["SUBSCRIPTION_KEY"] and c["API_USER"] and c["API_KEY"])

    def request_payment(self, payment):
        if not self.is_configured():
            raise GatewayNotConfigured("Add MTN_MOMO_* credentials to .env first.")
        # TODO: 1) POST {BASE_URL}/collection/token/ with Basic auth (API_USER:API_KEY)
        #       2) POST {BASE_URL}/collection/v1_0/requesttopay with X-Reference-Id = uuid4,
        #          body {amount, currency, externalId: payment.reference, payer: {partyIdType: MSISDN, partyId}}
        #       3) store the X-Reference-Id in payment.provider_transaction_id
        raise NotImplementedError("MTN MoMo integration pending merchant approval.")

    def check_status(self, payment):
        if not self.is_configured():
            raise GatewayNotConfigured("Add MTN_MOMO_* credentials to .env first.")
        # TODO: GET {BASE_URL}/collection/v1_0/requesttopay/{provider_transaction_id}
        #       then: return self.apply_provider_result(payment, data["status"], data)
        raise NotImplementedError("MTN MoMo integration pending merchant approval.")


class AirtelMoneyGateway(BaseGateway):
    name = "Airtel Money"

    def is_configured(self):
        c = settings.AIRTEL_MONEY
        return bool(c["CLIENT_ID"] and c["CLIENT_SECRET"])

    def request_payment(self, payment):
        if not self.is_configured():
            raise GatewayNotConfigured("Add AIRTEL_* credentials to .env first.")
        # TODO: POST /auth/oauth2/token, then POST /merchant/v1/payments/
        raise NotImplementedError("Airtel Money integration pending merchant approval.")

    def check_status(self, payment):
        if not self.is_configured():
            raise GatewayNotConfigured("Add AIRTEL_* credentials to .env first.")
        # TODO: GET /standard/v1/payments/{id}, map TS -> SUCCESSFUL, TF -> FAILED
        raise NotImplementedError("Airtel Money integration pending merchant approval.")


def get_gateway(method):
    from .models import Payment

    return {Payment.Method.MTN_MOMO: MTNMoMoGateway(), Payment.Method.AIRTEL: AirtelMoneyGateway()}.get(method)
