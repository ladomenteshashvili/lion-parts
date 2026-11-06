#!/usr/bin/env python
"""What Django really uses on the isolated staging, read from the loaded settings and the live connection. Run by stage.sh
from backend/, with .env loaded, and exits non-zero on any difference:

    verify_target.py <database> <role> <host> <port>"""

import os
import sys

SETTINGS = {"DEPLOY_ENV": "staging", "DEBUG": False, "PARTS_PROVIDER": "demo", "PAYMENT_PROVIDER": "fake",
            "SENDER_GE_ENABLED": False, "SMS_DEMO_MODE": True}
# host() is NULL over a Unix socket, which is then refused: the approved target is the TCP address.
IDENTITY_SQL = ("SELECT current_database(), current_user, session_user, host(inet_server_addr()), inet_server_port(), "
                "current_setting('is_superuser')")


def settings_problems(settings, database, role, host, port):
    db = settings.DATABASES["default"]
    expected = {**SETTINGS, "NAME": database, "USER": role, "HOST": host, "PORT": str(port)}
    actual = {**{name: getattr(settings, name, None) for name in SETTINGS},
              "NAME": db["NAME"], "USER": db["USER"], "HOST": db["HOST"], "PORT": str(db["PORT"])}
    return {name: value for name, value in actual.items() if value != expected[name]}


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
