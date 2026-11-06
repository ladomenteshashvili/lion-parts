"""Customer journeys for UAT (LPK-201-205). Standard library only, so the same steps run in two places:
uat/tests/test_journeys.py (Django test client, in CI) and uat/run_http.py (real HTTP, against the isolated staging).
Each journey talks only to the public API and asserts what docs/api-handoff.md promises."""

import random
import uuid
from urllib.parse import parse_qs, urlsplit

PASSWORD = "Uat-S3cure!pass"
PART = "G9010-60020"
NOT_FOUND_PART = "NF-404"


class Api:
    """One browser session: cookies live in the transport; this adds CSRF and the language, and checks each answer."""

    def __init__(self, transport, language="en", record=None):
        self.transport, self.language, self.record, self.csrf = transport, language, record, None

    def call(self, method, path, body=None, expect=200, code=None):
        headers = {"Accept-Language": self.language}
        if method != "GET":
            if self.csrf is None:
                self.csrf = self.transport("GET", "/api/auth/csrf/", None, {})[1]["csrf_token"]
            headers["X-CSRFToken"] = self.csrf
        status, data, response_headers = self.transport(method, path, body, headers)
        if self.record is not None:
            self.record.append({"method": method, "path": path, "request": body, "status": status, "response": data,
                                "language": self.language})
        assert status == expect, f"{method} {path}: expected {expect}, got {status}: {str(data)[:400]}"
        if code is not None:
            assert data.get("code") == code, f"{method} {path}: expected code {code}: {data}"
        if isinstance(data, dict) and data.get("csrf_token"):
            self.csrf = data["csrf_token"]
        return data

    def get(self, path, **kwargs):
        return self.call("GET", path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.call("POST", path, {} if body is None else body, **kwargs)

    def patch(self, path, body, **kwargs):
        return self.call("PATCH", path, body, **kwargs)

    def delete(self, path, body=None, **kwargs):
        return self.call("DELETE", path, body, **kwargs)


class Context:
    """What a runner provides: client(language), staff() (an Api or None), email_link(address, marker) -> URL.
    The runner's clients share `record`, so a run can be written out as a transcript."""

    def __init__(self):
        self.record, self.shared = [], None

    def client(self, language="en"):
        raise NotImplementedError

    def staff(self):
        return None

    def email_link(self, address, marker):
        raise NotImplementedError


def new_identity():
    tag = uuid.uuid4().hex[:10]
    return {"account_type": "physical", "first_name": "Uat", "last_name": "Tester",
            "personal_id": f"{random.randrange(10**10, 10**11)}", "phone": f"5{random.randrange(10**7, 10**8)}",
            "email": f"uat-{tag}@example.com", "password": PASSWORD, "terms_accepted": True, "marketing_opt_in": False}


def query_of(link):
    """The parameters of an emailed link, decoded as the frontend's URLSearchParams would."""
    return {key: values[0] for key, values in parse_qs(urlsplit(link).query).items()}


def token_of(link):
    return query_of(link)["token"]


def register(ctx, language="en", api=None):
    """-> (api, identity): logged in, phone verified, email not yet."""
    api, identity = api or ctx.client(language), new_identity()
    sent = api.post("/api/auth/register/", {**identity, "preferred_language": language})
    assert {"masked_phone", "expires_in", "resend_in", "demo_code"} <= set(sent), sent
    done = api.post("/api/auth/register/verify-phone/", {"code": sent["demo_code"]}, expect=201)
    assert done["customer"]["is_phone_verified"] and not done["customer"]["is_email_verified"]
    return api, identity


def verify_email(ctx, api, identity):
    link = ctx.email_link(identity["email"], "/verify-email")
    answer = api.post("/api/auth/email/verify/", {"token": token_of(link)})
    assert answer["verified"] is True and answer["customer"]["is_fully_verified"] is True


def customer(ctx):
    """A fully verified customer with an empty cart; one per run, because registration is rate limited per IP."""
    if ctx.shared is None:
        api, identity = register(ctx)
        verify_email(ctx, api, identity)
        ctx.shared = api, identity
    api = ctx.shared[0]
    for line in api.get("/api/cart/")["items"]:
        api.delete(f"/api/cart/items/{line['id']}/")
    return ctx.shared


def add_to_cart(api, quantity=1, part=PART):
    found = api.post("/api/parts/search/", {"part_number": part})
    offer = next(o for o in found["offers"] if o["status"] == "ready")
    return api.post("/api/cart/items/", {"source": "search", "search_id": found["search_id"],
                                         "offer_id": offer["offer_id"], "quantity": quantity}, expect=201)


def checkout(api, method="card"):
    options = api.get("/api/checkout/options/")
    answer = api.post("/api/orders/checkout/", {
        "delivery_method": "pickup", "pickup_point_id": options["pickup_points"][0]["id"], "payment_option": "full",
        "payment_method": method, "terms_accepted": True}, expect=201)
    return answer["order"], answer["payment"]


def paid_order(api):
    add_to_cart(api)
    order, payment = checkout(api)
    api.post(f"/api/payments/fake/{payment['id']}/complete/", {"status": "paid"})
    return api.get(f"/api/orders/{order['order_number']}/")


# 1
def guest_search_register_card_payment(ctx):
    """Guest cart survives registration; card checkout with the fake provider; order moves to processing."""
    guest = ctx.client()
    assert guest.get("/api/auth/me/")["authenticated"] is False
    assert add_to_cart(guest)["summary"]["items_count"] == 1
    api, identity = register(ctx, api=guest)
    cart = api.get("/api/cart/")
    assert cart["summary"]["items_count"] == 1, "the guest cart moves to the new account"
    api.get("/api/checkout/options/", expect=403, code="verification_required")
    verify_email(ctx, api, identity)
    options = api.get("/api/checkout/options/")
    order, payment = checkout(api)
    assert options["summary"]["total"] == order["total"]
    assert payment["status"] == "pending" and "fake=1" in payment["checkout_url"]
    done = api.post(f"/api/payments/fake/{payment['id']}/complete/", {"status": "paid"})
    assert done["status"] == "paid"
    number = order["order_number"]
    assert api.get(f"/api/orders/{number}/payments/{payment['id']}/")["status"] == "paid"
    detail = api.get(f"/api/orders/{number}/")
    assert (detail["status"], detail["payment_status"], detail["balance_due"]) == ("processing", "paid", "0.00")
    assert api.get("/api/cart/")["items"] == []


# 2
def login_logout_csrf_and_failures(ctx):
    _api, identity = customer(ctx)
    api = ctx.client()
    wrong = api.post("/api/auth/login/", {"email": identity["email"], "password": "wrong"}, expect=400,
                     code="invalid_credentials")
    unknown = api.post("/api/auth/login/", {"email": "nobody@example.com", "password": "wrong"}, expect=400)
    assert wrong == unknown, "an unknown email gets the same answer"
    api.get("/api/profile/", expect=401, code="not_authenticated")
    before = api.csrf
    api.post("/api/auth/login/", {"email": identity["email"].upper(), "password": PASSWORD})
    assert api.csrf != before, "login rotates the CSRF token"
    assert api.get("/api/auth/me/")["authenticated"] is True
    stale = api.csrf
    api.csrf = "invalid"
    api.post("/api/auth/logout/", expect=403, code="csrf_failed")
    api.csrf = stale
    api.post("/api/auth/logout/")
    assert api.get("/api/auth/me/")["authenticated"] is False
    api.post("/api/profile/password/", {}, expect=401)


# 3
def otp_errors_in_every_language(ctx):
    details = {}
    for language in ("ka", "en", "ru"):
        api, identity = ctx.client(language), new_identity()
        sent = api.post("/api/auth/register/", {**identity, "preferred_language": language})
        wrong = "000000" if sent["demo_code"] != "000000" else "111111"
        answer = api.post("/api/auth/register/verify-phone/", {"code": wrong}, expect=400, code="otp_invalid")
        assert answer["attempts_left"] == 4
        too_soon = api.post("/api/auth/register/resend-code/", expect=409, code="otp_resend_too_soon")
        assert 0 < too_soon["resend_in"] <= 60
        details[language] = answer["detail"]
    assert len(set(details.values())) == 3, f"each language has its own text: {details}"


# 4
def password_reset(ctx):
    _api, identity = customer(ctx)
    api = ctx.client()
    silent = api.post("/api/auth/password-reset/", {"email": "nobody@example.com"})
    assert api.post("/api/auth/password-reset/", {"email": identity["email"]}) == silent, "no account enumeration"
    link = ctx.email_link(identity["email"], "/reset-password")
    uid, token = query_of(link)["uid"], token_of(link)
    api.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": "wrong", "new_password": "An0ther!pass"},
             expect=400, code="token_invalid")
    api.post("/api/auth/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "An0ther!pass"})
    api.post("/api/auth/login/", {"email": identity["email"], "password": "An0ther!pass"})
    api.post("/api/profile/password/", {"current_password": "An0ther!pass", "new_password": PASSWORD})
    ctx.shared = api, identity  # a password reset ends the earlier sessions


# 5
def guest_cart_merges_at_login(ctx):
    _api, identity = customer(ctx)
    owner = ctx.client()
    owner.post("/api/auth/login/", {"email": identity["email"], "password": PASSWORD})
    add_to_cart(owner, quantity=2)
    owner.post("/api/auth/logout/")
    guest = ctx.client()
    line = add_to_cart(guest)["items"][0]
    changed = guest.patch(f"/api/cart/items/{line['id']}/", {"quantity": 3})
    assert changed["items"][0]["quantity"] == 3 and changed["summary"]["items_count"] == 3
    guest.patch(f"/api/cart/items/{line['id']}/", {"quantity": 100}, expect=400, code="validation_error")
    guest.post("/api/auth/login/", {"email": identity["email"], "password": PASSWORD})
    merged = guest.get("/api/cart/")
    assert merged["merge"]["merged"] is True and merged["items"][0]["quantity"] == 5
    assert guest.get("/api/cart/")["merge"] is None, "the merge summary is shown once"
    guest.delete(f"/api/cart/items/{merged['items'][0]['id']}/")
    assert guest.get("/api/cart/")["summary"]["items_count"] == 0


# 6
def invoice_checkout(ctx):
    api, _identity = customer(ctx)
    add_to_cart(api)
    order, payment = checkout(api, method="invoice")
    assert (payment["status"], payment["checkout_url"], payment["provider"]) == ("pending", None, "invoice")
    number = order["order_number"]
    detail = api.get(f"/api/orders/{number}/")
    assert detail["invoice_url"], detail
    blocked = api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=409, code="payment_in_progress")
    assert blocked["payment"]["id"] == payment["id"]
    for language in ("ka", "en", "ru"):
        html = api.get(f"/api/orders/{number}/invoice/?lang={language}")
        assert number in html, "the invoice is an HTML page naming the order"


