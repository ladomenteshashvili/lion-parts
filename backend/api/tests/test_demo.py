"""LP-BE-106 (LPK-101): demo endpoints switch and email outbox (G§4.10, G§8.10)."""

import importlib
from email.utils import formatdate

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.urls import Resolver404, clear_url_caches, resolve

import config.urls
from config.tests import run_python


def reload_urls():
    clear_url_caches()
    importlib.reload(config.urls)


class DemoSwitchTests(SimpleTestCase):
    def tearDown(self):
        reload_urls()  # back to the configured ENABLE_DEMO_ENDPOINTS

    def test_disabled_demo_routes_do_not_exist(self):
        with self.settings(ENABLE_DEMO_ENDPOINTS=False):
            reload_urls()
            with self.assertRaises(Resolver404):
                resolve("/api/demo/outbox/")
            self.assertEqual(self.client.get("/api/demo/outbox/").status_code, 404)

    def test_enabled_demo_routes_exist(self):
        with self.settings(ENABLE_DEMO_ENDPOINTS=True):
            reload_urls()
            self.assertEqual(resolve("/api/demo/outbox/").url_name, "demo-outbox")

    def test_demo_endpoints_are_off_by_default(self):
        result = run_python("import os; os.environ.pop('ENABLE_DEMO_ENDPOINTS', None);"
                            "import config.settings as s; print(s.ENABLE_DEMO_ENDPOINTS)")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "False"), result.stderr)


@override_settings(ENABLE_DEMO_ENDPOINTS=True)
class OutboxTests(SimpleTestCase):
    def setUp(self):
        reload_urls()

    def tearDown(self):
        reload_urls()

    def send(self, i, **headers):
        mail.EmailMessage(subject=f"Verify {i}", body=f"Open http://localhost:5173/verify-email?token=t{i}&lang=en now",
                          to=[f"user{i}@example.com"], headers=headers).send()

    def test_sent_email_appears_with_extracted_link(self):
        self.send(1, Date=formatdate(1792828800, usegmt=True))
        self.assertEqual(self.client.get("/api/demo/outbox/").json(), [{
            "to": ["user1@example.com"], "subject": "Verify 1",
            "body": "Open http://localhost:5173/verify-email?token=t1&lang=en now",
            "links": ["http://localhost:5173/verify-email?token=t1&lang=en"],
            "sent_at": "2026-10-24T08:00:00Z",
        }])

    def test_last_fifty_messages_only(self):
        for i in range(55):
            self.send(i)
        data = self.client.get("/api/demo/outbox/").json()
        self.assertEqual(len(data), 50)
        self.assertEqual((data[0]["subject"], data[-1]["subject"]), ("Verify 5", "Verify 54"))
        self.assertIsNone(data[0]["sent_at"])  # no Date header -> null

    def test_non_locmem_backend_returns_empty_list(self):
        self.send(1)
        with self.settings(MAILERS={"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}):
            self.assertEqual(self.client.get("/api/demo/outbox/").json(), [])
