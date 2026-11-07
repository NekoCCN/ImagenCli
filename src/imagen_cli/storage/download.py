"""Credential-free HTTPS download of provider result URLs.

Every hop re-resolves the host, rejects the whole host when any answer is not
globally routable, and connects to the validated address directly so a later
DNS answer cannot redirect the socket.
"""

import ipaddress
import socket
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

if TYPE_CHECKING:
    import ssl

__all__ = ["DownloadError", "fetch"]

MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
MAX_BYTES = 64 * 1024 * 1024
_STATUS_REDIRECTS = {301, 302, 303, 307, 308}


class DownloadError(ValueError):
    pass


class TransientDownloadError(DownloadError):
    pass


_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    # Transparent proxies (Clash, sing-box) answer DNS with 198.18/15 fake IPs
    # and route the connection themselves; loopback and private ranges stay refused.
    # Multicast addresses report is_global for some scopes but are never a peer.
    return (address.is_global and not address.is_multicast) or address in _FAKE_IP


def _resolve(host: str, port: int) -> list[str]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise DownloadError("host did not resolve") from error
    addresses: list[str] = []
    for answer in answers:
        value = answer[4][0]
        if isinstance(value, str):
            addresses.append(value)
    return list(dict.fromkeys(addresses))


def _pinned_socket(host: str, port: int, address: str, timeout: float) -> "ssl.SSLSocket":
    # Imported lazily: test harnesses replace socket.socket before import.
    import ssl

    raw = socket.create_connection((address, port), timeout=timeout)
    try:
        if not _public_ip(raw.getpeername()[0]):
            raise DownloadError("connection peer is not public")
        return ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    except BaseException:
        raw.close()
        raise


def _target(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    # shortcut: only https:443 provider URLs, widen when a provider needs another port
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 443}
    ):
        raise DownloadError("only https provider URLs are accepted")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.hostname, 443, path


def _remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise DownloadError("download deadline exceeded")
    return remaining


def _request(host: str, port: int, path: str, deadline: float, max_bytes: int) -> tuple[
    int, str | None, bytes
]:
    _remaining(deadline)
    addresses = _resolve(host, port)
    if not addresses or any(not _public_ip(value) for value in addresses):
        raise DownloadError("host resolves outside the public internet")
    remaining = _remaining(deadline)
    import http.client

    try:
        sock = _pinned_socket(host, port, addresses[0], remaining)
    except OSError as error:
        raise TransientDownloadError("download failed") from error
    conn = http.client.HTTPSConnection(host, port, timeout=remaining)
    conn.sock = sock
    response = None
    try:
        sock.settimeout(_remaining(deadline))
        conn.request("GET", path, headers={"Connection": "close"})
        sock.settimeout(_remaining(deadline))
        # 保留套接字直至读完正文；getresponse 会提前关闭 Connection: close 的连接。
        response = http.client.HTTPResponse(sock, method="GET")
        response.begin()
        if response.status in _STATUS_REDIRECTS:
            return response.status, response.getheader("Location"), b""
        if response.status >= 500:
            raise TransientDownloadError(f"unexpected download status {response.status}")
        if response.status != 200:
            raise DownloadError(f"unexpected download status {response.status}")
        length = response.getheader("Content-Length")
        declared = None
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                raise DownloadError("invalid content length") from None
            if declared < 0:
                raise DownloadError("invalid content length")
            if declared > max_bytes:
                raise DownloadError("download exceeds the byte limit")
        body = bytearray()
        while True:
            sock.settimeout(_remaining(deadline))
            # 每次只取已到达的数据，避免 read 为填满缓冲区而持续等待慢速响应。
            chunk = response.read1(min(65536, max_bytes - len(body) + 1))
            if not chunk:
                if not response.chunked and declared is not None and len(body) != declared:
                    raise TransientDownloadError("incomplete download response")
                return response.status, None, bytes(body)
            if len(body) + len(chunk) > max_bytes:
                raise DownloadError("download exceeds the byte limit")
            body.extend(chunk)
    except http.client.IncompleteRead as error:
        raise TransientDownloadError("incomplete download response") from error
    except http.client.HTTPException as error:
        raise DownloadError("malformed download response") from error
    except OSError as error:
        raise TransientDownloadError("download failed") from error
    finally:
        if response is not None:
            response.close()
        conn.close()


def _follow(url: str, deadline: float, max_bytes: int) -> bytes:
    for _ in range(MAX_REDIRECTS + 1):
        host, port, path = _target(url)
        status, location, body = _request(host, port, path, deadline, max_bytes)
        if status not in _STATUS_REDIRECTS:
            return body
        if not location:
            raise DownloadError("redirect without a location")
        url = urljoin(url, location)
    raise DownloadError("too many redirects")


def fetch(url: str, *, timeout: float = 30.0, max_bytes: int = MAX_BYTES) -> bytes:
    """Download a provider result URL with no credentials and no redirect to private hosts."""
    deadline = monotonic() + timeout
    failures = 0
    while True:
        try:
            return _follow(url, deadline, max_bytes)
        except TransientDownloadError:
            failures += 1
            if failures >= MAX_ATTEMPTS or monotonic() >= deadline:
                raise
