"""SSRF guard for Apprise notification URLs.

Apprise lets users configure notification URLs, some of which (``http``,
``https``, ``json``, ``form``, ``xml``, ``custom``, ...) make the server POST to
an arbitrary host. Without restrictions a user could point the server at
internal services (e.g. ``http://127.0.0.1`` or cloud metadata
``http://169.254.169.254``).

This module resolves the target host and rejects URLs that resolve to
private, loopback, link-local, reserved, or metadata addresses.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

# Apprise schemas that forward requests to a user-specified arbitrary host.
_RAW_FORWARDING_SCHEMES = {
    "http",
    "https",
    "json",
    "form",
    "xml",
    "custom",
    "post",
    "get",
}


def _resolves_to_private_host(host):
    """Return True if the host is, or resolves to, a private/unsafe IP."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            address = ipaddress.ip_address(socket.gethostbyname(host))
        except OSError:
            # Unresolvable; we cannot verify, so do not block based on host.
            return False
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified,
    )


def notification_url_is_safe(url):
    """Return False if the notification URL targets a private/internal host."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()

    if scheme not in _RAW_FORWARDING_SCHEMES:
        # Provider-specific schemas (discord, tgram, ...) target fixed hosts.
        return True

    return not _resolves_to_private_host(parts.hostname)
