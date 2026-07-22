"""Unit tests for the GitHub-release update check (version compare, SHA-256
extraction, and the latest-release fetch/parse).

Run with `python -m pytest tests/ -q`, or directly:
`python tests/test_update_check.py`. No real network is touched — update_check's
module-level `httpx` is swapped for a tiny fake.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import update_check  # noqa: E402
from update_check import (  # noqa: E402
    UpdateInfo,
    extract_sha256,
    fetch_latest,
    is_newer,
    parse_version,
)


# --- parse_version --------------------------------------------------------

def test_parse_version_strips_v_prefix():
    assert parse_version("v2.1.0") == (2, 1, 0)
    assert parse_version("2.1.0") == (2, 1, 0)


def test_parse_version_drops_prerelease_and_build():
    assert parse_version("v2.2.0-rc1") == (2, 2, 0)
    assert parse_version("2.2.0+build7") == (2, 2, 0)


def test_parse_version_unparseable_is_empty():
    assert parse_version("") == ()
    assert parse_version("nightly") == ()


# --- is_newer -------------------------------------------------------------

def test_is_newer_basic_ordering():
    assert is_newer("v2.1.0", "2.0.1") is True
    assert is_newer("v2.0.1", "2.1.0") is False


def test_is_newer_equal_is_false():
    assert is_newer("v2.1.0", "2.1.0") is False
    # zero-padding: 2.1 and 2.1.0 are the same version
    assert is_newer("v2.1", "2.1.0") is False


def test_is_newer_numeric_not_lexical():
    # the classic string-compare trap: "1.9" > "1.10" lexically, but 1.10 wins
    assert is_newer("v1.10.0", "1.9.0") is True


def test_is_newer_garbage_latest_is_false():
    assert is_newer("", "2.1.0") is False


# --- extract_sha256 -------------------------------------------------------

def test_extract_sha256_bare_hash():
    h = "a" * 64
    assert extract_sha256(f"notes\n{h}\nmore") == h


def test_extract_sha256_prefers_line_naming_the_exe():
    other = "b" * 64
    wanted = "c" * 64
    body = f"checksum: {other}\nClawdmeter.exe  {wanted}\n"
    assert extract_sha256(body, "Clawdmeter.exe") == wanted


def test_extract_sha256_none_when_absent():
    assert extract_sha256("no hashes here") == ""
    assert extract_sha256("") == ""


# --- fetch_latest (fake httpx) -------------------------------------------

class _FakeResp:
    def __init__(self, data, raise_exc=None):
        self._data = data
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise:
            raise self._raise

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class _FakeClient:
    payload = None
    raise_exc = None

    def __init__(self, timeout=None, headers=None, follow_redirects=False):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return _FakeResp(_FakeClient.payload, _FakeClient.raise_exc)


def _install_fake_httpx(payload, raise_exc=None):
    """Swap update_check's module-level httpx for a fake. Returns a restore fn."""
    real = update_check.httpx
    fake = types.ModuleType("httpx")
    fake.HTTPError = Exception
    _FakeClient.payload = payload
    _FakeClient.raise_exc = raise_exc
    fake.Client = _FakeClient
    update_check.httpx = fake
    return lambda: setattr(update_check, "httpx", real)


def test_fetch_latest_parses_release():
    h = "d" * 64
    payload = {
        "tag_name": "v2.2.0",
        "html_url": "https://github.com/weltern/Clawdmeter-Windows/releases/tag/v2.2.0",
        "body": f"Clawdmeter.exe  {h}\nShiny new things.",
        "assets": [
            {"name": "Clawdmeter.exe",
             "browser_download_url": "https://example.com/Clawdmeter.exe"},
        ],
    }
    restore = _install_fake_httpx(payload)
    try:
        info = fetch_latest()
    finally:
        restore()
    assert isinstance(info, UpdateInfo)
    assert info.version == "2.2.0"
    assert info.tag == "v2.2.0"
    assert info.asset_url == "https://example.com/Clawdmeter.exe"
    assert info.sha256 == h


