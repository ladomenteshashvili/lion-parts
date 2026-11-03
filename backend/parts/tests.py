from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import CarrierService, PartQuoteRequest, PartSearchLog
from .admin import PartQuoteRequestAdmin
from accounts.models import Customer, CustomerTariff
from accounts.testing import make_customer

from api.legacy import legacy_endpoint_test

@override_settings(PARTS_PROVIDER="demo")
class PartsSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

@override_settings(PARTS_PROVIDER="amt")
class PartPriceCalculationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "weight-enabled-session"

        self.tariff = CustomerTariff.objects.create(
            name="Weight enabled",
            markup_percent=Decimal("20.00"),
            can_enter_weight=True,
        )

        self.customer = Customer.objects.create(
            session_id=self.session_id,
            name="Weight Customer",
            phone="+995555123456",
            tariff=self.tariff,
        )

        CarrierService.objects.create(
            name="USA Standard",
            usd_per_kg=Decimal("8.00"),
            min_eta_days=10,
            max_eta_days=14,
            is_active=True,
            is_default=True,
        )

class PartQuoteRequestApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.session_id = "guest-test-session"

        self.customer = make_customer(first_name="Test", last_name="Customer")