# 7
def failed_card_payment_then_retry(ctx):
    api, _identity = customer(ctx)
    add_to_cart(api)
    order, payment = checkout(api)
    number = order["order_number"]
    api.post(f"/api/payments/fake/{payment['id']}/complete/", {"status": "failed"})
    api.post(f"/api/payments/fake/{payment['id']}/complete/", {"status": "paid"}, expect=409, code="nothing_to_pay")
    assert api.get(f"/api/orders/{number}/")["status"] == "payment_pending"
    retry = api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=201)
    api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=409, code="payment_in_progress")
    api.post(f"/api/payments/fake/{retry['id']}/complete/", {"status": "expired"})
    last = api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=201)
    api.post(f"/api/payments/fake/{last['id']}/complete/", {"status": "paid"})
    detail = api.get(f"/api/orders/{number}/")
    assert detail["payment_status"] == "paid" and detail["status"] == "processing"
    api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=409, code="nothing_to_pay")
    assert [p["status"] for p in detail["payments"]].count("paid") == 1


# 8
def cancel_before_and_after_payment(ctx):
    api, _identity = customer(ctx)
    add_to_cart(api)
    unpaid, _payment = checkout(api)
    cancelled = api.post(f"/api/orders/{unpaid['order_number']}/cancel/", {"reason": "UAT"})
    assert cancelled["status"] == "cancelled" and cancelled["payment_status"] == "unpaid"
    api.post(f"/api/orders/{unpaid['order_number']}/payments/", {"method": "card"}, expect=409, code="nothing_to_pay")
    paid = paid_order(api)
    assert paid["can_cancel"] is True
    refund = api.post(f"/api/orders/{paid['order_number']}/cancel/", {})
    assert (refund["status"], refund["payment_status"]) == ("cancelled", "refund_pending")
    assert refund["balance_due"] == f"-{paid['amount_paid']}"


