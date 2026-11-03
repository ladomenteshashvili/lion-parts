"""shortcut: LP-BE-201 removed CustomerSession; legacy session_id strings identify nobody now.
Kept only so the unrouted legacy views and parts/admin.py import; LP-BE-1201 deletes this module."""


def get_customer_for_session(session_id):
    return None
