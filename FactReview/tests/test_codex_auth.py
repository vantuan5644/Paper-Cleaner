"""Codex auth cache: expired access tokens are refreshed and written back."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llm import codex_auth


def _jwt(exp: float) -> str:
    def seg(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg({'exp': int(exp)})}.sig"


def _write_codex_cli_auth(path: Path, access_token: str, refresh_token: str = "rt-old") -> None:
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": "id-old",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "account_id": "acct-1",
                },
                "last_refresh": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cache lookup at a temp CODEX_HOME and nothing else."""
    monkeypatch.delenv("OPENAI_CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_CODEX_TOKEN", raising=False)
    monkeypatch.setattr(codex_auth, "_candidate_cache_files", lambda: [tmp_path / "auth.json"])
    return tmp_path


def test_valid_token_is_not_refreshed(codex_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = _jwt(time.time() + 3600)
    _write_codex_cli_auth(codex_home / "auth.json", token)
    monkeypatch.setattr(codex_auth, "_post_json", lambda *a, **k: pytest.fail("refresh should not run"))

    auth = codex_auth.get_codex_auth(allow_browser_login=False)

    assert auth.access_token == token
    assert auth.account_id == "acct-1"


def test_expired_token_is_refreshed_and_persisted(codex_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = codex_home / "auth.json"
    _write_codex_cli_auth(path, _jwt(time.time() - 60))
    new_token = _jwt(time.time() + 3600)
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, payload: dict[str, str], timeout_sec: int = 20) -> dict[str, Any]:
        calls.append({"url": url, **payload})
        return {"access_token": new_token, "refresh_token": "rt-new", "id_token": "id-new"}

    monkeypatch.setattr(codex_auth, "_post_json", fake_post)

    auth = codex_auth.get_codex_auth(allow_browser_login=False)

    assert auth.access_token == new_token
    assert auth.account_id == "acct-1"
    assert calls == [
        {
            "url": "https://auth.openai.com/oauth/token",
            "client_id": codex_auth.CODEX_OAUTH_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": "rt-old",
        }
    ]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["tokens"] == {
        "id_token": "id-new",
        "access_token": new_token,
        "refresh_token": "rt-new",
        "account_id": "acct-1",
    }
    assert saved["auth_mode"] == "chatgpt"
    assert saved["last_refresh"] != "2026-01-01T00:00:00Z"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_concurrent_callers_refresh_once(codex_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Refresh tokens rotate: a second refresh with the old one would be
    # rejected as reused, so parallel extractor threads must share one refresh.
    _write_codex_cli_auth(codex_home / "auth.json", _jwt(time.time() - 60))
    new_token = _jwt(time.time() + 3600)
    calls: list[int] = []

    def fake_post(url: str, payload: dict[str, str], timeout_sec: int = 20) -> dict[str, Any]:
        calls.append(1)
        time.sleep(0.05)
        return {"access_token": new_token, "refresh_token": "rt-new"}

    monkeypatch.setattr(codex_auth, "_post_json", fake_post)

    results: list[str] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(codex_auth.get_codex_auth(allow_browser_login=False).access_token)
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert results == [new_token] * 4


def test_refresh_failure_raises_actionable_error(codex_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_codex_cli_auth(codex_home / "auth.json", _jwt(time.time() - 60))

    def fake_post(url: str, payload: dict[str, str], timeout_sec: int = 20) -> dict[str, Any]:
        raise RuntimeError("HTTP 401: refresh_token_expired")

    monkeypatch.setattr(codex_auth, "_post_json", fake_post)

    with pytest.raises(RuntimeError, match="codex login"):
        codex_auth.get_codex_auth(allow_browser_login=False)


def test_expired_token_without_refresh_token_is_returned_as_is(
    codex_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = _jwt(time.time() - 60)
    (codex_home / "auth.json").write_text(json.dumps({"access_token": token}), encoding="utf-8")
    monkeypatch.setattr(codex_auth, "_post_json", lambda *a, **k: pytest.fail("refresh should not run"))

    assert codex_auth.get_codex_auth(allow_browser_login=False).access_token == token


def test_flat_auth_file_is_refreshed_in_its_own_format(
    codex_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The format persist_codex_auth() writes after a FactReview browser login.
    path = codex_home / "auth.json"
    path.write_text(
        json.dumps({"access_token": _jwt(time.time() - 60), "refresh_token": "rt-old", "account_id": "a"}),
        encoding="utf-8",
    )
    new_token = _jwt(time.time() + 3600)
    monkeypatch.setattr(
        codex_auth, "_post_json", lambda *a, **k: {"access_token": new_token, "refresh_token": "rt-new"}
    )

    assert codex_auth.get_codex_auth(allow_browser_login=False).access_token == new_token
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["access_token"] == new_token
    assert saved["refresh_token"] == "rt-new"
    assert "tokens" not in saved