# 9
def search_outcomes_and_support(ctx):
    guest = ctx.client()
    missing = guest.post("/api/parts/search/", {"part_number": NOT_FOUND_PART})
    assert missing["not_found"] is True and missing["offers"] == []
    guest.post("/api/parts/search/", {"part_number": ""}, expect=400, code="validation_error")
    ticket = guest.post("/api/support/tickets/", {"context": "not_found", "part_number": NOT_FOUND_PART,
                                                  "message": "Please find this part", "contact": "555000111"}, expect=201)
    assert ticket["status"] == "new"
    guest.get("/api/support/tickets/", expect=401)
    api, _identity = customer(ctx)
    api.post("/api/support/tickets/", {"context": "general", "message": "A question"}, expect=201)
    assert api.get("/api/support/tickets/")["count"] >= 1


# 10
def customers_cannot_see_each_other(ctx):
    owner, _identity = customer(ctx)
    order = paid_order(owner)
    number, payment = order["order_number"], order["payments"][0]["id"]
    stranger, _ = register(ctx)
    for path in (f"/api/orders/{number}/", f"/api/orders/{number}/payments/{payment}/", f"/api/orders/{number}/invoice/"):
        stranger.get(path, expect=404, code="not_found")
    stranger.post(f"/api/orders/{number}/cancel/", {}, expect=404)
    stranger.post(f"/api/payments/fake/{payment}/complete/", {"status": "paid"}, expect=404)
    anonymous = ctx.client()
    anonymous.get(f"/api/orders/{number}/", expect=401)
    anonymous.get("/api/public/n/not-a-real-token/", expect=404)


