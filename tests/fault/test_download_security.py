"""Provider result URLs are fetched only from public HTTPS addresses."""

import socket
import threading

import pytest

from imagen_cli.storage import download


def _serve(payload: bytes) -> tuple[socket.socket, bytes]:
    ours, theirs = socket.socketpair()
    received = bytearray()

    def run() -> None:
        try:
            while chunk := theirs.recv(65536):
                received.extend(chunk)
                if b"\r\n\r\n" in received:
                    break
            theirs.sendall(payload)
            theirs.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        finally:
            theirs.close()

    threading.Thread(target=run, daemon=True).start()
    return ours, received


def _wire(monkeypatch, payloads, addresses=("93.184.216.34",)):
    queue = list(payloads)
    seen: list[tuple[str, str]] = []
    requests: list[bytes] = []

    def resolve(host, port):
        if callable(addresses):
            return addresses(host)
        return list(addresses)

    def connect(host, port, ip, timeout):
        seen.append((host, ip))
        ours, received = _serve(queue.pop(0))
        requests.append(received)
        return ours

    monkeypatch.setattr(download, "_resolve", resolve)
    monkeypatch.setattr(download, "_pinned_socket", connect)
    return seen, requests


def _response(body: bytes, status="200 OK", extra: str = "") -> bytes:
    return (
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n"
        f"Connection: close\r\n{extra}\r\n"
    ).encode() + body


def _forbidden(*args, **kwargs):
    raise AssertionError("must not open a connection")


@pytest.mark.parametrize(
    "url",
    [
        "http://images.example/a.png",
        "ftp://images.example/a.png",
        "https://user:secret@images.example/a.png",
        "https://images.example:8443/a.png",
        "https:///a.png",
        "not a url",
    ],
)
def test_only_public_https_urls_without_userinfo_are_accepted(url, monkeypatch):
    monkeypatch.setattr(download, "_pinned_socket", _forbidden)
    with pytest.raises(ValueError):
        download.fetch(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fd00::1", "fe80::1",
        "fc00::1", "ff02::1", "::ffff:127.0.0.1",
    ],
)
def test_non_public_resolved_addresses_are_rejected(address, monkeypatch):
    monkeypatch.setattr(download, "_resolve", lambda host, port: [address])
    monkeypatch.setattr(download, "_pinned_socket", _forbidden)
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_one_private_answer_rejects_the_host(monkeypatch):
    """A rebinding answer pair must not be partially accepted."""
    monkeypatch.setattr(
        download, "_resolve",
        lambda host, port: ["93.184.216.34", "127.0.0.1"],
    )
    monkeypatch.setattr(download, "_pinned_socket", _forbidden)
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_literal_private_host_is_rejected(monkeypatch):
    monkeypatch.setattr(download, "_pinned_socket", _forbidden)
    with pytest.raises(ValueError):
        download.fetch("https://127.0.0.1/a.png")


def test_body_is_returned_from_the_pinned_public_address(monkeypatch):
    seen, requests = _wire(monkeypatch, [_response(b"image-bytes")])
    assert download.fetch("https://images.example/a.png?token=1") == b"image-bytes"
    assert seen == [("images.example", "93.184.216.34")]
    assert b"authorization" not in requests[0].lower()
    assert b"cookie" not in requests[0].lower()


def test_redirect_chain_is_followed_but_limited_to_three_hops(monkeypatch):
    hop = _response(b"", status="302 Found", extra="Location: /next\r\n")
    seen, _ = _wire(monkeypatch, [hop, hop, hop, _response(b"final")])
    assert download.fetch("https://images.example/a.png") == b"final"
    assert [ip for _, ip in seen] == ["93.184.216.34"] * 4

    seen, _ = _wire(monkeypatch, [hop, hop, hop, hop, _response(b"final")])
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_redirect_to_a_private_host_is_rejected(monkeypatch):
    answers = {
        "images.example": ["93.184.216.34"],
        "internal.example": ["10.0.0.7"],
    }
    _wire(
        monkeypatch,
        [_response(b"", status="307 Temporary Redirect",
                   extra="Location: https://internal.example/a.png\r\n")],
        addresses=lambda host: answers.get(host, ["127.0.0.1"]),
    )
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_oversized_response_is_rejected(monkeypatch):
    _wire(monkeypatch, [_response(b"x" * 50)])
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png", max_bytes=10)

    chunked = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
        b"20\r\n" + b"x" * 32 + b"\r\n0\r\n\r\n"
    )
    _wire(monkeypatch, [chunked])
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png", max_bytes=10)


@pytest.mark.parametrize("status", ["404 Not Found", "403 Forbidden"])
def test_error_status_is_rejected(status, monkeypatch):
    _wire(monkeypatch, [_response(b"", status=status)])
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_redirect_without_location_is_rejected(monkeypatch):
    _wire(monkeypatch, [_response(b"", status="302 Found")])
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")


def test_transient_failure_is_retried_within_a_three_attempt_ceiling(monkeypatch):
    seen, _ = _wire(
        monkeypatch,
        [_response(b"", status="503 Service Unavailable"), _response(b"ok")],
    )
    assert download.fetch("https://images.example/a.png") == b"ok"
    assert len(seen) == 2

    seen, _ = _wire(monkeypatch, [_response(b"", status="502 Bad Gateway")] * 3)
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")
    assert len(seen) == 3


def test_permanent_failure_is_not_retried(monkeypatch):
    seen, _ = _wire(monkeypatch, [_response(b"", status="404 Not Found")] * 3)
    with pytest.raises(ValueError):
        download.fetch("https://images.example/a.png")
    assert len(seen) == 1
