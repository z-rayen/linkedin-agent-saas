"""SSRF-safe fetching. Every test uses a mocked resolver and a mocked transport: no DNS, no socket.

The mocked transport records every request it receives, so the tests can assert where the
connection would really have gone (the pinned address, the Host header, the TLS server name).
"""

import asyncio
from collections.abc import Callable, Iterator

import httpx
import pytest

from linkedin_agent.infra import safe_http
from linkedin_agent.infra.safe_http import FetchError, fetch_text, is_public_address, parse_hop

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


class Network:
    """A scripted network: DNS answers per name, and HTTP responses per request."""

    def __init__(
        self,
        dns: dict[str, list[str] | Iterator[list[str]]],
        respond: Callable[[httpx.Request], httpx.Response],
    ) -> None:
        # A list is the same answer every time. An iterator gives one answer per lookup, in order (DNS rebinding tests).
        self.dns_answers = dns
        self.dns_calls: list[str] = []
        self.requests: list[httpx.Request] = []
        self._respond = respond

    async def resolver(self, host: str) -> list[str]:
        self.dns_calls.append(host)
        answer = self.dns_answers[host]
        if isinstance(answer, list):
            return list(answer)
        return list(next(answer))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def html(
    body: str = "<html><body>hello</body></html>", content_type: str = "text/html; charset=utf-8"
) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type}, content=body.encode("utf-8"))


def fetch(url: str, network: Network, **options: object) -> str:
    return asyncio.run(fetch_text(url, resolver=network.resolver, transport=network.transport, **options))  # type: ignore[arg-type]


# --- URL shape --------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "javascript:alert(1)",
        "data:text/html,hello",
    ],
)
def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(FetchError, match="http and https"):
        parse_hop(url)


def test_credentials_in_the_url_are_refused() -> None:
    with pytest.raises(FetchError, match="credentials"):
        parse_hop("https://user:secret@example.com/")


@pytest.mark.parametrize("url", ["http://example.com:8080/", "https://example.com:8443/", "http://example.com:22/"])
def test_non_default_ports_are_refused(url: str) -> None:
    with pytest.raises(FetchError, match="default port"):
        parse_hop(url)


def test_default_port_written_explicitly_is_accepted() -> None:
    assert parse_hop("https://example.com:443/job?id=1").path_and_query == "/job?id=1"


def test_a_url_without_a_host_is_refused() -> None:
    with pytest.raises(FetchError, match="no host"):
        parse_hop("http:///path")


def test_an_invalid_port_is_refused() -> None:
    with pytest.raises(FetchError, match="invalid port"):
        parse_hop("http://example.com:99999/")


# --- address classification -----------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "10.1.2.3",  # private
        "172.16.0.5",  # private
        "192.168.1.1",  # private
        "169.254.169.254",  # link-local: cloud metadata services
        "224.0.0.1",  # multicast
        "240.0.0.1",  # reserved
        "0.0.0.0",  # unspecified
        "100.64.0.1",  # shared address space (carrier-grade NAT)
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique local
        "ff02::1",  # IPv6 multicast
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "::ffff:169.254.169.254",  # IPv4-mapped metadata address
        "2002:c0a8:0101::1",  # 6to4 embedding 192.168.1.1
        "not-an-address",
    ],
)
def test_non_public_addresses_are_refused(address: str) -> None:
    assert is_public_address(address) is False


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", PUBLIC_V6, "::ffff:93.184.216.34"])
def test_public_addresses_are_accepted(address: str) -> None:
    assert is_public_address(address) is True


# --- DNS answers ----------------------------------------------------------------------


def test_a_host_resolving_to_loopback_is_refused_before_any_request() -> None:
    network = Network({"evil.example": ["127.0.0.1"]}, lambda request: html())
    with pytest.raises(FetchError, match="non-public"):
        fetch("http://evil.example/", network)
    assert network.requests == []


def test_one_non_public_answer_among_public_ones_is_enough_to_refuse() -> None:
    network = Network({"mixed.example": [PUBLIC, "10.0.0.7"]}, lambda request: html())
    with pytest.raises(FetchError, match="non-public"):
        fetch("https://mixed.example/", network)
    assert network.requests == []


def test_a_host_that_does_not_resolve_is_refused() -> None:
    network = Network({"nowhere.example": []}, lambda request: html())
    with pytest.raises(FetchError, match="did not resolve"):
        fetch("https://nowhere.example/", network)


def test_an_ip_literal_is_checked_without_any_dns_lookup() -> None:
    network = Network({}, lambda request: html())
    with pytest.raises(FetchError, match="non-public"):
        fetch("http://169.254.169.254/latest/meta-data/", network)
    assert network.dns_calls == []
    assert network.requests == []


# --- the connection is pinned to the validated address ----------------------------------


def test_the_connection_goes_to_the_validated_address_with_the_original_host_name() -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: html("<p>job</p>"))
    text = fetch("https://example.com/jobs/1?ref=x", network)
    assert "job" in text
    request = network.requests[0]
    assert request.url.host == PUBLIC  # the address that was checked
    assert request.headers["host"] == "example.com"  # the name the server expects
    assert request.extensions["sni_hostname"] == "example.com"  # the name used for TLS verification
    assert request.url.path == "/jobs/1"
    assert request.url.query == b"ref=x"


def test_ipv6_addresses_are_bracketed_in_the_pinned_url() -> None:
    network = Network({"v6.example": [PUBLIC_V6]}, lambda request: html())
    fetch("https://v6.example/", network)
    assert network.requests[0].url.host == PUBLIC_V6


