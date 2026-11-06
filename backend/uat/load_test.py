#!/usr/bin/env python
"""Bounded load scenarios for the isolated staging ONLY: docs/uat-matrix.md section D5. The limits below cannot be raised by
an option, and the same target check as the UAT runner refuses anything without the demo routes.

    venv/bin/python -m uat.load_test --base-url https://STAGE_HOST --origin https://FRONTEND_ORIGIN --scenario cart

Expect 429 in the login and otp scenarios: proving the limits hold is their purpose. Watch alongside:
    SELECT count(*), state FROM pg_stat_activity WHERE datname = 'lp_stage_v1' GROUP BY state;"""

import argparse
import os
import statistics
import sys
import threading
import time
from collections import Counter

from uat import journeys
from uat.run_http import HttpContext, Target

MAX_WORKERS = 16
MAX_REQUESTS = 400


class Tally:
    def __init__(self):
        self.lock, self.codes, self.seconds = threading.Lock(), Counter(), []

    def timed(self, call):
        started = time.monotonic()
        try:
            status = call()
        except Exception as error:
            status = type(error).__name__
        with self.lock:
            self.codes[status] += 1
            self.seconds.append(time.monotonic() - started)

    def report(self, name):
        times = sorted(self.seconds) or [0]
        print(f"{name}: {dict(self.codes)} p50={statistics.median(times):.3f}s "
              f"p95={times[max(int(len(times) * 0.95) - 1, 0)]:.3f}s max={times[-1]:.3f}s requests={len(self.seconds)}")


def run_parallel(workers, requests_per_worker, work):
    tally = Tally()

    def loop(worker):
        for number in range(requests_per_worker):
            tally.timed(lambda: work(worker, number))

    threads = [threading.Thread(target=loop, args=(worker,)) for worker in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return tally


def raw(ctx):
    """A transport without assertions: the status is the result."""
    return ctx.target.transport(ctx.target.session())


def csrf_for(send):
    return send("GET", "/api/auth/csrf/", None, {})[1]["csrf_token"]


def scenario_login(ctx, workers, each):
    """Parallel failed logins from one address: the limit is 10 per minute per IP and 5 per email."""
    sessions = [raw(ctx) for _ in range(workers)]
    tokens = [csrf_for(send) for send in sessions]
    return run_parallel(workers, each, lambda w, n: sessions[w](
        "POST", "/api/auth/login/", {"email": f"load{w}@example.com", "password": "wrong"}, {"X-CSRFToken": tokens[w]})[0])


def scenario_otp(ctx, workers, each):
    """Parallel resends in one registration session: one SMS at most, the others 409 or 429."""
    api, identity = ctx.client(), journeys.new_identity()
    api.post("/api/auth/register/", {**identity, "preferred_language": "en"})
    return run_parallel(workers, each, lambda w, n: api.transport(
        "POST", "/api/auth/register/resend-code/", {}, {"X-CSRFToken": api.csrf})[0])


def scenario_cart(ctx, workers, each):
    """Parallel quantity changes on one guest cart, which serialize on the cart row."""
    api = ctx.client()
    line = journeys.add_to_cart(api)["items"][0]["id"]
    return run_parallel(workers, each, lambda w, n: api.transport(
        "PATCH", f"/api/cart/items/{line}/", {"quantity": (n % 50) + 1}, {"X-CSRFToken": api.csrf})[0])


def scenario_checkout(ctx, workers, each):
    """One verified customer: parallel checkouts of one cart, repeated. Each round must create exactly one order."""
    api, _identity = journeys.customer(ctx)
    pickup = api.get("/api/checkout/options/")["pickup_points"][0]["id"]
    body = {"delivery_method": "pickup", "pickup_point_id": pickup, "payment_option": "full", "payment_method": "invoice",
            "terms_accepted": True}
    total = Tally()
    for _round in range(each):
        journeys.add_to_cart(api)
        round_tally = run_parallel(workers, 1, lambda w, n: api.transport(
            "POST", "/api/orders/checkout/", body, {"X-CSRFToken": api.csrf})[0])
        if round_tally.codes[201] != 1:
            print(f"PROBLEM: a round created {round_tally.codes[201]} orders")
        total.codes.update(round_tally.codes)
        total.seconds += round_tally.seconds
    return total


def scenario_quote_approval(ctx, workers, each):
    """Parallel operator approvals of one request through the demo endpoint. Needs a staff account and a tariff allowed to
    request prices (set it in the admin). The price comes from the offer, so this takes the path that asks the supplier."""
    staff = ctx.staff()
    if staff is None:
        sys.exit("REFUSED: set UAT_STAFF_EMAIL and UAT_STAFF_PASSWORD")
    api, _identity = journeys.customer(ctx)
    found = api.post("/api/parts/search/", {"part_number": journeys.PART})
    made = api.post("/api/parts/quote-requests/", {"request_type": "weight_price", "search_id": found["search_id"],
                                                   "offer_id": found["offers"][0]["offer_id"]}, expect=201)
    if staff.csrf is None:
        staff.csrf = csrf_for(staff.transport)
    return run_parallel(workers, each, lambda w, n: staff.transport(
        "POST", f"/api/demo/quote-requests/{made['id']}/ready/", {"weight_kg": "2.00"}, {"X-CSRFToken": staff.csrf})[0])


def scenario_slow_supplier(ctx, workers, each):
    """With PARTS_PROVIDER=amt and AMT_ENDPOINT on uat/slow_supplier.py: lookups must end at the budget as 502, and the health
    probe must keep answering while they hang. The search limit (30 per minute per IP) bounds this scenario."""
    sessions = [raw(ctx) for _ in range(workers)]
    tokens = [csrf_for(send) for send in sessions]
    probe, stop = Tally(), threading.Event()

    def watch():
        send = raw(ctx)
        while not stop.is_set():
            probe.timed(lambda: send("GET", "/api/health/", None, {})[0])
            time.sleep(1)

    watcher = threading.Thread(target=watch)
    watcher.start()
    tally = run_parallel(workers, each, lambda w, n: sessions[w](
        "POST", "/api/parts/search/", {"part_number": f"SLOW-{w}-{n}"}, {"X-CSRFToken": tokens[w]})[0])
    stop.set()
    watcher.join()
    probe.report("health during the slow lookups")
    return tally


SCENARIOS = {"login": scenario_login, "otp": scenario_otp, "cart": scenario_cart, "checkout": scenario_checkout,
             "quote_approval": scenario_quote_approval, "slow_supplier": scenario_slow_supplier}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--each", type=int, default=5, help="requests (or rounds) per worker")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--admin-path", default="stage-admin/")
    parser.add_argument("--host-header")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if args.workers > MAX_WORKERS or args.workers * args.each > MAX_REQUESTS:
        sys.exit(f"REFUSED: at most {MAX_WORKERS} workers and {MAX_REQUESTS} requests")

    target = Target(args)
    target.assert_is_staging()
    ctx = HttpContext(target, args.mailpit.rstrip("/"), os.environ.get("UAT_STAFF_EMAIL"),
                      os.environ.get("UAT_STAFF_PASSWORD"), args.admin_path)
    SCENARIOS[args.scenario](ctx, args.workers, args.each).report(args.scenario)


if __name__ == "__main__":
    main()
