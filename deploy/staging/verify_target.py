#!/usr/bin/env python
"""What Django really uses on the isolated staging, read from the loaded settings and the live connection. Run by stage.sh
from backend/, with .env loaded, and exits non-zero on any difference:

    verify_target.py <database> <role> <host> <port>"""

import os
import sys
from email.utils import parseaddr

SETTINGS = {"DEPLOY_ENV": "staging", "DEBUG": False, "PAYMENT_PROVIDER": "fake", "AMT_VERIFY_SSL": True,
            "AMT_ENDPOINT": "https://automototrade.com/wsdl/server.php", "SENDER_GE_SEND_URL": "https://sender.ge/api/send.php"}
# (PARTS_PROVIDER, SENDER_GE_ENABLED) -> SMS_DEMO_MODE and ENABLE_DEMO_ENDPOINTS. Any other pair is refused: stage.sh load_target.
PROFILES = {("demo", False): True, ("demo", True): False, ("amt", True): False}
# The longest single AMT lookup gunicorn (timeout 60) should ever wait for: the settings defaults.
MAX_AMT_TIMEOUT, MAX_AMT_BUDGET = 15, 30
# Mail (LPK-235): Mailpit on loopback without a password, or the Brevo relay over STARTTLS from the verified domain.
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
MAILPIT = {"EMAIL_BACKEND": SMTP_BACKEND, "EMAIL_HOST": "127.0.0.1", "EMAIL_USE_SSL": False, "EMAIL_HOST_PASSWORD set": False}
BREVO = {**MAILPIT, "EMAIL_HOST": "smtp-relay.brevo.com", "EMAIL_PORT": 587, "EMAIL_USE_TLS": True, "EMAIL_HOST_USER set": True,
         "EMAIL_HOST_PASSWORD set": True, "sender domain": "lionparts.ge"}
# A send runs inside a request (gunicorn timeout 60); settings.py allows any positive value.
MAX_EMAIL_TIMEOUT = 30
# host() is NULL over a Unix socket, which is then refused: the approved target is the TCP address.
IDENTITY_SQL = ("SELECT current_database(), current_user, session_user, host(inet_server_addr()), inet_server_port(), "
                "current_setting('is_superuser')")


def settings_problems(settings, database, role, host, port):
    """-> {name: wrong value}. Credentials are reported only as set or not, never by value."""
    db = settings.DATABASES["default"]
    pair = (settings.PARTS_PROVIDER, settings.SENDER_GE_ENABLED)
    demo_tools = PROFILES.get(pair)
    expected = {**SETTINGS, "SMS_DEMO_MODE": demo_tools, "ENABLE_DEMO_ENDPOINTS": demo_tools,
                **dict.fromkeys(("AMT_API_LOGIN set", "AMT_API_PASSWORD set"), pair[0] == "amt"), "SENDER_GE_API_KEY set": pair[1] is True,
                "NAME": database, "USER": role, "HOST": host, "PORT": str(port)}
    actual = {**{name: getattr(settings, name, None) for name in (*SETTINGS, "SMS_DEMO_MODE", "ENABLE_DEMO_ENDPOINTS")},
              "AMT_API_LOGIN set": bool(settings.AMT_API_LOGIN), "AMT_API_PASSWORD set": bool(settings.AMT_API_PASSWORD),
              "SENDER_GE_API_KEY set": bool(settings.SENDER_GE_API_KEY),
              "NAME": db["NAME"], "USER": db["USER"], "HOST": db["HOST"], "PORT": str(db["PORT"])}
    wrong = {name: value for name, value in actual.items() if value != expected[name]}
    if demo_tools is None:
        wrong["PARTS_PROVIDER/SENDER_GE_ENABLED"] = pair
    timeout, budget = settings.AMT_TIMEOUT_SECONDS, settings.AMT_LOOKUP_BUDGET_SECONDS
    if not 0 < timeout <= MAX_AMT_TIMEOUT or not timeout <= budget <= MAX_AMT_BUDGET:
        wrong["AMT_TIMEOUT_SECONDS/AMT_LOOKUP_BUDGET_SECONDS"] = (timeout, budget)
    return wrong | mail_problems(settings)


def mail_problems(settings):
    """-> {name: wrong value} for the effective default mailer. The username and password only as set or not."""
    mailer = settings.MAILERS["default"]
    options = mailer.get("OPTIONS", {})
    actual = {"EMAIL_BACKEND": mailer["BACKEND"], "EMAIL_HOST": options.get("host"), "EMAIL_USE_SSL": bool(options.get("use_ssl")),
              "EMAIL_HOST_PASSWORD set": bool(options.get("password"))}
    expected = MAILPIT
    if actual["EMAIL_HOST"] == BREVO["EMAIL_HOST"]:
        expected = BREVO
        actual |= {"EMAIL_PORT": options.get("port"), "EMAIL_USE_TLS": options.get("use_tls"),
                   "EMAIL_HOST_USER set": bool(options.get("username")),
                   "sender domain": parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition("@")[2].lower()}
    wrong = {name: value for name, value in actual.items() if value != expected[name]}
    timeout = options.get("timeout") or 0
    if expected is BREVO and not 0 < timeout <= MAX_EMAIL_TIMEOUT:
        wrong["EMAIL_TIMEOUT"] = timeout
    return wrong


def connection_problems(row, database, role, host, port):
    expected = {"database": database, "current_user": role, "session_user": role, "host": host, "port": str(port),
                "superuser": "off"}
    actual = dict(zip(expected, (row[0], row[1], row[2], row[3], str(row[4]), row[5])))
    return {name: value for name, value in actual.items() if value != expected[name]}


def read_identity(connection):
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        return cursor.fetchone()


def main(database, role, host, port):
    sys.path.insert(0, os.getcwd())
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection
    wrong = settings_problems(settings, database, role, host, port)
    if wrong:
        sys.exit(f"REFUSED: effective settings differ: {wrong}")
    wrong = connection_problems(read_identity(connection), database, role, host, port)
    if wrong:
        sys.exit(f"REFUSED: the live connection differs: {wrong}")
    print(f"effective target: {database} as {role} on {host}:{port}")


if __name__ == "__main__":
    main(*sys.argv[1:])
