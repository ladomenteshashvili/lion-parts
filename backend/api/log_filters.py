"""Log filters (D23): public link tokens never reach the application logs."""

import logging
import re

TOKEN_PATH = re.compile(r"(/(?:api/public/)?[nq]/)[0-9a-fA-F-]{8,}")


def _mask(value):
    return TOKEN_PATH.sub(r"\1<token>", value) if isinstance(value, str) else value


class MaskTokensFilter(logging.Filter):
    """Django logs every 4xx/5xx request path ("Not Found: /api/public/n/<uuid>/"): mask the token."""

    def filter(self, record):
        record.msg = _mask(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_mask(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _mask(v) for k, v in record.args.items()}
        return True
