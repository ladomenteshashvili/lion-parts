# UAT matrix (LPK-201 to LPK-205)

Run on 2026-11-06. **Where:** `J` = a journey in `backend/uat/journeys.py`, which runs in the test suite and over HTTP on staging.
`S` = `backend/uat/tests/test_scenarios.py`, for server-side state a client cannot set. Other paths are existing tests under `backend/`.
Fake payments, the demo supplier and fake SMS only: no real provider is called.

```bash
cd backend && python manage.py test uat          # local, needs no staging
python -m uat.run_http --base-url ... --origin ...   # staging: docs/staging-runbook.md section 6
```

## D1 Authentication

| Scenario | Where |
|---|---|
| Registration, six-digit code | J `guest_search_register_card_payment`; S `test_d1_a_code_is_six_digits_and_only_for_its_session` |
| Resend cooldown | J `otp_errors_in_every_language`; `accounts/test_otp.py::ParallelResendTests` |
| Code expiry, wrong codes, attempts limit | S `test_d1_expired_code`, `test_d1_too_many_wrong_codes_then_the_right_one_fails`; `accounts/test_otp.py::ParallelVerifyTests` |
| Login, logout, wrong password, unknown email | J `login_logout_csrf_and_failures` |
| Password reset | J `password_reset`; `accounts/test_password_reset.py` |
| Email verification | J `guest_search_register_card_payment`; S `test_d1_a_used_email_verification_link_is_harmless_and_a_bad_one_refused` |
| Profile, email and phone changes | J `profile_and_language`; S `test_d1_email_change_needs_the_password_and_the_new_address_link`; `accounts/test_phone_change.py` |
| Session handling | S `test_d1_a_password_change_ends_other_sessions`; `api/tests/test_auth.py` |
| CSRF | J `login_logout_csrf_and_failures` (rotation and refusal); every J runs with CSRF enforced |
| ka, en, ru | J `otp_errors_in_every_language`; S `test_d1_errors_are_localized_and_codes_are_not`; `api/tests/test_language_qa.py` |

## D2 Parts and cart

| Scenario | Where |
|---|---|
| Search, demo provider, missing product | J `search_outcomes_and_support` |
| Supplier unavailable, bad supplier answers | S `test_d2_supplier_unavailable_answers_502_in_every_language`; `parts/test_amt_budget.py` |
| Currency conversion, rate unavailable | S `test_d2_currency_conversion_follows_the_rate_and_keeps_usd`, `test_d2_no_exchange_rate_answers_502_for_gel` |
| Manual price request to order | S `test_d2_manual_price_request_through_the_public_link_into_an_order`, `test_d2_someone_elses_quote_cannot_be_added` |
| Weight quotation | S `test_d2_weight_quotation_needs_the_tariff_and_consent` |
| Guest and customer cart, merge, quantities | J `guest_cart_merges_at_login` |
| Expired quote, offer and line | S `test_d2_expired_line_blocks_checkout_until_refreshed`, `test_d2_an_expired_offer_cannot_be_added` |
| Client prices ignored | S `test_d2_a_price_in_the_request_is_ignored` |
| Concurrent cart changes | `api/tests/test_external_calls.py::ParallelCartAndQuoteTests`; `cart/test_refresh.py` |

## D3 Orders and fake payments

| Scenario | Where |
|---|---|
| Checkout, order, invoice | J `guest_search_register_card_payment`, `invoice_checkout`; S `test_d3_amounts_on_the_invoice_match_the_order` |
| Fake card payment, retries, failed and expired | J `failed_card_payment_then_retry` |
| Pending and unknown payment | J `invoice_checkout`; S `test_d3_unknown_payment_blocks_and_reads_as_being_checked` |
| Duplicate callback, also in parallel | S `test_d3_a_duplicate_paid_result_counts_once`; `payments/test_duplicates.py::ParallelPaymentTests` |
| Late result, reconciliation | S `test_d3_a_paid_result_after_expiry_is_counted_and_visible_to_review`; `payments/test_reconcile.py`, `test_closed_reconciliation.py` |
| Cancellation, refund-pending accounting | J `cancel_before_and_after_payment`; S `test_d3_a_paid_result_after_cancellation_is_owed_back`; `orders/test_operator.py` |
| Invoice confirmed by the operator | S `test_d3_invoice_confirmed_by_the_operator_moves_the_order_on_and_only_once` |
| Price change, balance, courier fee | J `price_change_decision`, `courier_fee_and_notifications` |
| Concurrent operations | `orders/test_checkout.py`, `orders/test_cancel.py`, `orders/test_decisions.py`, `payments/test_lock_order.py`, `payments/test_enforcement.py` |

