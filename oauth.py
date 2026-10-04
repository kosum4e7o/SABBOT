"""Generic OAuth 2.x authorization-code + PKCE flow with a localhost callback, plus token refresh."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote, urlencode, urlparse

import http_util
from http_util import ApiError

AUTH_EXPIRED_MSG = "Account authorization expired. Please reconnect this account."
REFRESH_MARGIN_SEC = 120


class OAuthError(Exception):
    pass


@dataclass
class Provider:
    key: str
    client_id: str
    client_secret: str
    auth_url: str
    token_url: str
    scopes: list
    redirect_host: str = "localhost"   # host written into redirect_uri
    port: int = 0                      # 0 = pick a free port
    redirect_path: str = "/callback"
    extra_auth_params: dict = field(default_factory=dict)
    label: str = ""


def make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


_DONE_PAGE = ("<html><head><meta charset='utf-8'><title>Stream Activity Bot</title></head>"
              "<body style='font-family:Segoe UI,Arial,sans-serif;text-align:center;margin-top:15vh;background:#090b14;color:#f1f2ff'>"
              "<h2>{title}</h2><p>{text}</p>"
              "<p id='fallback' style='display:none;color:#aeb5d6'>Ако браузърът не позволи автоматично затваряне, затвори този таб ръчно.</p>"
              "<script>"
              "(function(){{"
              "  function closeTab(){{"
              "    try{{ window.close(); }}catch(e){{}}"
              "    setTimeout(function(){{"
              "      try{{ if(!window.closed) document.getElementById('fallback').style.display='block'; }}catch(e){{}}"
              "    }},500);"
              "  }}"
              "  closeTab();"
              "  setTimeout(closeTab,1200);"
              "}})();"
              "</script></body></html>")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if parsed.path != self.server.expected_path or ("code" not in q and "error" not in q):
            self.send_response(404)
            self.end_headers()
            return
        if self.server.result is None:       # accept only the first valid callback
            self.server.result = q
        ok = "code" in q and "error" not in q
        page = _DONE_PAGE.format(
            title="Authorization complete" if ok else "Authorization failed",
            text="This tab will try to close itself. If it stays open, you can close it and return to Stream Activity Bot."
            if ok else "Return to Stream Activity Bot for details.")
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # never log request lines (they contain the code)
        pass


def _token_bundle(resp: dict, old_refresh: str = "") -> dict:
    access = resp.get("access_token")
    if not access:
        raise OAuthError("Token response did not contain an access token.")
    try:
        expires_in = int(float(resp.get("expires_in", 3600)))
    except (TypeError, ValueError):
        expires_in = 3600
    return {
        "access_token": access,
        "refresh_token": resp.get("refresh_token") or old_refresh,
        "expires_at": time.time() + expires_in,
        "scope": resp.get("scope", ""),
    }


def authorize(provider: Provider, *, open_url: Callable[[str], object] = webbrowser.open,
              cancel: Optional[threading.Event] = None, timeout: float = 240.0) -> dict:
    """Run the browser flow and return a token bundle. Blocking: call from a worker thread."""
    verifier, challenge = make_pkce()
    state = secrets.token_urlsafe(24)
    try:
        server = HTTPServer(("127.0.0.1", provider.port), _Handler)
    except OSError as e:
        raise OAuthError(f"Cannot listen on 127.0.0.1:{provider.port} for the OAuth callback "
                         f"({e.strerror or e}). Close the program using this port or change the "
                         f"redirect port in API / OAuth Setup.")
    server.expected_path = provider.redirect_path
    server.result = None
    server.timeout = 0.5
    try:
        port = server.server_address[1]
        redirect_uri = f"http://{provider.redirect_host}:{port}{provider.redirect_path}"
        params = {"client_id": provider.client_id, "response_type": "code",
                  "redirect_uri": redirect_uri, "scope": " ".join(provider.scopes),
                  "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        params.update(provider.extra_auth_params)
        auth_url = provider.auth_url + "?" + urlencode(params, quote_via=quote)
        open_url(auth_url)

        deadline = time.time() + timeout
        while server.result is None:
            if cancel is not None and cancel.is_set():
                raise OAuthError("Authorization cancelled.")
            if time.time() > deadline:
                raise OAuthError("Authorization timed out. Please try again.")
            server.handle_request()
        result = server.result
    finally:
        server.server_close()

    if "error" in result:
        raise OAuthError(f"Authorization denied or failed: {result.get('error_description') or result['error']}")
    if not hmac.compare_digest(result.get("state", ""), state):
        raise OAuthError("OAuth state mismatch - the response was rejected.")

    try:
        resp = http_util.request("POST", provider.token_url, form={
            "grant_type": "authorization_code", "code": result["code"],
            "client_id": provider.client_id, "client_secret": provider.client_secret,
            "redirect_uri": redirect_uri, "code_verifier": verifier})
    except ApiError as e:
        raise OAuthError(f"Token exchange failed: {e.message}")
    bundle = _token_bundle(resp)
    if not bundle["refresh_token"]:
        raise OAuthError("The provider did not return a refresh token. Remove the app from your "
                         "account's connected apps and try again.")
    return bundle


class TokenManager:
    """Returns valid access tokens, refreshing them automatically and thread-safely."""

    def __init__(self, token_store, provider_getter: Callable[[str], Optional[Provider]]):
        self.store = token_store
        self.provider_getter = provider_getter
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, account_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(account_id, threading.Lock())

    def has_tokens(self, account_id: str) -> bool:
        return self.store.get_tokens(account_id) is not None

    def access_token(self, account, force_refresh: bool = False) -> str:
        tokens = self.store.get_tokens(account.id)
        if not tokens:
            raise ApiError(AUTH_EXPIRED_MSG, kind="auth", reason="missing_tokens")
        if not force_refresh and tokens["expires_at"] - time.time() > REFRESH_MARGIN_SEC:
            return tokens["access_token"]
        with self._lock_for(account.id):
            tokens = self.store.get_tokens(account.id)   # may have been refreshed meanwhile
            if not tokens:
                raise ApiError(AUTH_EXPIRED_MSG, kind="auth", reason="missing_tokens")
            if not force_refresh and tokens["expires_at"] - time.time() > REFRESH_MARGIN_SEC:
                return tokens["access_token"]
            return self._refresh(account, tokens)

    def _refresh(self, account, tokens: dict) -> str:
        provider = self.provider_getter(account.platform)
        if provider is None:
            raise ApiError("OAuth is not configured for this platform. Open API / OAuth Setup.",
                           kind="config")
        if not tokens.get("refresh_token"):
            raise ApiError(AUTH_EXPIRED_MSG, kind="auth", reason="no_refresh_token")
        try:
            resp = http_util.request("POST", provider.token_url, form={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": provider.client_id, "client_secret": provider.client_secret})
        except ApiError as e:
            if e.reason == "invalid_client":
                raise ApiError("OAuth client credentials were rejected. Check API / OAuth Setup.",
                               status=e.status, reason=e.reason, kind="config")
            if e.kind in ("auth", "forbidden") or e.status in (400, 401) or e.reason == "invalid_grant":
                raise ApiError(AUTH_EXPIRED_MSG, status=e.status, reason=e.reason or "invalid_grant",
                               kind="auth")
            raise   # network / rate limit / server error: transient
        bundle = _token_bundle(resp, old_refresh=tokens["refresh_token"])
        self.store.set_tokens(account.id, bundle)
        return bundle["access_token"]
