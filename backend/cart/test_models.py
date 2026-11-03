"""LP-BE-501: cart models & cart resolution (G§6.4, G§7.7)."""

from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from rest_framework.test import APIClient

from accounts.testing import make_customer
from cart.models import Cart
from cart.services import get_cart


class CartResolutionTests(TestCase):
    def request(self, client):
        """A request carrying the client's session and user, as the views see it."""
        from django.contrib.auth.middleware import AuthenticationMiddleware
        from django.contrib.sessions.middleware import SessionMiddleware

        request = RequestFactory().get("/")
        if "sessionid" in client.cookies:
            request.COOKIES["sessionid"] = client.cookies["sessionid"].value
        SessionMiddleware(lambda r: None).process_request(request)
        AuthenticationMiddleware(lambda r: None).process_request(request)
        return request

    def test_guest_and_customer_get_distinct_carts(self):
        guest = APIClient()
        guest.get("/api/auth/csrf/")
        guest.session.save()
        guest_cart = get_cart(self.request(guest))
        self.assertEqual((guest_cart.customer, guest_cart.session_key is not None), (None, True))

        customer = make_customer()
        member = APIClient()
        member.force_login(customer.user)
        member_cart = get_cart(self.request(member))
        self.assertEqual(member_cart.customer, customer)
        self.assertNotEqual(guest_cart.pk, member_cart.pk)

    def test_same_session_resolves_the_same_guest_cart(self):
        guest = APIClient()
        first = get_cart(self.request(guest))
        guest.cookies["sessionid"] = first.session_key
        self.assertEqual(get_cart(self.request(guest)).pk, first.pk)
        self.assertEqual(Cart.objects.count(), 1)

    def test_no_create(self):
        self.assertIsNone(get_cart(self.request(APIClient()), create=False))

    def test_one_cart_per_customer_and_session_and_an_owner_is_required(self):
        customer = make_customer()
        Cart.objects.create(customer=customer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cart.objects.create(customer=customer)
        Cart.objects.create(session_key="s1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cart.objects.create(session_key="s1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cart.objects.create()
