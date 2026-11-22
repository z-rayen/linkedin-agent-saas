"""The address a request really comes from, for the per-address limits.

X-Forwarded-For is a list that each proxy extends on the right: the browser's address is the rightmost entry added by a
proxy we trust. The header is believed only when the connection's own peer is a trusted proxy (TRUSTED_PROXIES). Walking
from the right, the first entry that is not a trusted proxy is the client. Entries on the left were written by whoever
sent the request, so they are never used.
"""

import ipaddress
from collections.abc import Iterable

UNKNOWN_ADDRESS = "unknown"
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(trusted: str) -> tuple[Network, ...]:
    """The configured proxies as networks. Blank entries are ignored; the settings validator refuses bad ones."""
    networks: list[Network] = []
    for entry in trusted.split(","):
        if entry.strip():
            networks.append(ipaddress.ip_network(entry.strip(), strict=False))
    return tuple(networks)


def _as_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_trusted(value: str, networks: Iterable[Network]) -> bool:
    address = _as_address(value)
    return address is not None and any(address in network for network in networks)


def client_address(peer: str | None, forwarded_for: str | None, trusted: str) -> str:
    """The client's address. Without a trusted peer, the connection's own address is used and the header is ignored."""
    if peer is None:
        return UNKNOWN_ADDRESS
    networks = parse_networks(trusted)
    if not forwarded_for or not _is_trusted(peer, networks):
        return peer
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop
    return hops[0] if hops else peer
