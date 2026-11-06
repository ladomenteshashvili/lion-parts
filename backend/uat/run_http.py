#!/usr/bin/env python
"""Run the UAT journeys over real HTTP against the isolated staging: docs/staging-runbook.md section 6.

    UAT_STAFF_EMAIL=... UAT_STAFF_PASSWORD=... venv/bin/python -m uat.run_http \\
        --base-url https://STAGE_HOST --origin https://FRONTEND_ORIGIN --mailpit http://127.0.0.1:8025

Creates test customers and orders with fake payments, so it refuses any target that does not show the demo routes."""

import argparse
import json
import os
import sys
import time
from urllib.parse import urlsplit

import requests

from uat import journeys

PROTECTED_HOSTS = ("lionparts.ge", "www.lionparts.ge", "api.lionparts.ge", "api-test.lionparts.ge")


def refuse(message):
    sys.exit(f"REFUSED: {message}")


class Target:
    def __init__(self, args):
        self.base, self.origin, self.host = args.base_url.rstrip("/"), args.origin, args.host_header
        self.verify, self.timeout = not args.insecure, args.timeout

    def session(self):
        session = requests.Session()
        session.verify = self.verify
        session.headers.update({"Origin": self.origin})
        if self.host:
            session.headers["Host"] = self.host
        return session

    def transport(self, session):
        def send(method, path, body, headers):
            response = session.request(method, self.base + path, json=body, headers=headers, timeout=self.timeout,
                                       allow_redirects=False)
            kind = response.headers.get("Content-Type", "")
            return response.status_code, response.json() if "json" in kind else response.text, response.headers
        return send

    def assert_is_staging(self):
        """Production has no fake-payment route at all, so its answer here would be 404."""
        host = urlsplit(self.base).hostname
        if host in PROTECTED_HOSTS:
            refuse(f"{host} is a protected host")
        session = self.session()
        health = session.get(f"{self.base}/api/health/", timeout=self.timeout, allow_redirects=False)
        if health.status_code != 200:
            refuse(f"health answered {health.status_code}")
        route = session.post(f"{self.base}/api/payments/fake/0/complete/", json={}, timeout=self.timeout,
                             allow_redirects=False)
        if route.status_code not in (401, 403):
            refuse("the target does not show the demo routes, so it is not the isolated staging")


class HttpContext(journeys.Context):
    def __init__(self, target, mailpit, staff_email, staff_password, admin_path):
        super().__init__()
        self.target, self.mailpit, self.staff_email, self.staff_password = target, mailpit, staff_email, staff_password
        self.admin_path, self.staff_api = admin_path, None

    def client(self, language="en"):
        return journeys.Api(self.target.transport(self.target.session()), language, self.record)

    def staff(self):
        if not (self.staff_email and self.staff_password):
            return None
        if self.staff_api is None:
            session = self.target.session()
            login = f"{self.target.base}/{self.admin_path}login/"
            session.get(login, timeout=self.target.timeout)
            answer = session.post(login, data={"username": self.staff_email, "password": self.staff_password,
                                               "csrfmiddlewaretoken": session.cookies.get("csrftoken"), "next": "/"},
                                  headers={"Referer": login}, allow_redirects=False, timeout=self.target.timeout)
            if answer.status_code != 302:
                refuse(f"the staff login answered {answer.status_code}")
            self.staff_api = journeys.Api(self.target.transport(session), "en", self.record)
        return self.staff_api

    def email_link(self, address, marker):
        """Mailpit's API. Emails are sent during the request, so the message is normally there at once."""
        for _attempt in range(10):
            messages = requests.get(f"{self.mailpit}/api/v1/messages?limit=100", timeout=10).json()["messages"]
            for summary in messages:
                if any(to["Address"] == address for to in summary["To"]):
                    text = requests.get(f"{self.mailpit}/api/v1/message/{summary['ID']}", timeout=10).json()["Text"]
                    if marker in text:
                        return next(word for word in text.split() if marker in word)
            time.sleep(1)
        raise AssertionError(f"no email with {marker} for {address} in Mailpit")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--origin", required=True, help="a frontend origin listed in CSRF_TRUSTED_ORIGINS")
    parser.add_argument("--mailpit", default="http://127.0.0.1:8025")
    parser.add_argument("--admin-path", default="stage-admin/")
    parser.add_argument("--host-header", help="for an address the DNS does not know yet")
    parser.add_argument("--insecure", action="store_true", help="accept a self-signed certificate")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--only", help="comma-separated journey names")
    parser.add_argument("--transcript", help="write every request and answer to this JSON file (holds test tokens)")
    args = parser.parse_args()

    target = Target(args)
    target.assert_is_staging()
    ctx = HttpContext(target, args.mailpit.rstrip("/"), os.environ.get("UAT_STAFF_EMAIL"),
                      os.environ.get("UAT_STAFF_PASSWORD"), args.admin_path)
    chosen = set(args.only.split(",")) if args.only else None
    failures, transcript = 0, []
    for journey in journeys.ALL:
        if chosen and journey.__name__ not in chosen:
            continue
        ctx.record.clear()
        started = time.monotonic()
        try:
            note = journey(ctx)
            result = "PASS" + (f" ({note})" if note else "")
        except (AssertionError, requests.RequestException) as error:
            failures += 1
            result = f"FAIL {error}"
        print(f"{journey.__name__:45} {time.monotonic() - started:6.1f}s  {result}", flush=True)
        transcript.append({"journey": journey.__name__, "doc": journey.__doc__, "steps": list(ctx.record)})
    if args.transcript:
        fd = os.open(args.transcript, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as out:
            json.dump(transcript, out, ensure_ascii=False, indent=1)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
