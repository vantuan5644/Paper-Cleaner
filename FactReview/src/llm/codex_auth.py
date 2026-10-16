from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

# Public OAuth client id of the Codex CLI. Tokens written by `codex login` can
# only be refreshed with the client id that issued them.
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"
# Refresh slightly early so a token doesn't expire mid-request.
_REFRESH_SKEW_SEC = 60
# Refresh tokens rotate on use; serialise refreshes so parallel callers don't
# spend the same refresh token twice (the second attempt gets "reused").
_REFRESH_LOCK = threading.Lock()


def _is_truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    raw = (data or "").strip()
    if not raw:
        return b""
    raw += "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw.encode("ascii"))


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}
    try:
        return json.loads(_b64url_decode(parts[1]).decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def _pick_account_id(data: dict[str, Any], token: str) -> str | None:
    for key in ("account_id", "accountId", "aid"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    payload = _decode_jwt_payload(token)
    for key in ("account_id", "accountId", "aid"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class CodexAuth:
    access_token: str
    account_id: str | None = None
    source: str = ""


def _safe_json_load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _candidate_cache_files() -> list[Path]:
    candidates: list[Path] = []
    project_root = Path(__file__).resolve().parents[2]

    # Allow local project-scoped auth caches for development.
    candidates.append(project_root / "auth-profiles.json")
    candidates.append(project_root / "auth.json")

    codex_home = (os.getenv("CODEX_HOME") or "").strip()
    if codex_home:
        candidates.append(Path(codex_home) / "auth.json")

    home = Path.home()
    candidates.append(home / ".codex" / "auth.json")
    candidates.append(home / ".clawdbot" / "agents" / "main" / "agent" / "auth-profiles.json")

    openclaw_root = Path((os.getenv("OPENCLAW_STATE_DIR") or "").strip() or (home / ".openclaw"))
    agents_dir = openclaw_root / "agents"
    if agents_dir.exists():
        try:
            for agent_dir in agents_dir.iterdir():
                if not agent_dir.is_dir():
                    continue
                candidates.append(agent_dir / "agent" / "auth-profiles.json")
                candidates.append(agent_dir / "agent" / "auth.json")
                candidates.append(agent_dir / "agent" / "auth" / "auth-profiles.json")
                candidates.append(agent_dir / "agent" / "auth" / "auth.json")
        except Exception:
            pass

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _extract_from_auth_json(path: Path) -> CodexAuth | None:
    data = _safe_json_load(path)
    if not isinstance(data, dict):
        return None

    token_candidates = [
        data.get("access_token"),
        data.get("token"),
        (data.get("auth") or {}).get("access_token") if isinstance(data.get("auth"), dict) else None,
        (data.get("auth") or {}).get("token") if isinstance(data.get("auth"), dict) else None,
        (data.get("credentials") or {}).get("access_token")
        if isinstance(data.get("credentials"), dict)
        else None,
        (data.get("credentials") or {}).get("token") if isinstance(data.get("credentials"), dict) else None,
        (data.get("session") or {}).get("access_token") if isinstance(data.get("session"), dict) else None,
        (data.get("session") or {}).get("token") if isinstance(data.get("session"), dict) else None,
    ]

    for candidate in token_candidates:
        if isinstance(candidate, str) and candidate.strip():
            token = candidate.strip()
            return CodexAuth(
                access_token=token,
                account_id=_pick_account_id(data, token),
                source=str(path),
            )

    # Some tools store provider-named blocks under auth.json, e.g.:
    # {"openai-codex": {"access": "...", "accountId": "..."}}
    for item in _iter_dicts(data):
        token = item.get("access") or item.get("access_token") or item.get("token")
        if isinstance(token, str) and token.strip():
            token = token.strip()
            return CodexAuth(
                access_token=token,
                account_id=_pick_account_id(item, token),
                source=str(path),
            )
    return None


def _iter_dicts(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_dicts(value)


def _extract_from_profiles(path: Path) -> CodexAuth | None:
    data = _safe_json_load(path)
    if not isinstance(data, dict):
        return None

    profiles = data.get("profiles")
    profile_dicts: list[dict[str, Any]] = []
    if isinstance(profiles, dict):
        preferred_keys = ("openai-codex:default", "openai-codex", "codex")
        for key in preferred_keys:
            value = profiles.get(key)
            if isinstance(value, dict):
                profile_dicts.append(value)
        for value in profiles.values():
            if isinstance(value, dict):
                profile_dicts.append(value)
    else:
        profile_dicts.extend(_iter_dicts(data))

    seen_tokens: set[str] = set()
    for item in profile_dicts:
        token = item.get("access") or item.get("access_token") or item.get("token")
        if not isinstance(token, str) or not token.strip():
            continue
        token = token.strip()
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        return CodexAuth(
            access_token=token,
            account_id=_pick_account_id(item, token),
            source=str(path),
        )
    return None


def load_cached_codex_auth() -> CodexAuth | None:
    env_token = (os.getenv("OPENAI_CODEX_ACCESS_TOKEN") or os.getenv("OPENAI_CODEX_TOKEN") or "").strip()
    if env_token:
        return CodexAuth(
            access_token=env_token,
            account_id=(os.getenv("OPENAI_CODEX_ACCOUNT_ID") or "").strip() or None,
            source="env",
        )

    for path in _candidate_cache_files():
        if not path.exists():
            continue
        if path.name == "auth.json":
            auth = _extract_from_auth_json(path)
        else:
            auth = _extract_from_profiles(path)
        if auth:
            return auth
    return None


def _post_form(url: str, payload: dict[str, str], timeout_sec: int = 20) -> dict[str, Any]:
    body = urllib.parse.urlencode({k: v for k, v in payload.items() if v is not None}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return json.loads(raw) if raw.strip() else {}


def _post_json(url: str, payload: dict[str, str], timeout_sec: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    return json.loads(raw) if raw.strip() else {}


def _token_expired(token: str) -> bool:
    """True when the JWT `exp` claim has passed. Opaque tokens count as valid."""
    exp = _decode_jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return False
    return exp - _REFRESH_SKEW_SEC <= time.time()


def _with_refreshed_tokens(data: dict[str, Any], token_data: dict[str, Any]) -> dict[str, Any] | None:
    """Return a copy of an auth.json dict with rotated tokens, in its own layout.

    Handles the Codex CLI layout (`{"tokens": {...}, "last_refresh": ...}`) and
    the flat layout persist_codex_auth() writes. None if neither has a refresh token.
    """
    rotated = {
        k: v for k, v in token_data.items() if k in ("id_token", "access_token", "refresh_token") and v
    }
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and tokens.get("refresh_token"):
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {**data, "tokens": {**tokens, **rotated}, "last_refresh": now}
    if data.get("refresh_token"):
        return {**data, **rotated, "updated_at": int(time.time())}
    return None


def _refresh_token_of(data: dict[str, Any]) -> str:
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and tokens.get("refresh_token"):
        return str(tokens["refresh_token"])
    return str(data.get("refresh_token") or "")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def refresh_codex_auth_if_expired(auth: CodexAuth) -> CodexAuth:
    """Refresh an expired cached token with its refresh token and write it back.

    Returns `auth` unchanged when it is still valid, came from the environment,
    or its cache file has no refresh token. Raises RuntimeError if the refresh
    request is rejected, since the cached token is unusable at that point.
    """
    if not _token_expired(auth.access_token) or auth.source in ("", "env"):
        return auth

    path = Path(auth.source)
    with _REFRESH_LOCK:
        # Another thread (or the Codex CLI) may have refreshed it meanwhile.
        data = _safe_json_load(path)
        if not isinstance(data, dict):
            return auth
        current = _extract_from_auth_json(path) if path.name == "auth.json" else _extract_from_profiles(path)
        if current and not _token_expired(current.access_token):
            return current

        refresh_token = _refresh_token_of(data)
        if not refresh_token or _with_refreshed_tokens(data, {}) is None:
            return auth

        try:
            token_data = _post_json(
                _CODEX_REFRESH_URL,
                {
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
            )
        except Exception as exc:
            raise RuntimeError(
                f"Codex access token in {path} expired and refreshing it failed ({exc}). Run `codex login`."
            ) from exc

        access_token = str(token_data.get("access_token") or "").strip()
        updated = _with_refreshed_tokens(data, token_data)
        if not access_token or updated is None:
            raise RuntimeError(f"Codex token refresh returned no access_token. Run `codex login`. ({path})")
        _write_json_atomic(path, updated)

    return CodexAuth(
        access_token=access_token,
        account_id=auth.account_id or _pick_account_id(token_data, access_token),
        source=auth.source,
    )


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = _b64url_encode(secrets.token_bytes(32))
    challenge = _b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _build_authorization_url(
    auth_url: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in auth_url else "?"
    return auth_url + sep + urllib.parse.urlencode({k: v for k, v in query.items() if v})


def _wait_for_loopback_code(redirect_uri: str, expected_state: str, timeout_sec: int = 180) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 1455)
    path = parsed.path or "/auth/callback"

    result: dict[str, str] = {"code": "", "state": "", "error": ""}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not (self.path or "").startswith(path):
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return

            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query or "")
            result["code"] = str((query.get("code") or [""])[0])
            result["state"] = str((query.get("state") or [""])[0])
            result["error"] = str((query.get("error") or [""])[0])

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if result["error"]:
                self.wfile.write(f"OpenAI Codex login failed: {result['error']}\n".encode())
            else:
                self.wfile.write(b"OpenAI Codex login succeeded. You can close this tab.\n")
            done.set()

        def log_message(self, format, *args):
            return

    server = HTTPServer((host, port), Handler)

    def serve() -> None:
        try:
            server.timeout = 1
            deadline = time.time() + float(timeout_sec)
            while time.time() < deadline and not done.is_set():
                server.handle_request()
        finally:
            try:
                server.server_close()
            except Exception:
                pass
            done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    done.wait(timeout=float(timeout_sec))

    if result["error"]:
        raise RuntimeError(f"codex_login_error: {result['error']}")
    if not result["code"]:
        raise TimeoutError("codex_login_timeout")
    if result["state"] != expected_state:
        raise RuntimeError("codex_login_state_mismatch")
    return result["code"]


def _exchange_code_for_token(
    token_url: str,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    client_secret: str,
) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret
    return _post_form(token_url, payload)


def persist_codex_auth(
    auth: CodexAuth,
    *,
    refresh_token: str = "",
    target_path: Path | None = None,
) -> Path:
    path = target_path or (
        Path((os.getenv("CODEX_HOME") or "").strip() or (Path.home() / ".codex")) / "auth.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": auth.access_token,
        "account_id": auth.account_id,
        "refresh_token": refresh_token or "",
        "source": auth.source or "browser-login",
        "updated_at": int(time.time()),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def login_with_browser(timeout_sec: int = 180) -> CodexAuth:
    client_id = (os.getenv("OPENAI_CODEX_CLIENT_ID") or "").strip()
    if not client_id:
        raise RuntimeError(
            "OPENAI_CODEX_CLIENT_ID is required for browser login. "
            "If you already signed in elsewhere, run again after creating ~/.codex/auth.json or ~/.clawdbot/... auth cache."
        )

    auth_url = (os.getenv("OPENAI_CODEX_AUTH_URL") or "https://auth.openai.com/oauth/authorize").strip()
    token_url = (os.getenv("OPENAI_CODEX_TOKEN_URL") or "https://auth.openai.com/oauth/token").strip()
    redirect_uri = (os.getenv("OPENAI_CODEX_REDIRECT_URI") or "http://127.0.0.1:1455/auth/callback").strip()
    scope = (os.getenv("OPENAI_CODEX_SCOPE") or "").strip()
    client_secret = (os.getenv("OPENAI_CODEX_CLIENT_SECRET") or "").strip()
    no_browser = _is_truthy(os.getenv("OPENAI_CODEX_NO_BROWSER") or "")

    verifier, challenge = _generate_pkce_pair()
    state = _b64url_encode(secrets.token_bytes(16))
    url = _build_authorization_url(auth_url, client_id, redirect_uri, scope, state, challenge)

    print(f"[codex] Open this URL to sign in:\n{url}\n")
    if not no_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    code = _wait_for_loopback_code(redirect_uri=redirect_uri, expected_state=state, timeout_sec=timeout_sec)
    token_data = _exchange_code_for_token(
        token_url=token_url,
        client_id=client_id,
        code=code,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        client_secret=client_secret,
    )
    access_token = str(token_data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(
            f"Codex login did not return access_token. Response keys: {sorted(token_data.keys())}"
        )

    auth = CodexAuth(
        access_token=access_token,
        account_id=_pick_account_id(token_data, access_token),
        source="browser-login",
    )
    persist_codex_auth(auth, refresh_token=str(token_data.get("refresh_token") or "").strip())
    return auth


def get_codex_auth(*, allow_browser_login: bool = True) -> CodexAuth:
    auth = load_cached_codex_auth()
    if auth:
        return refresh_codex_auth_if_expired(auth)

    if not allow_browser_login:
        raise FileNotFoundError(
            "No cached OpenAI Codex login found. Looked for ~/.codex/auth.json, ~/.clawdbot/... and ~/.openclaw/...."
        )

    if not sys.stdin.isatty():
        raise FileNotFoundError(
            "No cached OpenAI Codex login found, and interactive browser login is unavailable in a non-interactive session."
        )

    return login_with_browser()