def test_dns_rebinding_between_redirects_is_caught() -> None:
    # First answer: public. The redirect to the same name gets a second answer: loopback. Must be refused.
    network = Network(
        {"rebind.example": iter([[PUBLIC], ["127.0.0.1"]])},
        lambda request: httpx.Response(302, headers={"location": "https://rebind.example/second"})
        if request.url.path == "/first"
        else html(),
    )
    with pytest.raises(FetchError, match="non-public"):
        fetch("https://rebind.example/first", network)
    assert [request.url.host for request in network.requests] == [PUBLIC]
    assert network.dns_calls == ["rebind.example", "rebind.example"]


# --- redirects -----------------------------------------------------------------------


def _redirect_chain(length: int) -> Callable[[httpx.Request], httpx.Response]:
    def respond(request: httpx.Request) -> httpx.Response:
        hop = int(request.url.path.rsplit("/", 1)[-1])
        if hop < length:
            return httpx.Response(302, headers={"location": f"https://example.com/hop/{hop + 1}"})
        return html("<p>finally</p>")

    return respond


def test_three_redirects_are_followed() -> None:
    network = Network({"example.com": [PUBLIC]}, _redirect_chain(3))
    assert "finally" in fetch("https://example.com/hop/0", network)
    assert len(network.requests) == 4


def test_a_fourth_redirect_is_refused() -> None:
    network = Network({"example.com": [PUBLIC]}, _redirect_chain(4))
    with pytest.raises(FetchError, match="too many redirects"):
        fetch("https://example.com/hop/0", network)


def test_a_redirect_to_the_metadata_address_is_refused_and_never_requested() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"}),
    )
    with pytest.raises(FetchError, match="non-public"):
        fetch("https://example.com/", network)
    assert all(request.url.host != "169.254.169.254" for request in network.requests)


def test_a_redirect_to_a_file_url_is_refused() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(302, headers={"location": "file:///etc/passwd"}),
    )
    with pytest.raises(FetchError, match="http and https"):
        fetch("https://example.com/", network)


def test_a_relative_redirect_stays_validated() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(301, headers={"location": "/moved"})
        if request.url.path == "/start"
        else html("<p>moved</p>"),
    )
    assert "moved" in fetch("https://example.com/start", network)


def test_a_redirect_without_a_location_is_refused() -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: httpx.Response(302))
    with pytest.raises(FetchError, match="without a location"):
        fetch("https://example.com/", network)


# --- content type, status, size ----------------------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    ["application/pdf", "image/png", "application/json", "text/plain", "application/octet-stream"],
)
def test_only_html_is_read(content_type: str) -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: html("payload", content_type=content_type))
    with pytest.raises(FetchError, match="only text/html"):
        fetch("https://example.com/", network)


def test_a_missing_content_type_is_refused() -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: httpx.Response(200, content=b"<p>x</p>"))
    with pytest.raises(FetchError, match="missing"):
        fetch("https://example.com/", network)


@pytest.mark.parametrize("status", [403, 404, 500, 503])
def test_non_200_responses_are_refused(status: int) -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: httpx.Response(status, text="nope"))
    with pytest.raises(FetchError, match=f"HTTP status {status}"):
        fetch("https://example.com/", network)


def test_a_declared_size_over_the_limit_is_refused_before_reading() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html", "content-length": "2000000"}, content=b"x"
        ),
    )
    with pytest.raises(FetchError, match="declared size"):
        fetch("https://example.com/", network)


def test_a_body_over_the_limit_is_refused_even_without_a_declared_size() -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: html("a" * 2_000_000))
    # httpx may add a Content-Length header, in which case the declared-size check fires first. Both are refusals.
    with pytest.raises(FetchError, match="over the"):
        fetch("https://example.com/", network)


def test_a_body_exactly_at_the_limit_is_accepted() -> None:
    network = Network({"example.com": [PUBLIC]}, lambda request: html("a" * 10))
    assert fetch("https://example.com/", network, max_bytes=10) == "a" * 10


def test_the_declared_charset_is_honoured() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html; charset=iso-8859-1"}, content="café".encode("iso-8859-1")
        ),
    )
    assert fetch("https://example.com/", network) == "café"


def test_an_unknown_charset_falls_back_to_utf8() -> None:
    network = Network(
        {"example.com": [PUBLIC]},
        lambda request: httpx.Response(200, headers={"content-type": "text/html; charset=x-made-up"}, content=b"ok"),
    )
    assert fetch("https://example.com/", network) == "ok"


# --- failures and deadlines --------------------------------------------------------------


def test_a_transport_failure_becomes_a_fetch_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    network = Network({"example.com": [PUBLIC]}, fail)
    with pytest.raises(FetchError, match="network error"):
        fetch("https://example.com/", network)


def test_the_overall_deadline_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(1)
        return "too late"

    monkeypatch.setattr(safe_http, "_fetch", slow)
    monkeypatch.setattr(safe_http, "OVERALL_DEADLINE_SECONDS", 0.05)
    network = Network({"example.com": [PUBLIC]}, lambda request: html())
    with pytest.raises(FetchError, match="deadline"):
        fetch("https://example.com/", network)


def test_the_default_resolver_is_the_system_one_and_is_looked_up_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_system(host: str) -> list[str]:
        return [PUBLIC]

    monkeypatch.setattr(safe_http, "system_resolver", fake_system)
    text = asyncio.run(fetch_text("https://example.com/", transport=httpx.MockTransport(lambda r: html("via default"))))
    assert "via default" in text
