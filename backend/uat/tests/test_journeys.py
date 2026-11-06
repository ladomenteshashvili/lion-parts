"""The UAT journeys on the Django test client: CSRF enforced, demo settings on, no network, in-memory email."""

import importlib
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import clear_url_caches
from django.utils import timezone

import config.urls
from parts.models import CarrierService
from pricing.models import ExchangeRate
from uat import journeys


def transport_for(client):
    def transport(method, path, body, headers):
        kwargs = {} if body is None else {"data": json.dumps(body), "content_type": "application/json"}
        with TestCase.captureOnCommitCallbacks(execute=True):  # notifications are sent after commit, as in production
            response = client.generic(method, path, headers=headers, **kwargs)
        kind = response.headers.get("Content-Type", "")
        data = response.json() if "json" in kind else response.content.decode()
        return response.status_code, data, response.headers
    return transport


class TestClientContext(journeys.Context):
    def client(self, language="en"):
        return journeys.Api(transport_for(Client(enforce_csrf_checks=True)), language, self.record)

    def staff(self):
        user = get_user_model().objects.filter(username="uat-staff").first() or \
            get_user_model().objects.create_superuser("uat-staff", "uat-staff@example.com", "Uat-Staff!pass1")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        return journeys.Api(transport_for(client), "en", self.record)

    def email_link(self, address, marker):
        for message in reversed(mail.outbox):
            if address in message.to and marker in message.body:
                return next(word for word in message.body.split() if marker in word)
        raise AssertionError(f"no email with {marker} for {address}")


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True, SECURE_SSL_REDIRECT=False, PARTS_PROVIDER="demo",
                   PAYMENT_PROVIDER="fake", SENDER_GE_ENABLED=False)
class JourneyTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        clear_url_caches()
        importlib.reload(config.urls)
        cls.addClassCleanup(lambda: (clear_url_caches(), importlib.reload(config.urls)))

    def setUp(self):
        ExchangeRate.objects.create(date=timezone.localdate(), rate=Decimal("2.6000"))
        CarrierService.objects.create(name="Air", min_eta_days=9, max_eta_days=15, is_default=True)


def journey_test(journey):
    def test(self):
        journey(TestClientContext())
    test.__doc__ = journey.__doc__
    return test


for _journey in journeys.ALL:
    setattr(JourneyTests, f"test_{_journey.__name__}", journey_test(_journey))
