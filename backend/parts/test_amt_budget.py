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

    def test_an_attempt_starting_at_the_deadline_gets_a_valid_timeout(self):
        from parts.amt_provider import _post

        with self.supplier(response()), self.assertRaises(requests.Timeout):  # no ValueError from a zero timeout
            _post("<x/>", {}, deadline=self.now)
        self.assertEqual(self.timeouts, [0.01])

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


@override_settings(AMT_API_LOGIN="u", AMT_API_PASSWORD="p", PARTS_PROVIDER="amt", AMT_TIMEOUT_SECONDS=15,
                   AMT_LOOKUP_BUDGET_SECONDS=30)
class SupplierAnswerTests(SimpleTestCase):
    """Every bad answer ends as supplier_unavailable (502), never as a crash or as "no parts"."""

    def fetch(self, *answers):
        with mock.patch("parts.amt_provider.requests.post", side_effect=lambda *a, **k: next(replies)) as post:
            replies = iter([*answers, *[answers[-1]] * 40])
            try:
                return fetch_rows("X"), post.call_count
            except PartsProviderError as error:
                self.assertEqual((error.code, error.status_code), ("supplier_unavailable", 502))
                return None, post.call_count

    def test_http_errors_are_a_supplier_failure(self):
        for status in (403, 500, 503):
            with self.subTest(status=status):
                self.assertIsNone(self.fetch(response("busy", status=status))[0])

    def test_connection_failures_are_a_supplier_failure(self):
        for error in (requests.ConnectionError(), requests.exceptions.SSLError()):
            with self.subTest(error=error), mock.patch("parts.amt_provider.requests.post", side_effect=error), \
                    self.assertRaises(PartsProviderError):
                fetch_rows("X")

    def test_a_connection_lost_during_the_body_is_a_supplier_failure(self):
        partial = response()
        partial.iter_content = mock.Mock(side_effect=requests.exceptions.ChunkedEncodingError())
        self.assertIsNone(self.fetch(partial)[0])

    def test_a_truncated_body_is_a_supplier_failure(self):
        self.assertIsNone(self.fetch(response(ROWS_XML[:40]))[0])

    def test_well_formed_answers_with_an_unexpected_shape_do_not_crash(self):
        for body in ("<Map/>", "<Map>text</Map>", "<r><Map>text</Map></r>", "<r/>", "<r><row>text</row></r>"):
            with self.subTest(body=body):
                self.assertIsNone(self.fetch(*[response(body) for _ in range(36)])[0])

    def test_a_list_mixing_rows_and_text_keeps_the_rows(self):
        body = "<r><row><oem>A1</oem><brand>B</brand><list_price>10</list_price></row><row>text</row></r>"
        result, _calls = self.fetch(response(body))
        self.assertEqual([row["oem"] for row in result[1]], ["A1"])

    def test_a_list_mixing_maps_and_text_keeps_the_maps(self):
        pair = "<item><key>{}</key><value>{}</value></item>"
        row = f"<row><Map>{pair.format('oem', 'A1')}{pair.format('brand', 'B')}{pair.format('list_price', '10')}</Map></row>"
        result, _calls = self.fetch(response(f"<r>{row}<row>text</row></r>"))
        self.assertEqual([row["oem"] for row in result[1]], ["A1"])

    def test_the_suppliers_no_part_answer_is_an_empty_result_not_a_failure(self):
        body = "<r><row><oem>X</oem><descr>NO_PART_FOUND</descr></row></r>"
        result, calls = self.fetch(response(body))
        self.assertEqual((result[2], calls), ([], 1))

    def test_a_single_plain_row_is_a_result(self):
        body = "<r><row><oem>A1</oem><brand>B</brand><list_price>10</list_price></row></r>"
        result, calls = self.fetch(response(body))
        self.assertEqual(([row["oem"] for row in result[1]], calls), (["A1"], 1))

    def test_incorrect_parameters_stop_at_once(self):
        result, calls = self.fetch(response("Incorrect parameters"))
        self.assertEqual((result, calls), (None, 1))

    def test_a_valid_answer_after_a_bad_one_is_used(self):
        result, calls = self.fetch(response("<html>busy"), response())
        self.assertEqual((len(result[1]), calls), (2, 2))
