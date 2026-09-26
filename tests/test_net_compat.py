import socket

import net_compat

_V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0))
_V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443))


def test_ipv4_is_tried_before_ipv6_but_ipv6_stays_as_fallback(monkeypatch):
    monkeypatch.setattr(net_compat, "_original_getaddrinfo", lambda *a, **k: [_V6, _V4])
    assert net_compat._ipv4_first_getaddrinfo("host", 443) == [_V4, _V6]


def test_ipv6_only_hosts_are_unchanged(monkeypatch):
    monkeypatch.setattr(net_compat, "_original_getaddrinfo", lambda *a, **k: [_V6])
    assert net_compat._ipv4_first_getaddrinfo("host", 443) == [_V6]


def test_prefer_ipv4_installs_once(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    net_compat.prefer_ipv4()
    net_compat.prefer_ipv4()
    assert socket.getaddrinfo is net_compat._ipv4_first_getaddrinfo
