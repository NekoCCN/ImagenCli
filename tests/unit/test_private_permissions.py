import os
import stat

import pytest

from imagen_cli.storage import private_state, safety


def test_windows_acl_policy_accepts_only_protected_current_user_full_control():
    check = safety._validate_windows_acl
    check("user", "user", 0x1000, [(0, 3, 0x1F01FF, "user")])
    for owner, control, entries in [
        ("other", 0x1000, [(0, 3, 0x1F01FF, "user")]),
        ("user", 0, [(0, 3, 0x1F01FF, "user")]),
        ("user", 0x1000, []),
        ("user", 0x1000, [(0, 3, 0x1F01FF, "everyone")]),
        ("user", 0x1000, [(0, 3, 0x1F01FF, "user")] * 2),
        ("user", 0x1000, [(0, 0x10, 0x1F01FF, "user")]),
        ("user", 0x1000, [(1, 3, 0x1F01FF, "user")]),
        ("user", 0x1000, [(0, 3, 0x120089, "user")]),
    ]:
        with pytest.raises(PermissionError):
            check(owner, "user", control, entries)


def test_windows_backend_failure_does_not_fallback_to_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "_WINDOWS", True)
    monkeypatch.setattr(private_state, "_WINDOWS", True)

    def unavailable():
        raise PermissionError("Windows ACL unavailable")

    monkeypatch.setattr(safety, "_WindowsSecurity", unavailable)
    with pytest.raises(PermissionError, match="Windows ACL unavailable"):
        private_state.PrivateStateStore(tmp_path / "private")
    assert not (tmp_path / "private").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_posix_checks_descriptor_before_writing_payload(tmp_path, monkeypatch):
    store = private_state.PrivateStateStore(tmp_path / "private")
    original = os.open

    def insecure_open(path, flags, mode=0o777):
        fd = original(path, flags, mode)
        if flags & os.O_CREAT:
            os.fchmod(fd, 0o644)
        return fd

    monkeypatch.setattr(os, "open", insecure_open)
    with pytest.raises(PermissionError):
        store.write("ctx_a", b"must-not-be-written")
    assert not (store.root / "ctx_a.bin").exists()


def test_reparse_directory_is_rejected_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "_WINDOWS", True)
    original = safety.Path.lstat

    class Reparse:
        st_file_attributes = 0x400
        st_mode = stat.S_IFDIR

    monkeypatch.setattr(
        safety.Path, "lstat", lambda path: Reparse() if path == tmp_path else original(path)
    )
    with pytest.raises(ValueError, match="reparse"):
        safety.secure_directory(tmp_path / "private")


@pytest.mark.parametrize("creating", [False, True])
def test_windows_file_verification_failure_closes_handle(tmp_path, monkeypatch, creating):
    events = []

    class Security:
        def open(self, path):
            events.append("open")
            return 123

        def create(self, path, *, directory):
            events.append("create")
            return 123

        def verify(self, handle, *, directory):
            raise PermissionError("unsafe ACL")

        def close(self, handle):
            events.append(("close", handle))

    import sys
    from types import ModuleType

    monkeypatch.setitem(sys.modules, "msvcrt", ModuleType("msvcrt"))
    monkeypatch.setattr(private_state, "_WINDOWS", True)
    monkeypatch.setattr(private_state, "_WindowsSecurity", Security)
    store = private_state.PrivateStateStore(tmp_path / "private")
    with pytest.raises(PermissionError, match="unsafe ACL"):
        store._open(store.root / "ctx_a.bin", create=creating)
    assert events == ["create" if creating else "open", ("close", 123)]


def test_windows_directory_verification_failure_closes_handle(tmp_path, monkeypatch):
    closed = []

    class Security:
        def open(self, path):
            return 123

        def verify(self, handle, *, directory):
            raise PermissionError("unsafe ACL")

        def close(self, handle):
            closed.append(handle)

    monkeypatch.setattr(safety, "_WINDOWS", True)
    monkeypatch.setattr(safety, "_WindowsSecurity", Security)
    with pytest.raises(PermissionError, match="unsafe ACL"):
        safety.secure_directory(tmp_path)
    assert closed == [123]


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows ACL APIs and filesystem")
def test_windows_real_private_acl_roundtrip_and_unsafe_existing_directory(tmp_path):
    root = tmp_path / "private"
    store = private_state.PrivateStateStore(root)
    saved = store.write("ctx_a", b"test-only-opaque")
    assert store.read("ctx_a", saved["sha256"]) == b"test-only-opaque"
    with pytest.raises(FileExistsError):
        store.write("ctx_a", b"replacement")
    unsafe = tmp_path / "inherited"
    unsafe.mkdir()
    with pytest.raises(PermissionError):
        private_state.PrivateStateStore(unsafe)
