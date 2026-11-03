"""LP-BE-203: OTP service & SMS wrapper (G§5.7)."""

import threading
import unittest
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from accounts import otp
from accounts.models import PhoneVerificationCode
from accounts.sms import SenderGeError, send_sms
from api.errors import ApiError

ARGS = {"session_key": "s1", "phone": "555123456", "purpose": "registration"}


def send(**kwargs):
    return otp.send_code(**{**ARGS, **kwargs})


def verify(code, **kwargs):
    return otp.verify_code(**{**ARGS, **kwargs}, code=code)


def error_of(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except ApiError as exc:
        return {"code": exc.code, "status": exc.status_code, **exc.extra}
    return None


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True, DEBUG=False)
class SendCodeTests(TestCase):
    def test_send_returns_contract_and_stores_only_a_hash(self):
        with mock.patch("accounts.otp.send_sms", return_value={"messageId": 77}) as sms:
            data = send()
        self.assertEqual({k: data[k] for k in ("masked_phone", "expires_in", "resend_in")},
                         {"masked_phone": "+995 555 •• •• 56", "expires_in": 300, "resend_in": 60})
        code = data["demo_code"]
        self.assertRegex(code, r"^\d{6}$")
        row = PhoneVerificationCode.objects.get()
        self.assertNotIn(code, row.code_hash)
        self.assertTrue(row.check_code(code))
        self.assertEqual((row.status, row.provider_message_id), ("pending", "77"))
        self.assertEqual(sms.call_args.args[0], "555123456")
        self.assertIn(code, sms.call_args.args[1])

    def test_resend_within_cooldown_is_rejected_with_resend_in(self):
        send()
        err = error_of(send)
        self.assertEqual((err["code"], err["status"]), ("otp_resend_too_soon", 409))
        self.assertTrue(0 < err["resend_in"] <= 60)
        self.assertEqual(PhoneVerificationCode.objects.count(), 1)

    def test_resend_after_cooldown_expires_the_older_code(self):
        first = send()["demo_code"]
        PhoneVerificationCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        second = send()["demo_code"]
        statuses = list(PhoneVerificationCode.objects.order_by("created_at").values_list("status", flat=True))
        self.assertEqual(statuses, ["expired", "pending"])
        self.assertEqual(error_of(verify, first)["code"], "otp_invalid")  # only the newest code counts
        verify(second)

    def test_cooldown_is_per_session_phone_and_purpose(self):
        send()
        send(session_key="s2")
        send(phone="599000000")
        send(purpose="phone_change")
        self.assertEqual(PhoneVerificationCode.objects.count(), 4)

    def test_sms_failure_returns_502_and_stores_no_usable_code(self):
        with mock.patch("accounts.otp.send_sms", side_effect=SenderGeError("down")):
            err = error_of(send)
        self.assertEqual((err["code"], err["status"]), ("sms_failed", 502))
        self.assertFalse(PhoneVerificationCode.objects.exists())

    def test_sms_text_uses_the_given_language(self):
        texts = {}
        for i, lang in enumerate(("ka", "en", "ru")):
            with mock.patch("accounts.otp.send_sms", return_value={}) as sms:
                code = send(session_key=f"lang{i}", language=lang)["demo_code"]
            texts[lang] = sms.call_args.args[1].replace(code, "{code}")
        self.assertEqual(texts["en"], "Lion Parts code: {code}")
        self.assertEqual(len(set(texts.values())), 3)

    def test_demo_code_rules(self):
        cases = [  # SMS_DEMO_MODE, DEBUG, ENABLE_DEMO_ENDPOINTS -> demo_code present
            (False, True, True, False), (True, False, False, False),
            (True, True, False, True), (True, False, True, True),
        ]
        for i, (demo, debug, endpoints, present) in enumerate(cases):
            with self.subTest(demo=demo, debug=debug, endpoints=endpoints), \
                    self.settings(SMS_DEMO_MODE=demo, DEBUG=debug, ENABLE_DEMO_ENDPOINTS=endpoints,
                                  SENDER_GE_ENABLED=True), \
                    mock.patch("accounts.otp.send_sms", return_value={}):
                self.assertEqual("demo_code" in send(session_key=f"d{i}"), present)

    @override_settings(SENDER_GE_ENABLED=False, SMS_DEMO_MODE=False)
    def test_disabled_sms_without_demo_codes_fails_loudly(self):
        with mock.patch("accounts.otp.send_sms") as sms, self.assertLogs("accounts.otp", "ERROR") as logs:
            err = error_of(send)
        self.assertEqual((err["code"], err["status"]), ("sms_failed", 502))
        sms.assert_not_called()
        self.assertFalse(PhoneVerificationCode.objects.exists())
        self.assertIn("SMS delivery is disabled", logs.output[0])


