"""Shared offline test fixtures."""

import errno

import pytest

pytest_plugins = ["tests.fixtures.offline"]


@pytest.fixture
def symlink_or_skip():
    """Create a symlink, skipping when the account lacks the OS privilege.

    Windows without Developer Mode raises WinError 1314; Linux containers may
    raise EPERM. Other failures still surface as test errors.
    """

    def make(link, target, *, target_is_directory: bool = False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as error:
            if getattr(error, "winerror", None) != 1314 and error.errno not in {
                errno.EPERM,
                errno.EACCES,
            }:
                raise
            pytest.skip(f"symlink creation is not permitted here: {error}")

    return make


@pytest.fixture(autouse=True)
def _chinese_ui_for_legacy_tests(monkeypatch, tmp_path):
    """Existing assertions were written against the Chinese interface.

    I18n tests override this by clearing ``IMAGEN_LANG`` or passing ``--lang``.
    """
    monkeypatch.setenv("IMAGEN_LANG", "zh-CN")
    from imagen_cli import i18n

    monkeypatch.setattr(i18n, "config_path", lambda: tmp_path / "config" / "config.json")
    i18n.reset_language()
    yield
    i18n.reset_language()


def pytest_collection_modifyitems(config, items):
    """Skip GPU tests on a machine without a CUDA provider, even under `-m cuda`."""
    if not any("cuda" in item.keywords for item in items):
        return
    try:
        # Imported dynamically: the runtime is an optional extra, so a static
        # import here would fail type checking and collection on a base install.
        from importlib import import_module

        providers = import_module("onnxruntime").get_available_providers()
    except Exception:  # noqa: BLE001 - an absent runtime means no GPU either
        providers = []
    if "CUDAExecutionProvider" in providers:
        return
    skip = pytest.mark.skip(reason="no CUDA provider available here")
    for item in items:
        if "cuda" in item.keywords:
            item.add_marker(skip)
