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
  text, and ignores proxy environment variables;
- is allowed by the site's robots.txt (RFC 9309) for USER_AGENT, checked before every request, redirects included. A
  robots.txt that refuses access (401, 403, 429, other 4xx except 404/410) or cannot be read safely (5xx, timeout,
  too large, unsafe address) means the page is not fetched and stays unverified.

A weight counts only when it belongs to the part: the part number, the manufacturer and the weight must sit in the same
product record (a list item, table row, article or schema.org Product of the page's HTML), the same specification
block, or the section of a single-product page whose one <h1> names the part. Their mere presence somewhere on a
category or listing page is not enough.

Source independence uses the Public Suffix List (data/public_suffix_list.dat, Mozilla, MPL 2.0): `shop.example.co.uk`
and `www.example.co.uk` are one site, `a.github.io` and `b.github.io` are two."""

import ipaddress
import re
import socket
import time
from decimal import Decimal, InvalidOperation
from bisect import bisect_right
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

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
ROBOTS_AGENT = "LionPartsWeightCheck"  # the product token robots.txt groups are matched against
MAX_ROBOTS_BYTES = 512 * 1024
SPEC_ITEM_CHARS = 60  # list items this short, each "label: value", are specification lines, not product cards
# a part number labelled as such ("Part number: ...", "OEM ...", "Артикул ...") in a specification block
_NUMBER_LABEL = re.compile(r"(part|item|article|art\.?|oem|mpn|sku|ref\.?|reference|teile|référence|артикул|номер|код|"
                           r"№)\s*(no\.?|nr\.?|number|nummer|#)?\s*[:#]?\s*$", re.I)

# verification outcomes, stored on WeightResearchSource.verification
VERIFIED = "verified"
CONTRADICTED = "contradicted"  # the page shows a different weight than the service claimed
AMBIGUOUS = "ambiguous"
NOT_CONFIRMED = "not_confirmed"  # readable, but the part, manufacturer or a weight is not on it
UNREADABLE = "unreadable"
INACCESSIBLE = "inaccessible"
BLOCKED = "blocked"
REFUSED = "refused"  # unsafe or unusable address: never requested
DISALLOWED = "disallowed"  # robots.txt does not allow it, or refuses access to itself: never requested
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
_WEIGHT = re.compile(r"(?:weight|wt\.|gewicht|poids|peso|вес|масса|\u10ec\u10dd\u10dc\u10d0)[^\d]{0,40}?"
                     r"(\d{1,6}(?:[.,]\d{1,4})?)\s?"
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


def _read(response, deadline, limit):
    body = bytearray()
    try:
        for chunk in response.stream(64 * 1024, decode_content=True):
            body += chunk
            if len(body) > limit:
                raise Unverifiable(INACCESSIBLE, "the page is too large")
            if time.monotonic() > deadline:
                raise Unverifiable(NOT_CHECKED, "time limit for checking pages reached")
    except urllib3.exceptions.HTTPError as error:
        raise Unverifiable(INACCESSIBLE, f"the page could not be read ({type(error).__name__})") from None
    return bytes(body)


def _get(url, deadline, limit, types, allowed=None):
    """One safe GET following at most MAX_REDIRECTS redirects, each hop checked in full (and against robots.txt when
    `allowed` is given). -> (final URL, status, content type, body; the body only for a 200 of an accepted type)."""
    for _ in range(MAX_REDIRECTS + 1):
        host, path = _target(url)
        if allowed:
            allowed(url, host)
        pool, response = _request(host, public_address(host), path, deadline)
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise Unverifiable(INACCESSIBLE, "a redirect without a destination")
                url = urljoin(url, location)
                continue
            accepted = response.status == 200 and content_type.split(";")[0].strip() in types
            body = _read(response, deadline, limit) if accepted else None
        finally:
            response.release_conn()
            pool.close()
        return url, response.status, content_type, body
    raise Unverifiable(INACCESSIBLE, "too many redirects")


def _decode(body, content_type):
    charset = re.search(r"charset=([\w-]+)", content_type)
    try:
        return body.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def _robots(host, deadline):
    """-> a parsed robots.txt for https://host, or raises Unverifiable (DISALLOWED or INACCESSIBLE): access that cannot
    be established safely leaves the page unverified. A missing robots.txt (404, 410) allows everything (RFC 9309)."""
    try:
        _url, status, content_type, body = _get(f"https://{host}/robots.txt", deadline, MAX_ROBOTS_BYTES,
                                                ("text/plain", "text/html"))
    except Unverifiable as error:
        if error.status in (NOT_CHECKED, REFUSED):  # out of time, or the host itself is unsafe: said as it is
            raise
        raise Unverifiable(INACCESSIBLE, f"robots.txt could not be checked: {error.note}") from None
    parser = RobotFileParser()
    if status in (404, 410):
        parser.parse([])
    elif status == 200 and body is not None:
        parser.parse(_decode(body, content_type).splitlines())
    elif 400 <= status < 500:
        raise Unverifiable(DISALLOWED, f"the site refuses access to its robots.txt (HTTP {status}); not fetched")
    else:
        raise Unverifiable(INACCESSIBLE, f"robots.txt could not be checked (HTTP {status}); not fetched")
    return parser


def fetch_page(url, deadline, robots=None):
    """-> (final URL, Page). Raises Unverifiable. `deadline` is a time.monotonic() value; `robots` caches each host's
    robots.txt for one job (a dict)."""
    robots = {} if robots is None else robots

    def allowed(hop, host):
        if host not in robots:
            try:
                robots[host] = _robots(host, deadline)
            except Unverifiable as error:
                robots[host] = error
        if isinstance(robots[host], Unverifiable):
            raise robots[host]
        if not robots[host].can_fetch(ROBOTS_AGENT, hop):
            raise Unverifiable(DISALLOWED, "robots.txt does not allow checking this page; not fetched")

    url, status, content_type, body = _get(url, deadline, MAX_PAGE_BYTES,
                                           ("text/html", "application/xhtml+xml", "text/plain"), allowed)
    if status in (401, 403, 429, 451):
        raise Unverifiable(BLOCKED, f"the site refused the request (HTTP {status})")
    if status in (404, 410):
        raise Unverifiable(INACCESSIBLE, f"the page does not exist (HTTP {status})")
    if status != 200:
        raise Unverifiable(INACCESSIBLE, f"the page could not be loaded (HTTP {status})")
    if body is None:
        raise Unverifiable(UNREADABLE, "not an HTML or text page")
    text = _decode(body, content_type)
    return url, parse_page(text) if "html" in content_type else Page(" ".join(text.split()))


# --- the page's structure ---------------------------------------------------------------------------------------------

class Node:
    __slots__ = ("tag", "attrs", "parent", "children", "start", "end", "spec", "colon")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, attrs, parent, []
        self.start = self.end = None
        self.spec = False  # not computed yet (see _spec_block)
        self.colon = False  # its text has a "label: value" colon
        if parent is not None:
            parent.children.append(self)

    def holds(self, offset):
        return self.start is not None and self.start <= offset < self.end

    def ancestors(self):
        node = self
        while node is not None:
            yield node
            node = node.parent


class Page:
    """A page's visible text (whitespace collapsed) and, for HTML, which element each piece of text sits in."""

    def __init__(self, text, chunks=(), headings=()):
        self.text, self.chunks, self.headings = text, list(chunks), list(headings)
        self.starts = [start for start, _node in self.chunks]

    @property
    def structured(self):
        return bool(self.chunks)

    def node_at(self, offset):
        return self.chunks[max(bisect_right(self.starts, offset) - 1, 0)][1]


class _Builder(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
            "wbr"}
    # an opening tag closes an open element of these tags, unless one of the boundary tags comes first
    IMPLIED = {"li": ({"li"}, {"ul", "ol", "menu"}), "tr": ({"tr", "td", "th"}, {"table", "tbody", "thead", "tfoot"}),
               "td": ({"td", "th"}, {"tr", "table"}), "th": ({"td", "th"}, {"tr", "table"}),
               "dt": ({"dt", "dd"}, {"dl"}), "dd": ({"dt", "dd"}, {"dl"}), "p": ({"p"}, {"div", "li", "td", "th"})}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skipping, self.parts, self.length, self.chunks, self.headings = (
            [Node("#root", {}, None)], 0, [], 0, [], [])

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        if self.skipping or tag in self.VOID:
            return
        closes, boundary = self.IMPLIED.get(tag, ((), ()))
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag in boundary:
                break
            if self.stack[i].tag in closes:
                del self.stack[i:]
                break
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.stack[-1])
        self.stack.append(node)
        if tag == "h1":
            self.headings.append(node)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(self.skipping - 1, 0)
            return
        if self.skipping:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        text = " ".join(data.split())
        if self.skipping or not text:
            return
        start = self.length + (1 if self.parts else 0)
        self.parts.append(text)
        self.length = start + len(text)
        self.chunks.append((start, self.stack[-1]))
        for node in self.stack:
            node.start = start if node.start is None else node.start
            node.end = self.length
            node.colon = node.colon or ":" in text


