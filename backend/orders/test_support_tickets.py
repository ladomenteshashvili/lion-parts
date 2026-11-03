"""LP-BE-1001: support tickets for guests and customers (G§7.16, G§8.9)."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.testing import PASSWORD, make_customer
from orders.models import SupportTicket

URL = "/api/support/tickets/"
BODY = {"context": "not_found", "part_number": "17940-25099", "vin": "wba8e9g50gnu12345", "message": "Can't find it"}


class GuestTicketTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def post(self, client=None, **body):
        return (client or self.client).post(URL, {**BODY, **body}, format="json", headers={"accept-language": "en"})

    def test_guest_needs_a_valid_contact(self):
        self.assertEqual(self.post().json()["fields"]["contact"][0]["code"], "required")
        self.assertEqual(self.post(contact="not an email").json()["fields"]["contact"][0]["code"], "invalid")
        self.assertFalse(SupportTicket.objects.exists())

    def test_guest_ticket_is_stored_with_session_language_and_normalized_fields(self):
        response = self.post(contact="+995 555 12 34 56")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "context", "status", "created_at"})
        ticket = SupportTicket.objects.get()
        self.assertEqual((ticket.contact, ticket.vin, ticket.language, ticket.customer, bool(ticket.session_key)),
                         ("555123456", "WBA8E9G50GNU12345", "en", None, True))

    def test_guest_ticket_appears_in_the_customer_list_after_login(self):
        make_customer()
        self.post(contact="nino@example.com")
        self.client.post("/api/auth/login/", {"email": "nino@example.com", "password": PASSWORD}, format="json")
        body = self.client.get(URL).json()
        self.assertEqual((body["count"], body["results"][0]["message"]), (1, "Can't find it"))

    def test_guests_are_throttled_and_a_double_submit_is_one_ticket(self):
        first, second = self.post(contact="a@b.ge"), self.post(contact="a@b.ge")
        self.assertEqual(first.json()["id"], second.json()["id"])
        codes = [self.post(contact="a@b.ge", message=f"m{i}").status_code for i in range(4)]
        self.assertEqual(codes, [201, 201, 201, 429])  # support_guest 5/hour (the double submit counted too)

    def test_csrf_is_required_for_guests(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.post(client, contact="a@b.ge").json()["code"], "csrf_failed")

    def test_message_limits(self):
        self.assertEqual(self.post(contact="a@b.ge", message="").json()["fields"]["message"][0]["code"], "required")
        self.assertEqual(self.post(contact="a@b.ge", message="x" * 2001).status_code, 400)
        self.assertEqual(self.post(contact="a@b.ge", vin="BAD").json()["fields"]["vin"][0]["code"], "invalid_vin")

    def test_guests_cannot_list(self):
        self.assertEqual(self.client.get(URL).status_code, 401)


class CustomerTicketTests(TestCase):
    def setUp(self):
        cache.clear()
        self.customer = make_customer()
        self.client = APIClient()
        self.client.force_login(self.customer.user)

    def test_contact_is_ignored_and_customers_are_not_throttled_per_ip(self):
        for i in range(7):
            self.assertEqual(self.client.post(URL, {**BODY, "message": f"m{i}", "contact": "x@y.ge"},
                                              format="json").status_code, 201)
        self.assertEqual(set(SupportTicket.objects.values_list("contact", flat=True)), {""})
        self.assertEqual(SupportTicket.objects.filter(customer=self.customer).count(), 7)

    def test_only_own_tickets_are_listed(self):
        SupportTicket.objects.create(customer=make_customer(email="o@example.com", phone="599000000"),
                                     context="general", message="other")
        self.client.post(URL, BODY, format="json")
        body = self.client.get(URL).json()
        self.assertEqual([t["message"] for t in body["results"]], ["Can't find it"])
        self.assertEqual(set(body["results"][0]), {"id", "context", "part_number", "vin", "message", "status",
                                                   "operator_reply", "replied_at", "created_at"})

    def test_html_is_kept_as_text_and_escaped_in_the_admin(self):
        self.client.post(URL, {**BODY, "message": "<script>alert(1)</script>"}, format="json")
        self.assertEqual(SupportTicket.objects.get().message, "<script>alert(1)</script>")
        admin = APIClient()
        admin.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "x"))
        page = admin.get(f"/admin/orders/supportticket/{SupportTicket.objects.get().pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", page.content.decode())
