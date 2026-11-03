"""LP-BE-105 (LPK-100): pagination, meta and health (G§4.5, G§8.1)."""

from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny

from api.pagination import Pagination

META_KEYS = {"languages", "default_language", "currencies", "usd_rate", "pickup_points", "delivery",
             "otp_length", "otp_resend_seconds", "password_rules"}


class CountedPagination(Pagination):
    def get_paginated_response(self, data):
        return super().get_paginated_response(data, new_count=2)


def list_view(pagination_class):
    @api_view(["GET"])
    @permission_classes([AllowAny])
    def view(request):
        paginator = pagination_class()
        page = paginator.paginate_queryset(list(range(1, 46)), request)
        return paginator.get_paginated_response(page)
    return view


urlpatterns = [path("items/", list_view(Pagination)), path("counted/", list_view(CountedPagination))]


class MetaTests(SimpleTestCase):
    def test_meta_returns_every_g81_key(self):
        response = self.client.get("/api/meta/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), META_KEYS)
        self.assertEqual(data["delivery"], {"country": "GE", "cities": [], "districts": []})
        self.assertEqual((data["currencies"], data["usd_rate"], data["pickup_points"]), ([], None, []))
        self.assertEqual((data["otp_length"], data["otp_resend_seconds"]), (6, 60))
        self.assertEqual(data["password_rules"],
                         {"min_length": 8, "upper": True, "lower": True, "digit": True, "symbol": True})

    def test_password_min_length_follows_validator_settings(self):
        validators = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
                       "OPTIONS": {"min_length": 12}}]
        with self.settings(AUTH_PASSWORD_VALIDATORS=validators):
            self.assertEqual(self.client.get("/api/meta/").json()["password_rules"]["min_length"], 12)

    def test_health(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.json(), {"status": "ok", "service": "lion-parts-backend"})


@override_settings(ROOT_URLCONF=__name__)
class PaginationTests(SimpleTestCase):
    def test_page_shape_and_size(self):
        data = self.client.get("/items/?page=2").json()
        self.assertEqual(list(data), ["count", "next", "previous", "results"])
        self.assertEqual(data["count"], 45)
        self.assertEqual(data["results"], list(range(21, 41)))
        self.assertTrue(data["next"].endswith("/items/?page=3"))
        self.assertTrue(data["previous"].endswith("/items/"))  # page 1 link

    def test_last_page_and_out_of_range(self):
        data = self.client.get("/items/?page=3").json()
        self.assertEqual((data["results"], data["next"]), (list(range(41, 46)), None))
        response = self.client.get("/items/?page=9")
        self.assertEqual((response.status_code, response.json()["code"]), (404, "not_found"))

    def test_subclass_adds_documented_aggregate_after_count(self):
        data = self.client.get("/counted/").json()
        self.assertEqual(list(data), ["count", "new_count", "next", "previous", "results"])
        self.assertEqual(data["new_count"], 2)
