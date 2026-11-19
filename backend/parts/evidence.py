"""Independent evidence checks for AI weight research (LPK-247).

The research service (an AI) only says where it saw a weight. Nothing it says is trusted: this module fetches each cited
page itself and checks that the page really shows the exact part number, the manufacturer and the claimed weight. A page
that cannot be fetched, shows no readable text (JavaScript-only), is blocked, or is ambiguous is *not verified* and
never counts towards a proposal.

Fetching an address an AI chose is a server-side request forgery risk, so every request:

- is https on port 443 to a host name (no IP literal, no user:password@);
- resolves the name once, refuses it if any address is private, loopback, link-local, reserved or otherwise not
  global, and connects to that very address (the name is used only for TLS and Host), so a DNS answer cannot change
  between the check and the connection (DNS rebinding);
- follows at most MAX_REDIRECTS redirects, each checked again in full;
- has short connect and read timeouts plus an overall deadline, reads at most MAX_PAGE_BYTES, accepts only HTML or plain
  text, and ignores proxy environment variables.

Source independence uses the Public Suffix List (data/public_suffix_list.dat, Mozilla, MPL 2.0): `shop.example.co.uk`
and `www.example.co.uk` are one site, `a.github.io` and `b.github.io` are two."""

import ipaddress
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import certifi
import idna
import urllib3

PSL_PATH = Path(__file__).with_name("data") / "public_suffix_list.dat"
MAX_REDIRECTS = 3
MAX_PAGE_BYTES = 2 * 1024 * 1024
CONNECT_SECONDS, READ_SECONDS = 5, 10
MIN_TEXT = 200  # fewer visible characters: a JavaScript-only, empty or challenge page
MAX_PAGE_WEIGHTS = 4  # more different weights on one page: a list of parts, not this part's page
USER_AGENT = "LionPartsWeightCheck/1.0"

# verification outcomes, stored on WeightResearchSource.verification
VERIFIED = "verified"
CONTRADICTED = "contradicted"  # the page shows a different weight than the service claimed
AMBIGUOUS = "ambiguous"
NOT_CONFIRMED = "not_confirmed"  # readable, but the part, manufacturer or a weight is not on it
UNREADABLE = "unreadable"
INACCESSIBLE = "inaccessible"
BLOCKED = "blocked"
REFUSED = "refused"  # unsafe or unusable address: never requested
NOT_CHECKED = "not_checked"

TO_KG = {"kg": Decimal("1"), "kgs": Decimal("1"), "kilo": Decimal("1"), "kilogram": Decimal("1"),
         "kilograms": Decimal("1"), "кг": Decimal("1"),
         "g": Decimal("0.001"), "gr": Decimal("0.001"), "gram": Decimal("0.001"), "grams": Decimal("0.001"),
         "г": Decimal("0.001"),
         "lb": Decimal("0.45359237"), "lbs": Decimal("0.45359237"), "pound": Decimal("0.45359237"),
         "pounds": Decimal("0.45359237"), "oz": Decimal("0.028349523125"), "ounce": Decimal("0.028349523125"),
         "ounces": Decimal("0.028349523125")}
_UNITS = "|".join(sorted(map(re.escape, TO_KG), key=len, reverse=True))
# "weight" in English, German, French, Spanish/Italian/Portuguese, Russian (2) and Georgian (escaped: G§5.8)
_WEIGHT = re.compile(r"(?:weight|wt\.|gewicht|poids|peso|вес|масса|\u10ec\u10dd\u10dc\u10d0)[^\d]{0,40}?(\d{1,6}(?:[.,]\d{1,4})?)\s?"
                     rf"({_UNITS})(?![^\W\d_])", re.I)
_LABELS = {"shipping": re.compile(r"ship|packag|parcel|gross|brutto|versand|доставк|брутто|упаковк", re.I),
           "net": re.compile(r"\bnet\b|netto|нетто", re.I)}


class Unverifiable(Exception):
    def __init__(self, status, note):
        super().__init__(note)
        self.status, self.note = status, note


# --- public suffix list ----------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _psl():
    rules, wildcards, exceptions = set(), set(), set()
    for line in PSL_PATH.read_text("utf-8").splitlines():
        rule = line.strip().split(" ")[0].lower()
        if not rule or rule.startswith("//"):
            continue
        if rule.startswith("!"):
            exceptions.add(rule[1:])
        elif rule.startswith("*."):
            wildcards.add(rule[2:])
        else:
            rules.add(rule)
    return rules, wildcards, exceptions


