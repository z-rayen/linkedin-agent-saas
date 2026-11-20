"""Fetching URLs that come from outside the application (job pages, search results), safely.

Rules, applied to every hop: the first request and each redirect.
1. Scheme http or https only. No credentials in the URL. Default port only (80 or 443).
2. The host is resolved, and every address it resolves to must be public. Loopback, private, link-local,
   multicast, reserved, unspecified, shared (100.64/10), 6to4 and Teredo addresses are refused, and an
   IPv4-mapped IPv6 address is judged by the IPv4 address it embeds.
3. The request connects to the address that was validated, not to the name. The original host is sent as
   the Host header and as the TLS server name. A second DNS answer cannot redirect the connection (DNS rebinding).
4. At most MAX_REDIRECTS redirects, each one validated again by the rules above.
5. The response must be 200 with a text/html content type, and its body is read under a hard size cap.
6. Environment proxies are ignored: a proxy would hide the real destination from the checks above.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_REDIRECTS = 3
MAX_BYTES = 1_000_000
ACCEPTED_CONTENT_TYPES = frozenset({"text/html"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
OVERALL_DEADLINE_SECONDS = 20.0
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
USER_AGENT = "Mozilla/5.0 (compatible; ResearchAgent/1.0)"

Resolver = Callable[[str], Awaitable[list[str]]]


class FetchError(Exception):
    """The URL cannot be fetched safely, or the fetch failed. Callers treat it as 'no page'."""


async def system_resolver(host: str) -> list[str]:
    """Every address the system resolver returns for `host`, in order, without duplicates."""
    infos = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def is_public_address(text: str) -> bool:
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.sixtofour is not None or address.teredo is not None:
            return False  # these tunnel formats embed an IPv4 address that may be private
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


@dataclass(frozen=True)
class Hop:
    scheme: str
    host: str
    port: int
    path_and_query: str


def parse_hop(url: str) -> Hop:
    """Validate the URL's shape. Raises FetchError for anything outside the rules."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchError("only http and https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise FetchError("credentials in the URL are refused")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise FetchError("the URL has no host")
    try:
        explicit_port = parts.port
    except ValueError as exc:
        raise FetchError("invalid port") from exc
    if explicit_port is not None and explicit_port != DEFAULT_PORTS[scheme]:
        raise FetchError("only the default port of the scheme is allowed")
    path = parts.path or "/"
    path_and_query = f"{path}?{parts.query}" if parts.query else path
    return Hop(scheme=scheme, host=host, port=DEFAULT_PORTS[scheme], path_and_query=path_and_query)


async def validated_address(hop: Hop, resolver: Resolver) -> str:
    """The address to connect to. Every address the host resolves to must be public."""
    try:
        addresses = [str(ipaddress.ip_address(hop.host))]  # the host is an IP literal: no DNS involved
    except ValueError:
        addresses = await resolver(hop.host)
    if not addresses:
        raise FetchError("the host did not resolve")
    if not all(is_public_address(address) for address in addresses):
        raise FetchError("the host resolves to a non-public address")
    return addresses[0]


def _pinned_url(hop: Hop, address: str) -> str:
    host_part = f"[{address}]" if ":" in address else address
    return f"{hop.scheme}://{host_part}:{hop.port}{hop.path_and_query}"


def _check_content_type(response: httpx.Response) -> None:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in ACCEPTED_CONTENT_TYPES:
        raise FetchError(f"content type {content_type or 'missing'} is refused: only text/html is read")


async def _read_capped(response: httpx.Response, max_bytes: int) -> str:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError("the declared size is over the limit")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise FetchError("the response is over the size limit")
    try:
        encoding = response.charset_encoding or "utf-8"
        return bytes(body).decode(encoding, errors="replace")
    except LookupError:
        return bytes(body).decode("utf-8", errors="replace")


async def _fetch(url: str, resolver: Resolver, transport: httpx.AsyncBaseTransport | None, max_bytes: int) -> str:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        hop = parse_hop(current)
        address = await validated_address(hop, resolver)
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, timeout=TIMEOUT, trust_env=False
        ) as client:
            request = client.build_request(
                "GET",
                _pinned_url(hop, address),
                headers={"Host": hop.host, "User-Agent": USER_AGENT, "Accept": "text/html"},
                extensions={"sni_hostname": hop.host},
            )
            response = await client.send(request, stream=True)
            try:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("redirect without a location")
                    current = urljoin(current, location)
                    continue  # the next hop is validated from the start
                if response.status_code != 200:
                    raise FetchError(f"HTTP status {response.status_code}")
                _check_content_type(response)
                return await _read_capped(response, max_bytes)
            finally:
                await response.aclose()
    raise FetchError("too many redirects")


async def fetch_text(
    url: str,
    *,
    resolver: Resolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_bytes: int = MAX_BYTES,
) -> str:
    """The text of an HTML page, fetched under every rule in this module. Raises FetchError otherwise.

    `resolver` and `transport` exist for tests. Production uses the system resolver and the network.
    """
    try:
        return await asyncio.wait_for(
            _fetch(url, resolver or system_resolver, transport, max_bytes),
            timeout=OVERALL_DEADLINE_SECONDS,
        )
    except TimeoutError as exc:
        raise FetchError("the fetch exceeded its deadline") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise FetchError("network error while fetching the page") from exc
