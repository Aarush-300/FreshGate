"""URL validation and SSRF protection for outbound provider requests."""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


def is_production() -> bool:
    return os.getenv("FRESHGATE_ENV", "development").lower() == "production"


def validate_base_url(url: str, *, allow_private: bool | None = None) -> str:
    """Return a normalized URL (no trailing slash) or raise ValueError with a user-friendly message."""
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in {"http", "https"} or not p.netloc or not p.hostname:
        raise ValueError("Enter a full URL that starts with http:// or https://")
    if p.username or p.password:
        raise ValueError("Do not put credentials inside the URL.")
    if allow_private is None:
        allow_private = not is_production()
    if not allow_private:
        if p.scheme != "https":
            raise ValueError("Production servers only accept https:// provider URLs.")
        try:
            infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise ValueError("That host name could not be resolved.")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise ValueError(
                    "This server cannot reach local or private addresses. Use a public https URL (for example an ngrok or Cloudflare tunnel) or run FreshGate locally."
                )
    return url.rstrip("/")