def registrable_domain(host):
    """-> the registrable domain (public suffix plus one label) of a host name, or "" for a bare public suffix, an IP
    address or an invalid name. The Public Suffix List algorithm: exception rules first, then the longest match, then
    the implicit "*" rule."""
    host = host.strip().rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        return ""
    except ValueError:
        pass
    try:
        labels = idna.decode(host, uts46=True).split(".")
    except (idna.IDNAError, UnicodeError):
        return ""
    if not all(labels):
        return ""
    rules, wildcards, exceptions = _psl()
    suffix = 1
    for i in range(len(labels)):
        candidate = ".".join(labels[i:])
        if candidate in exceptions:
            suffix = len(labels) - i - 1
            break
        if candidate in rules or (i + 1 < len(labels) and ".".join(labels[i + 1:]) in wildcards):
            suffix = len(labels) - i
            break
    return ".".join(labels[-suffix - 1:]) if len(labels) > suffix else ""


def site(url):
    """-> the registrable domain of an https URL, or ""."""
    parts = urlsplit(url)
    return registrable_domain(parts.hostname or "") if parts.scheme == "https" else ""


# --- units ---------------------------------------------------------------------------------------------------------

def to_kg(value, unit):
    """-> kg (3 decimals) or None. Accepts '1.2', '1,2' and '1 200' style numbers; units from TO_KG only."""
    factor = TO_KG.get(str(unit).strip().lower().rstrip("."))
    text = str(value).strip().replace(" ", "").replace(" ", "")
    if factor is None or not text:
        return None
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return (number * factor).quantize(Decimal("0.001"))


def close(a, b):
    """Two readings of the same weight (rounding, unit conversion): within 3 % or 10 g."""
    return abs(a - b) <= max(Decimal("0.010"), max(a, b) * Decimal("0.03"))


# --- safe fetching --------------------------------------------------------------------------------------------------

def _target(url):
    """-> (ASCII host, path with query). Raises Unverifiable(REFUSED) for anything but https://name[:443]/..."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise Unverifiable(REFUSED, "not a valid URL") from None
    if parts.scheme != "https":
        raise Unverifiable(REFUSED, "not an https URL")
    if parts.username is not None or parts.password is not None:
        raise Unverifiable(REFUSED, "the URL carries a user name or password")
    if port not in (None, 443):
        raise Unverifiable(REFUSED, "a port other than 443")
    host = (parts.hostname or "").rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise Unverifiable(REFUSED, "an IP address instead of a site name")
    try:
        host = idna.encode(host, uts46=True).decode("ascii")
    except idna.IDNAError:
        raise Unverifiable(REFUSED, "not a valid host name") from None
    if "." not in host:
        raise Unverifiable(REFUSED, "not a public host name")
    return host, (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def public_address(host):
    """Resolve once; refuse when ANY address is not globally routable. -> the address to connect to."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise Unverifiable(INACCESSIBLE, "the site name does not resolve") from None
    addresses = []
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if address.version == 6:  # an IPv4 address carried inside IPv6 is checked as that IPv4 address too
            embedded = address.ipv4_mapped or address.sixtofour or (address.teredo or (None, None))[1]
            if embedded and (not embedded.is_global or embedded.is_multicast):
                raise Unverifiable(REFUSED, "the site resolves to a private or reserved address")
        if not address.is_global or address.is_multicast:
            raise Unverifiable(REFUSED, "the site resolves to a private or reserved address")
        addresses.append(address)
    if not addresses:
        raise Unverifiable(INACCESSIBLE, "the site name does not resolve")
    return str(min(addresses, key=lambda a: a.version))  # IPv4 first