# 11
def profile_and_language(ctx):
    api, identity = customer(ctx)
    assert api.get("/api/profile/")["email"] == identity["email"]
    assert api.patch("/api/profile/", {"preferred_language": "ru"})["preferred_language"] == "ru"
    api.patch("/api/profile/", {"preferred_language": "de"}, expect=400, code="validation_error")
    api.patch("/api/profile/", {"preferred_language": "en"})
    api.post("/api/profile/password/", {"current_password": "wrong", "new_password": "An0ther!pass"}, expect=400)
    other = new_identity()["phone"]
    sent = api.post("/api/profile/phone/", {"phone": other})
    api.post("/api/profile/phone/verify/", {"code": sent["demo_code"]})
    assert api.get("/api/profile/")["phone"] == other


# 12 (staff)
def price_change_decision(ctx):
    staff = ctx.staff()
    if staff is None:
        return "skipped: no staff account given"
    api, _identity = customer(ctx)
    order = paid_order(api)
    number, item = order["order_number"], order["items"][0]
    proposed = f"{float(item['unit_price']) + 10:.2f}"
    staff.post(f"/api/demo/orders/{number}/items/{item['id']}/action/",
               {"action_type": "price_change", "message": "The supplier price changed", "proposed_unit_price": proposed})
    asked = api.get(f"/api/orders/{number}/")
    assert asked["status"] == "action_required" and asked["items"][0]["action"]["type"] == "price_change"
    decided = api.post(f"/api/orders/items/{item['id']}/decision/", {"decision": "accept"})
    assert decided["balance_due"] == "10.00" and decided["payment_status"] == "partially_paid"
    api.post(f"/api/orders/items/{item['id']}/decision/", {"decision": "accept"}, expect=409, code="no_pending_action")
    balance = api.post(f"/api/orders/{number}/payments/", {"method": "card"}, expect=201)
    assert balance["kind"] == "balance" and balance["amount"] == "10.00"
    api.post(f"/api/payments/fake/{balance['id']}/complete/", {"status": "paid"})
    assert api.get(f"/api/orders/{number}/")["balance_due"] == "0.00"