## D4 Notifications and administration

| Scenario | Where |
|---|---|
| Transactional email, Reply-To, footers in three languages | S `test_d4_emails_carry_from_reply_to_and_a_footer_in_the_customers_language`; `api/tests/test_mail.py` |
| Mailpit-compatible delivery | rehearsed over real SMTP against a sink; the real Mailpit is untested |
| Demo SMS never reaches a provider | S `test_d4_demo_sms_and_email_channels_never_reach_a_provider` |
| Public notification link | S `test_d4_public_notification_link_shows_nothing_personal` |
| Support tickets | J `search_outcomes_and_support`; `orders/test_support_tickets.py` |
| Admin workflows | `orders/test_operator.py`, `payments/test_admin.py`, `orders/test_delivery_admin.py`, `pricing/tests/test_admin.py` |
| Access control | S `test_d4_a_customer_is_refused_on_operator_routes_and_a_staff_session_on_customer_routes`, `test_d4_staff_without_the_permission_cannot_change_orders`; `api/tests/test_access.py` |
| Cross-customer isolation | J `customers_cannot_see_each_other`; `orders/test_order_api.py` |
| Sensitive-token masking | `api/tests/test_log_secrets.py`; nginx masking rehearsed (`docs/staging-runbook.md` section 10) |

## D5 Load and reliability

`backend/uat/load_test.py`. **Isolated staging only:** it refuses a target without the demo routes, and at most 16 workers and 400 requests per
run. Never run it against any existing server.

```bash
venv/bin/python -m uat.load_test --base-url https://<host> --origin https://<frontend origin> --scenario <name> --workers 8 --each 5
```

Between scenarios, clear the limits on staging only (`docs/staging-runbook.md` section 6), or `otp` uses up the OTP limit for the rest.
Alongside each run, watch `SELECT count(*), state FROM pg_stat_activity WHERE datname = 'lp_stage_v1' GROUP BY state;`.

| Requirement | Scenario | Expected | Rehearsal, shared host, 2 x 4 threads |
|---|---|---|---|
| Parallel logins | `login` | exactly 10 not limited per minute, the rest `429` | 10 `400`, 30 `429`; p95 0.95 s (password hashing) |
| OTP requests | `otp` | at most one send, the rest `409`/`429` | 4 `409`, 36 `429` |
| Cart changes | `cart` | all `200`, ordered by the cart lock | 40 `200`; p95 0.08 s |
| Checkout | `checkout` | each round exactly one `201` | 5 rounds of 8: exactly one `201` each round, the others `409`; p95 0.09 s |
| Quote approval | `quote_approval` | one consistent price, no server error; losers may get the "request changed, try again" `400`; needs a staff account and a tariff allowed to request | 4 `200`, 12 `400`, 0 server errors |
| Slow supplier | `slow_supplier` with `uat/slow_supplier.py` (`PARTS_PROVIDER=amt`, `AMT_ENDPOINT` on loopback) | `502` at the 30 s budget | silent and trickling: all `502` at 30.0 s |
| Timeouts | same | no gunicorn worker timeout | 0 worker timeouts |
| Connection limits | any | connections at most workers x threads + 1 | 8 idle, 1 active, during and after |
| Cache contention | `login`, `otp` | limits exact, no errors | as above |

Supplier calls hold request threads, so staging keeps `PARTS_PROVIDER=demo` until B7, and real AMT needs its own measured thread budget
(`docs/operations.md` section 4). These numbers come from a rehearsal on a shared host: treat them as behaviour, not as capacity.

## Still missing

- Real HTTP runs on the actual staging, which does not exist yet (needs approval).
- A browser: CSP reports, cookies across origins, and the frontend itself. `api/tests/test_csp_coverage.py` only reads the HTML.
- Real Mailpit, real SMS, real AMT (B7) and any real payment provider (B1, B5).
- Long-running behaviour: `cache_table` growth and the cron jobs over days.
- Native-speaker review of texts (B3).
