"""LPK-220: the overall limit on one AMT lookup. A fake clock moves only when a fake supplier spends time, so nothing here
depends on real time, and nothing touches the network."""

import io
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from parts.amt_provider import AMTProviderError, get_price_by_oem
from parts.providers import PartsProviderError, fetch_rows

ROWS_XML = ("<r><row><oem>A1</oem><descr>Деталь</descr><brand>B</brand><list_price>10.5</list_price></row>"
            "<row><oem>A2</oem><descr>Other</descr><brand>B</brand><list_price>12</list_price></row></r>")


def response(body=ROWS_XML, status=200, encoding="cp1251"):
    real = requests.Response()
    real.status_code, real.encoding, real.raw = status, encoding, io.BytesIO(body.encode(encoding))
    return real


@override_settings(AMT_API_LOGIN="u", AMT_API_PASSWORD="p", AMT_TIMEOUT_SECONDS=15, AMT_LOOKUP_BUDGET_SECONDS=30)
class LookupBudgetTests(SimpleTestCase):
    def setUp(self):
        self.now = 1000.0
        self.timeouts = []
        clock = mock.patch("parts.amt_provider.time")
        clock.start().monotonic.side_effect = lambda: self.now
        self.addCleanup(clock.stop)

    def supplier(self, *behaviour):
        """Each attempt takes the next behaviour; the last one repeats. An exception means the wait ran out."""
        steps = list(behaviour)

        def post(*_args, timeout, **_kwargs):
            self.timeouts.append(timeout)
            step = steps.pop(0) if len(steps) > 1 else steps[0]
            if isinstance(step, Exception):
                self.now += timeout
                raise step
            return step

        return mock.patch("parts.amt_provider.requests.post", side_effect=post)

    def test_a_dead_supplier_stops_at_the_budget_not_after_36_attempts(self):
        with self.supplier(requests.Timeout()), self.assertLogs("parts.amt_provider", "WARNING"), \
                self.assertRaises(AMTProviderError):
            get_price_by_oem("X")
        self.assertEqual(self.timeouts, [15, 15])
        self.assertEqual(self.now, 1030.0)

    @override_settings(AMT_LOOKUP_BUDGET_SECONDS=20)
    def test_an_attempt_never_waits_longer_than_the_budget_left(self):
        with self.supplier(requests.Timeout()), self.assertLogs("parts.amt_provider", "WARNING"), \
                self.assertRaises(AMTProviderError):
            get_price_by_oem("X")
        self.assertEqual(self.timeouts, [15, 5])

    def test_a_valid_answer_after_a_timeout_is_returned(self):
        with self.supplier(requests.Timeout(), response()):
            rows = get_price_by_oem("X")
        self.assertEqual([row["oem"] for row in rows], ["A1", "A2"])
        self.assertEqual(rows[0]["descr"], "Деталь")  # the charset is handled as before
        self.assertEqual(rows[0]["list_price"], 10.5)

    def test_a_slow_trickling_body_cannot_outlast_the_budget(self):
        slow = response()

        def chunks(chunk_size):
            yield b"<r>"
            self.now += 31  # one network wait passes the deadline
            yield b"</r>"

        slow.iter_content = chunks
        with self.supplier(slow), self.assertLogs("parts.amt_provider", "WARNING"), self.assertRaises(AMTProviderError):
            get_price_by_oem("X")
        self.assertEqual(len(self.timeouts), 1)

    def test_a_timeout_is_a_supplier_failure_never_an_empty_result(self):
        with override_settings(PARTS_PROVIDER="amt"), self.supplier(requests.Timeout()), \
                self.assertLogs("parts.amt_provider", "WARNING"), self.assertRaises(PartsProviderError) as caught:
            fetch_rows("X")
        self.assertEqual((caught.exception.code, caught.exception.status_code), ("supplier_unavailable", 502))

    def test_a_response_that_is_not_xml_is_a_supplier_failure_not_a_crash(self):
        with override_settings(PARTS_PROVIDER="amt"), self.supplier(response("<html>busy")), \
                self.assertRaises(PartsProviderError):
            fetch_rows("X")
        self.assertEqual(len(self.timeouts), 36)  # every variant answered at once, so none was cut short

    def test_the_error_and_log_hold_no_credentials(self):
        with override_settings(AMT_API_LOGIN="secret-login", AMT_API_PASSWORD="secret-pass"), \
                self.supplier(requests.Timeout()), self.assertLogs("parts.amt_provider", "WARNING") as logs, \
                self.assertRaises(AMTProviderError) as caught:
            get_price_by_oem("X")
        text = f"{caught.exception} {' '.join(logs.output)}"
        self.assertNotIn("secret", text)
