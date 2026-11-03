"""LP-BE-201: account models, fresh migrations, seed (G§6.1)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Customer, CustomerTariff, LegalEntityProfile
from accounts.testing import make_customer


class TariffSeedTests(TestCase):
    def test_migration_seeds_exactly_one_default_tariff_with_its_markup(self):
        default = CustomerTariff.objects.get(is_default=True)
        self.assertEqual((default.name, default.payment_terms, default.markup_percent),
                         ("Standard", "full", Decimal("20.00")))

    def test_saving_another_default_unsets_the_first(self):
        partner = CustomerTariff.objects.create(name="Partner", is_default=True, payment_terms="split_70_30")
        self.assertEqual(list(CustomerTariff.objects.filter(is_default=True)), [partner])

    def test_the_only_default_cannot_be_unset(self):
        default = CustomerTariff.objects.get(is_default=True)
        default.is_default = False
        with self.assertRaises(ValidationError) as ctx:
            default.full_clean()
        self.assertIn("is_default", ctx.exception.message_dict)


class CustomerModelTests(TestCase):
    def test_user_customer_round_trip_and_cascade(self):
        customer = make_customer()
        user = customer.user
        self.assertEqual(user.customer, customer)
        self.assertEqual(customer.user.customer.pk, customer.pk)
        user.delete()
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())

    def test_get_tariff_falls_back_to_the_default(self):
        customer = make_customer()
        self.assertEqual(customer.get_tariff(), CustomerTariff.objects.get(is_default=True))
        partner = CustomerTariff.objects.create(name="Partner")
        customer.tariff = partner
        self.assertEqual(customer.get_tariff(), partner)

    def test_defaults(self):
        customer = make_customer()
        self.assertEqual(customer.preferred_language, "ka")
        self.assertFalse(customer.is_fully_verified)
        self.assertIsNone(customer.anonymized_at)

    def test_phone_and_personal_id_are_unique_but_may_be_null(self):
        make_customer(personal_id="01234567890")
        make_customer(email="a@example.com", phone=None)
        make_customer(email="b@example.com", phone=None)  # several anonymized customers
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_customer(email="c@example.com", phone="599000000", personal_id="01234567890")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_customer(email="d@example.com", phone="555123456")

    def test_customer_has_at_most_one_company_and_company_codes_are_unique(self):
        customer = make_customer(account_type=Customer.TYPE_LEGAL)
        company = LegalEntityProfile.objects.create(
            customer=customer, company_official_name="EZShop LLC", company_identification_code="405834094",
        )
        self.assertEqual(customer.company, company)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegalEntityProfile.objects.create(
                customer=customer, company_official_name="Second", company_identification_code="405834095",
            )
        other = make_customer(email="o@example.com", phone="599111222", account_type=Customer.TYPE_LEGAL)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LegalEntityProfile.objects.create(
                customer=other, company_official_name="Copy", company_identification_code="405834094",
            )


class TariffAdminTests(TestCase):
    def test_default_tariff_cannot_be_deleted_in_admin(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model

        admin_user = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        request = RequestFactory().get("/")
        request.user = admin_user
        model_admin = site._registry[CustomerTariff]
        default = CustomerTariff.objects.get(is_default=True)
        self.assertFalse(model_admin.has_delete_permission(request, default))
        self.assertTrue(model_admin.has_delete_permission(request, CustomerTariff.objects.create(name="Other")))
        self.assertNotIn("delete_selected", model_admin.get_actions(request))