def _request(host, address, path, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise Unverifiable(NOT_CHECKED, "time limit for checking pages reached")
    pool = urllib3.HTTPSConnectionPool(address, port=443, server_hostname=host, assert_hostname=host,
                                       cert_reqs="CERT_REQUIRED", ca_certs=certifi.where(), maxsize=1, retries=False,
                                       timeout=urllib3.Timeout(connect=min(CONNECT_SECONDS, remaining),
                                                               read=min(READ_SECONDS, remaining)))
    try:
        return pool, pool.urlopen("GET", path, headers={"Host": host, "User-Agent": USER_AGENT,
                                                        "Accept": "text/html,text/plain;q=0.9"},
                                  redirect=False, retries=False, preload_content=False, decode_content=True)
    except urllib3.exceptions.HTTPError as error:
        pool.close()
        raise Unverifiable(INACCESSIBLE, f"the page could not be loaded ({type(error).__name__})") from None


def _read(response, deadline):
    body = bytearray()
    try:
        for chunk in response.stream(64 * 1024, decode_content=True):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                raise Unverifiable(INACCESSIBLE, "the page is too large")
            if time.monotonic() > deadline:
                raise Unverifiable(NOT_CHECKED, "time limit for checking pages reached")
    except urllib3.exceptions.HTTPError as error:
        raise Unverifiable(INACCESSIBLE, f"the page could not be read ({type(error).__name__})") from None
    return bytes(body)


def fetch_page(url, deadline):
    """-> (final URL, visible text). Raises Unverifiable. `deadline` is a time.monotonic() value."""
    for _ in range(MAX_REDIRECTS + 1):
        host, path = _target(url)
        pool, response = _request(host, public_address(host), path, deadline)
        try:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise Unverifiable(INACCESSIBLE, "a redirect without a destination")
                url = urljoin(url, location)
                continue
            if response.status in (401, 403, 429, 451):
                raise Unverifiable(BLOCKED, f"the site refused the request (HTTP {response.status})")
            if response.status in (404, 410):
                raise Unverifiable(INACCESSIBLE, f"the page does not exist (HTTP {response.status})")
            if response.status != 200:
                raise Unverifiable(INACCESSIBLE, f"the page could not be loaded (HTTP {response.status})")
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type.split(";")[0].strip() not in ("text/html", "application/xhtml+xml", "text/plain"):
                raise Unverifiable(UNREADABLE, "not an HTML or text page")
            body = _read(response, deadline)
        finally:
            response.release_conn()
            pool.close()
        charset = re.search(r"charset=([\w-]+)", content_type)
        try:
            text = body.decode(charset.group(1) if charset else "utf-8", "replace")
        except LookupError:
            text = body.decode("utf-8", "replace")
        return url, page_text(text) if "html" in content_type else " ".join(text.split())
    raise Unverifiable(INACCESSIBLE, "too many redirects")


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        self.skipping += tag in self.SKIP

    def handle_endtag(self, tag):
        self.skipping -= tag in self.SKIP and self.skipping > 0

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def page_text(html):
    """The visible text of a page, whitespace collapsed. Scripts (and so JavaScript-rendered content) are ignored."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


# --- what the page says -----------------------------------------------------------------------------------------------

def _loose(key):
    """A regex for a normalized key ('90915YZZE1') as printed: any case, optional space, dash or dot between characters,
    not part of a longer code."""
    return re.compile(r"(?<![0-9A-Za-z])" + r"[\s.\-]?".join(map(re.escape, key)) + r"(?![0-9A-Za-z])", re.I)


def page_weights(text):
    """-> [(kg, label text before the number, snippet)] for every "weight ... <number> <unit>" on the page."""
    found, previous = [], 0
    for match in _WEIGHT.finditer(text):
        kg = to_kg(match.group(1), match.group(2))
        if kg is not None:  # the label: up to 30 characters before "weight", never reaching into the previous weight
            found.append((kg, text[max(previous, match.start() - 30):match.start(1)],
                          text[max(0, match.start() - 80):match.end() + 80]))
        previous = match.end()
    return found


def check_page(text, part_number_key, brand_key, claimed_kg, claimed_measurement):
    """-> (outcome, the page's matching weight in kg or None, the measurement the page labels it with or "", note,
    snippet). Conservative: anything the page does not show plainly is not verified."""
    if len(text) < MIN_TEXT:
        return UNREADABLE, None, "", "no readable text on the page (JavaScript-only, empty or a challenge page)", ""
    if not _loose(part_number_key).search(text):
        return NOT_CONFIRMED, None, "", "the page does not show this part number", ""
    if not _loose(brand_key).search(text):
        return NOT_CONFIRMED, None, "", "the page does not name the manufacturer", ""
    weights = page_weights(text)
    if not weights:
        return NOT_CONFIRMED, None, "", "the page states no weight", ""
    distinct = []
    for kg, _label, _snippet in weights:
        if not any(close(kg, seen) for seen in distinct):
            distinct.append(kg)
    shown = ", ".join(f"{kg} kg" for kg in distinct[:MAX_PAGE_WEIGHTS + 1])
    if len(distinct) > MAX_PAGE_WEIGHTS:
        return AMBIGUOUS, None, "", f"the page lists {len(distinct)} different weights (a list of parts?): {shown}", ""
    matching = [(kg, label, snippet) for kg, label, snippet in weights if close(kg, claimed_kg)]
    if not matching:
        return CONTRADICTED, None, "", f"the page states {shown}, not the claimed {claimed_kg} kg", weights[0][2]
    labels = {name for name, pattern in _LABELS.items() for _kg, label, _s in matching if pattern.search(label)}
    measurement = labels.pop() if len(labels) == 1 else ""
    kg, snippet = matching[0][0], matching[0][2]
    if len(distinct) == 1:
        return VERIFIED, kg, measurement, "the page shows the part number, manufacturer and weight", snippet
    if measurement and measurement == claimed_measurement:
        return VERIFIED, kg, measurement, f"the page shows several weights ({shown}); this one is labelled " \
                                      f"{measurement} weight", snippet
    return AMBIGUOUS, None, "", f"the page shows several weights ({shown}) without labelling the claimed one as " \
                          f"{claimed_measurement or 'net or shipping'} weight", snippet