def test_fetch_latest_skips_prerelease():
    restore = _install_fake_httpx({"tag_name": "v2.2.0", "prerelease": True})
    try:
        assert fetch_latest() is None
    finally:
        restore()


def test_fetch_latest_swallows_http_error():
    restore = _install_fake_httpx({"tag_name": "v2.2.0"},
                                  raise_exc=Exception("503"))
    try:
        assert fetch_latest() is None
    finally:
        restore()


def test_fetch_latest_none_on_missing_tag():
    restore = _install_fake_httpx({"body": "no tag here"})
    try:
        assert fetch_latest() is None
    finally:
        restore()


# --- macOS branch: asset resolution + browser-open (no real network) -------

def _force_macos():
    """Make update_check believe it's running on macOS. Returns a restore fn."""
    real = update_check._is_macos
    update_check._is_macos = lambda: True
    return lambda: setattr(update_check, "_is_macos", real)


def test_fetch_latest_macos_picks_zip_asset():
    """On macOS the downloadable is Clawdmeter-macos.zip, not the .exe — the
    asset URL and the SHA-256 must come from the mac line, not the Windows one."""
    h_win = "e" * 64
    h_mac = "f" * 64
    payload = {
        "tag_name": "v2.5.0",
        "html_url": "https://github.com/weltern/Clawdmeter-Windows/releases/tag/v2.5.0",
        "body": f"Clawdmeter.exe  {h_win}\nClawdmeter-macos.zip  {h_mac}\n",
        "assets": [
            {"name": "Clawdmeter.exe",
             "browser_download_url": "https://example.com/Clawdmeter.exe"},
            {"name": "Clawdmeter-macos.zip",
             "browser_download_url": "https://example.com/Clawdmeter-macos.zip"},
        ],
    }
    unmac = _force_macos()
    restore = _install_fake_httpx(payload)
    try:
        info = fetch_latest()
    finally:
        restore()
        unmac()
    assert isinstance(info, UpdateInfo)
    assert info.asset_url == "https://example.com/Clawdmeter-macos.zip"
    assert info.sha256 == h_mac


def test_fetch_latest_macos_default_asset_name_when_absent():
    """With no matching asset, the SHA-256 lookup still keys off the mac name, so
    a bare hash on the mac line wins over a hash that names the .exe."""
    h_win = "1" * 64
    h_mac = "2" * 64
    payload = {
        "tag_name": "v2.5.0",
        "html_url": "https://github.com/weltern/Clawdmeter-Windows/releases/tag/v2.5.0",
        "body": f"Clawdmeter.exe  {h_win}\nClawdmeter-macos.zip  {h_mac}\n",
        "assets": [],
    }
    unmac = _force_macos()
    restore = _install_fake_httpx(payload)
    try:
        info = fetch_latest()
    finally:
        restore()
        unmac()
    assert info.asset_url == ""       # nothing to download from the asset list
    assert info.sha256 == h_mac       # but the mac line's hash is still resolved


def test_download_url_opens_release_page():
    """A detected update opens the release page for a manual download — never a
    direct artifact URL (macOS can't self-replace a running .app)."""
    info = UpdateInfo(
        version="2.5.0", tag="v2.5.0",
        url="https://github.com/weltern/Clawdmeter-Windows/releases/tag/v2.5.0",
        notes="", asset_url="https://example.com/Clawdmeter-macos.zip",
        sha256="f" * 64,
    )
    unmac = _force_macos()
    try:
        opened = update_check.download_url(info)
    finally:
        unmac()
    # The page, not the .zip asset — the browser gets pointed at the release page.
    assert opened == info.url
    assert opened != info.asset_url


def test_download_url_rejects_offsite_url():
    """A spoofed html_url outside github.com/<REPO> falls back to the canonical
    releases page so a compromised response can't redirect the user."""
    evil = UpdateInfo(
        version="9.9.9", tag="v9.9.9", url="https://evil.example/pwn",
        notes="", asset_url="", sha256="",
    )
    assert update_check.download_url(evil) == update_check.RELEASES_PAGE
    assert update_check.download_url(None) == update_check.RELEASES_PAGE


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