class SendSmsTests(SimpleTestCase):
    @override_settings(SENDER_GE_ENABLED=False)
    def test_disabled_delivery_is_logged_without_the_content(self):
        with mock.patch("accounts.sms.requests.post") as post, self.assertLogs("accounts.sms", "WARNING") as logs:
            result = send_sms("555123456", "Lion Parts code: 123456")
        post.assert_not_called()
        self.assertEqual(result["provider"], "console")
        self.assertIn("SENDER_GE_ENABLED=False", logs.output[0])
        self.assertIn("56", logs.output[0])
        self.assertNotIn("123456", logs.output[0])
        self.assertNotIn("555123456", logs.output[0])


@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class VerifyCodeTests(TestCase):
    def setUp(self):
        self.code = send()["demo_code"]
        self.wrong = "000000" if self.code != "000000" else "111111"

    def test_correct_code_verifies_once(self):
        row = verify(self.code)
        self.assertEqual(row.status, "verified")
        self.assertIsNotNone(row.verified_at)
        self.assertEqual(error_of(verify, self.code)["code"], "otp_expired")  # single use

    def test_five_wrong_codes_then_attempts_exceeded(self):
        lefts = [error_of(verify, self.wrong)["attempts_left"] for _ in range(5)]
        self.assertEqual(lefts, [4, 3, 2, 1, 0])
        self.assertEqual(error_of(verify, self.code)["code"], "otp_attempts_exceeded")
        self.assertEqual(PhoneVerificationCode.objects.get().status, "failed")

    def test_wrong_code_response_carries_attempts_left(self):
        err = error_of(verify, self.wrong)
        self.assertEqual((err["code"], err["status"], err["attempts_left"]), ("otp_invalid", 400, 4))
        self.assertEqual(PhoneVerificationCode.objects.get().attempts, 1)  # saved despite the error

    def test_expired_code(self):
        PhoneVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(error_of(verify, self.code)["code"], "otp_expired")
        self.assertEqual(PhoneVerificationCode.objects.get().status, "expired")

    def test_code_is_bound_to_session_phone_and_purpose(self):
        for kwargs in ({"session_key": "other"}, {"phone": "599000000"}, {"purpose": "phone_change"}):
            with self.subTest(**kwargs):
                self.assertEqual(error_of(verify, self.code, **kwargs)["code"], "otp_expired")
        verify(self.code)


@unittest.skipUnless(connection.vendor == "postgresql", "row locks need PostgreSQL (CI)")
@override_settings(SMS_DEMO_MODE=True, ENABLE_DEMO_ENDPOINTS=True)
class ParallelVerifyTests(TransactionTestCase):
    serialized_rollback = True  # keep the seeded default tariff for later tests

    def test_parallel_wrong_guesses_cannot_exceed_max_attempts(self):
        code = send()["demo_code"]
        wrong = "000000" if code != "000000" else "111111"
        results, barrier = [], threading.Barrier(10)

        def guess():
            barrier.wait()
            try:
                results.append(error_of(verify, wrong)["code"])
            finally:
                connection.close()

        threads = [threading.Thread(target=guess) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("otp_invalid"), 5)
        self.assertEqual(PhoneVerificationCode.objects.get().attempts, 5)
