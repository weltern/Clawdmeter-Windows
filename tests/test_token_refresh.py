"""Tests for the safety-critical credential write path in token_refresh.

Exercises `_write_tokens_safely` (backup -> atomic write -> verify -> revert) and
the helpers directly — no network, so the OAuth POST isn't involved.

Run with `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import token_refresh as tr  # noqa: E402


def _creds(access: str, expires: int = 1) -> str:
    return json.dumps({"claudeAiOauth": {
        "accessToken": access, "refreshToken": "r", "expiresAt": expires}})


def test_write_success_deletes_backup(tmp_path):
    cred = tmp_path / ".credentials.json"
    orig = _creds("old")
    cred.write_text(orig, encoding="utf-8")
    new = {"claudeAiOauth": {"accessToken": "new", "refreshToken": "r", "expiresAt": 2}}

    res = tr._write_tokens_safely(cred, orig, new, expected_access="new")

    assert res.ok
    assert json.loads(cred.read_text())["claudeAiOauth"]["accessToken"] == "new"
    assert not tr._backup_path(cred).exists()   # backup cleaned on verified success


def test_write_mismatch_reverts(tmp_path):
    cred = tmp_path / ".credentials.json"
    orig = _creds("old")
    cred.write_text(orig, encoding="utf-8")
    # The write lands "written", but we claim to expect "different" -> verify
    # fails -> the file is reverted to the original.
    data = {"claudeAiOauth": {"accessToken": "written", "refreshToken": "r"}}

    res = tr._write_tokens_safely(cred, orig, data, expected_access="different")

    assert (not res.ok) and res.reverted
    assert json.loads(cred.read_text())["claudeAiOauth"]["accessToken"] == "old"


def test_oauth_block_and_expiry(tmp_path):
    cred = tmp_path / ".credentials.json"
    cred.write_text(_creds("a", expires=12345), encoding="utf-8")
    assert tr.token_expiry_ms(cred) == 12345
    assert tr._oauth_block(json.loads(_creds("a")))["accessToken"] == "a"
    assert tr.token_expiry_ms(tmp_path / "missing.json") is None


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in fns:
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print(f"ok  {name}")
    print(f"\n{len(fns)} passed")
