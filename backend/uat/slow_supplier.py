#!/usr/bin/env python
"""A stand-in for AMT on loopback, for the slow_supplier load scenario: no real supplier is involved.

    python uat/slow_supplier.py --delay 40   # then AMT_ENDPOINT=http://127.0.0.1:18099/ with any test credentials"""

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def handler(delay, trickle):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if trickle:
                self.send_response(200)
                self.send_header("Content-Type", "text/xml")
                self.end_headers()
                deadline = time.monotonic() + delay
                try:
                    while time.monotonic() < deadline:
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(1)
                except OSError:  # the client gave up, which is the point of the test
                    pass
                return
            time.sleep(delay)
            try:
                self.send_response(503)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *_args):
            pass

    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18099)
    parser.add_argument("--delay", type=float, default=40)
    parser.add_argument("--trickle", action="store_true", help="send one byte a second instead of waiting silently")
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(args.delay, args.trickle)).serve_forever()