def parse_page(html):
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return Page(" ".join(builder.parts), builder.chunks, builder.headings)


def page_text(html):
    """The visible text of a page, whitespace collapsed. Scripts (and so JavaScript-rendered content) are ignored."""
    return parse_page(html).text


def _container(node, tags):
    return next((n for n in node.ancestors() if n.tag in tags), None)


def _spec_block(node):
    """A key/value specification block (a two-column table, a definition list, a list of short lines), else None."""
    if node.spec is False:
        node.spec = _find_spec_block(node)
    return node.spec


def _find_spec_block(node):
    if node.tag == "tr":
        table = _container(node.parent, {"table"}) if node.parent else None
        rows = [n for n in _descendants(table) if n.tag == "tr"] if table else []
        if rows and all(sum(c.tag in ("td", "th") for c in row.children) <= 2 for row in rows):
            return table
    if node.tag in ("dt", "dd"):
        return _container(node, {"dl"})
    if node.tag == "li" and node.parent is not None and node.parent.tag in ("ul", "ol"):
        items = [c for c in node.parent.children if c.tag == "li" and c.start is not None]
        if items and all(c.end - c.start <= SPEC_ITEM_CHARS and c.colon for c in items):
            return node.parent
    return None


def _descendants(node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def _is_record(node):
    """A product record: an article, a schema.org Product, or a list item / table row that is not a spec line."""
    if node.tag == "article" or "product" in node.attrs.get("itemtype", "").lower():
        return True
    return node.tag in ("li", "tr") and _spec_block(node) is None


def _record(page, offset):
    """The innermost product record holding this offset, or None (the page itself)."""
    return next((n for n in page.node_at(offset).ancestors() if _is_record(n)), None)


def associate(page, weight_at, numbers_at, brands_at):
    """Does the weight at `weight_at` belong to the part? -> (True, how) or (False, why not)."""
    if not page.structured:
        return False, "a plain-text page: the weight cannot be tied to the part"
    row = next((n for n in page.node_at(weight_at).ancestors() if n.tag in ("tr", "li")), None)
    if row is not None and any(row.holds(o) for o in numbers_at) and any(row.holds(o) for o in brands_at):
        return True, "in the same row as the part number and manufacturer"
    record = _record(page, weight_at)
    if record is not None:
        if any(record.holds(o) for o in numbers_at) and any(record.holds(o) for o in brands_at):
            return True, "in the same product record"
        return False, "the weight belongs to a product record that does not show this part number and manufacturer"
    # outside any product record: the page's own section
    own = [o for o in brands_at if _record(page, o) is None]
    if not own:
        return False, "the manufacturer is named only inside other product records"
    block = next((b for n in page.node_at(weight_at).ancestors() if (b := _spec_block(n)) is not None), None)
    if block is not None and any(block.holds(o) and _record(page, o) is None
                                 and _NUMBER_LABEL.search(page.text[max(0, o - 30):o]) for o in numbers_at):
        return True, "in the same specification block as the labelled part number"
    if len(page.headings) == 1 and any(page.headings[0].holds(o) for o in numbers_at):
        return True, "on a single-product page whose heading names the part"
    return False, "the weight is not tied to this part (no shared product record or section: a list or category page?)"


# --- what the page says -----------------------------------------------------------------------------------------------

def _loose(key):
    """A regex for a normalized key ('90915YZZE1') as printed: any case, optional space, dash or dot between characters,
    not part of a longer code."""
    return re.compile(r"(?<![0-9A-Za-z])" + r"[\s.\-]?".join(map(re.escape, key)) + r"(?![0-9A-Za-z])", re.I)


def page_weights(text):
    """-> [(kg, label text before the number, snippet, offset of the number)] for every "weight ... <number> <unit>"."""
    found, previous = [], 0
    for match in _WEIGHT.finditer(text):
        kg = to_kg(match.group(1), match.group(2))
        if kg is not None:  # the label: up to 30 characters before "weight", never reaching into the previous weight
            found.append((kg, text[max(previous, match.start() - 30):match.start(1)],
                          text[max(0, match.start() - 80):match.end() + 80], match.start(1)))
        previous = match.end()
    return found


def check_page(page, part_number_key, brand_key, claimed_kg, claimed_measurement):
    """-> (outcome, the page's matching weight in kg or None, the measurement the page labels it with or "", note,
    snippet). Conservative: anything the page does not show plainly, in one product record or section, is not
    verified. `page` is a Page (or plain text, which can never be tied to a product record)."""
    page = Page(page) if isinstance(page, str) else page
    text = page.text
    if len(text) < MIN_TEXT:
        return UNREADABLE, None, "", "no readable text on the page (JavaScript-only, empty or a challenge page)", ""
    numbers_at = [m.start() for m in _loose(part_number_key).finditer(text)]
    if not numbers_at:
        return NOT_CONFIRMED, None, "", "the page does not show this part number", ""
    brands_at = [m.start() for m in _loose(brand_key).finditer(text)]
    if not brands_at:
        return NOT_CONFIRMED, None, "", "the page does not name the manufacturer", ""
    weights = page_weights(text)
    if not weights:
        return NOT_CONFIRMED, None, "", "the page states no weight", ""
    distinct = []
    for kg, *_rest in weights:
        if not any(close(kg, seen) for seen in distinct):
            distinct.append(kg)
    shown = ", ".join(f"{kg} kg" for kg in distinct[:MAX_PAGE_WEIGHTS + 1])
    if len(distinct) > MAX_PAGE_WEIGHTS:
        return AMBIGUOUS, None, "", f"the page lists {len(distinct)} different weights (a list of parts?): {shown}", ""
    matching = [w for w in weights if close(w[0], claimed_kg)]
    if not matching:
        return CONTRADICTED, None, "", f"the page states {shown}, not the claimed {claimed_kg} kg", weights[0][2]
    tied = [(w, associate(page, w[3], numbers_at, brands_at)) for w in matching]
    ours = [(w, how) for w, (ok, how) in tied if ok]
    if not ours:
        return AMBIGUOUS, None, "", f"the claimed weight is on the page, but {tied[0][1][1]}", matching[0][2]
    labels = {name for name, pattern in _LABELS.items() for (_kg, label, _s, _o), _h in ours if pattern.search(label)}
    measurement = labels.pop() if len(labels) == 1 else ""
    (kg, _label, snippet, _offset), how = ours[0]
    if len(distinct) == 1:
        return VERIFIED, kg, measurement, f"the page shows the part number, manufacturer and weight {how}", snippet
    if measurement and measurement == claimed_measurement:
        return VERIFIED, kg, measurement, f"the page shows several weights ({shown}); this one is labelled " \
                                          f"{measurement} weight, {how}", snippet
    return AMBIGUOUS, None, "", f"the page shows several weights ({shown}) without labelling the claimed one as " \
                                f"{claimed_measurement or 'net or shipping'} weight", snippet
