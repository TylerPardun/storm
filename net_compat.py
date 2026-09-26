"""Process-wide network workarounds.

Some data hosts (data.nssl.noaa.gov among them) publish an IPv6 address
ahead of their IPv4 one. On networks without a working IPv6 route, Python
tries that address first and waits several seconds for it to fail before
falling back to IPv4, on every single request. curl and browsers race the
two families ("happy eyeballs") and never notice; urllib, requests and
http.client do not. For a host that offers both families we therefore list
the IPv4 addresses first. IPv6 stays in the list as the fallback, and
IPv6-only hosts are left exactly as they were.
"""

import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _original_getaddrinfo(*args, **kwargs)
    return sorted(results, key=lambda info: info[0] != socket.AF_INET)  # stable


def prefer_ipv4() -> None:
    """Install the IPv4-first lookup once for the whole process."""
    socket.getaddrinfo = _ipv4_first_getaddrinfo