# 13 (staff)
def manual_price_request(ctx):
    staff = ctx.staff()
    if staff is None:
        return "skipped: no staff account given"
    api, _identity = customer(ctx)
    tariff = api.get("/api/profile/")["tariff"]
    if not tariff["can_request_quote"]:
        api.post("/api/parts/quote-requests/", {"request_type": "manual_search", "part_number": PART}, expect=403,
                 code="quote_request_not_allowed")
        return "default tariff cannot request prices: refusal checked"
    made = api.post("/api/parts/quote-requests/", {"request_type": "manual_search", "part_number": PART}, expect=201)
    assert made["is_price_ready"] is False
    staff.post(f"/api/demo/quote-requests/{made['id']}/ready/", {"weight_kg": "2.00", "operator_message": "Ready"},
               expect=400)  # a manual search has no supplier price until the operator enters one
    return "refusal without a supplier price checked"


# 14 (staff)
def courier_fee_and_notifications(ctx):
    staff = ctx.staff()
    if staff is None:
        return "skipped: no staff account given"
    api, _identity = customer(ctx)
    add_to_cart(api)
    options = api.get("/api/checkout/options/")
    city = options["delivery"]["cities"][0]["code"]
    district = next(d["code"] for d in options["delivery"]["districts"] if d["city"] == city)
    answer = api.post("/api/orders/checkout/", {
        "delivery_method": "delivery", "delivery": {"city": city, "district": district, "address": "UAT street 1"},
        "payment_option": "full", "payment_method": "card", "terms_accepted": True}, expect=201)
    number, payment = answer["order"]["order_number"], answer["payment"]
    api.post(f"/api/payments/fake/{payment['id']}/complete/", {"status": "paid"})
    staff.post(f"/api/demo/orders/{number}/courier-fee/", {"proposed_fee_gel": "15.00", "message": "Courier"})
    asked = api.get(f"/api/orders/{number}/")
    assert asked["courier"]["action_required"] is True and asked["attention"]["kind"] == "courier_fee"
    inbox = api.get("/api/notifications/?unread=1")
    assert inbox["unread_count"] >= 1 and inbox["results"][0]["event_type"] == "courier_fee_proposed"
    api.post(f"/api/notifications/{inbox['results'][0]['id']}/read/", expect=204)
    accepted = api.post(f"/api/orders/{number}/courier-fee/accept/")
    assert accepted["courier"]["action_required"] is False and accepted["courier"]["fee"] is not None
    api.post(f"/api/orders/{number}/courier-fee/accept/", expect=409, code="no_pending_action")


ALL = [guest_search_register_card_payment, login_logout_csrf_and_failures, otp_errors_in_every_language, password_reset,
       guest_cart_merges_at_login, invoice_checkout, failed_card_payment_then_retry, cancel_before_and_after_payment,
       search_outcomes_and_support, customers_cannot_see_each_other, profile_and_language, price_change_decision,
       manual_price_request, courier_fee_and_notifications]
