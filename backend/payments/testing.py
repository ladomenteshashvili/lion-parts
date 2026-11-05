"""Test factories (not used at runtime): a simulated card provider. It is registered only for the duration of a test,
so no setting can select it outside the test suite."""

import hashlib
import hmac
import json
import threading
from unittest import mock

from django.test import override_settings
from rest_framework.test import APIClient

from .models import Payment
from .providers import PROVIDERS, PaymentInitError, PaymentProvider

SIGNING_KEY = b"simulated-provider-test-key"


class SimulatedSession:
    def __init__(self, payment):
        self.provider_order_id = payment.provider_order_id
        self.amount = f"{payment.amount:.2f}"
        self.currency = payment.currency
        self.status = "pending"
        self.chargeable = True
        self.charges = 0


class SimulatedGateway:
    """The provider's side of the world: its own sessions, which only change when the provider acts. Finality and
    retry behaviour are per-test choices, because a real provider's rules are not assumed here."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.start_error = False
        self.fetch_answers = {}
        self.fetch_calls = []

    def session(self, payment):
        return self.sessions[payment.provider_order_id]

    def pay(self, payment):
        """The customer completes the provider's page. -> True when the provider took the money."""
        with self.lock:
            session = self.session(payment)
            if not session.chargeable:
                return False
            session.charges += 1
            session.status, session.chargeable = "paid", False
            return True

    def decline(self, payment, chargeable_after=False):
        with self.lock:
            session = self.session(payment)
            session.status, session.chargeable = "failed", chargeable_after

    def expire(self, payment):
        with self.lock:
            session = self.session(payment)
            session.status, session.chargeable = "expired", False

    def answer_fetch_with(self, payment, *answers):
        """Scripted fetch_status answers, oldest first; an Exception is raised. Afterwards the session's status."""
        self.fetch_answers[payment.provider_order_id] = list(answers)

    def callback_body(self, payment, status, *, amount=None, currency=None, key=SIGNING_KEY):
        fields = {"order_id": payment.provider_order_id, "status": status,
                  "amount": amount if amount is not None else f"{payment.amount:.2f}",
                  "currency": currency if currency is not None else payment.currency}
        return {**fields, "signature": sign(fields, key)}

    def send_callback(self, payment, status, **kwargs):
        return APIClient().post("/api/payments/simulated/callback/", self.callback_body(payment, status, **kwargs),
                                format="json")


def sign(fields, key=SIGNING_KEY):
    return hmac.new(key, json.dumps(fields, sort_keys=True).encode(), hashlib.sha256).hexdigest()


class SimulatedProvider(PaymentProvider):
    name = "simulated"

    def __init__(self, gateway):
        self.gateway = gateway

    def start(self, payment):
        if self.gateway.start_error:
            raise PaymentInitError("simulated provider is down")
        with self.gateway.lock:
            self.gateway.sessions[payment.provider_order_id] = SimulatedSession(payment)
        return f"https://provider.invalid/pay/{payment.provider_order_id}"

    def handle_callback(self, request):
        body = dict(request.data)
        signature = body.pop("signature", "")
        if not hmac.compare_digest(str(signature), sign(body)):
            raise ValueError("bad signature")
        payment = Payment.objects.get(provider_order_id=body["order_id"])
        if body["amount"] != f"{payment.amount:.2f}" or body["currency"] != payment.currency:
            raise ValueError("amount or currency mismatch")
        return payment, body["status"]

    def fetch_status(self, payment):
        with self.gateway.lock:
            self.gateway.fetch_calls.append(payment.provider_order_id)
            answers = self.gateway.fetch_answers.get(payment.provider_order_id)
            answer = answers.pop(0) if answers else self.gateway.session(payment).status
        if isinstance(answer, Exception):
            raise answer
        return answer


def use_simulated_provider(test):
    """Selects the simulated provider until the test ends. -> its gateway."""
    gateway = SimulatedGateway()
    patches = (mock.patch.dict(PROVIDERS, {SimulatedProvider.name: lambda: SimulatedProvider(gateway)}),
               override_settings(PAYMENT_PROVIDER=SimulatedProvider.name))
    patches[0].start()
    test.addCleanup(patches[0].stop)
    patches[1].enable()
    test.addCleanup(patches[1].disable)
    return gateway
