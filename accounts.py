"""Provider configuration and the 'Add account' flow (browser OAuth -> account)."""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from typing import Callable, Optional

import kick_api
import oauth
import youtube_api
from models import PLATFORM_KICK, PLATFORM_YT, Account, new_id
from oauth import OAuthError, Provider

GOOGLE_NOT_CONFIGURED = ("Google OAuth is not configured. Open API / OAuth Setup and select "
                         "your Desktop OAuth client JSON.")
KICK_NOT_CONFIGURED = ("Kick OAuth is not configured. Open API / OAuth Setup and enter your "
                       "Kick Client ID and Client Secret.")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
KICK_AUTH_URL = "https://id.kick.com/oauth/authorize"
KICK_TOKEN_URL = "https://id.kick.com/oauth/token"


def load_google_client(path: str) -> Optional[dict]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    block = doc.get("installed") or doc.get("web")
    if not block or not block.get("client_id"):
        return None
    return block


def provider_problem(platform: str, settings, token_store) -> str:
    """Empty string if configured, otherwise a human-readable reason."""
    if platform == PLATFORM_YT:
        return "" if load_google_client(settings.google_client_json) else GOOGLE_NOT_CONFIGURED
    if platform == PLATFORM_KICK:
        ok = settings.kick_client_id and token_store.get_secret("kick_client_secret")
        return "" if ok else KICK_NOT_CONFIGURED
    return "Unknown platform."


def provider_for(platform: str, settings, token_store) -> Optional[Provider]:
    if provider_problem(platform, settings, token_store):
        return None
    if platform == PLATFORM_YT:
        c = load_google_client(settings.google_client_json)
        return Provider(
            key=PLATFORM_YT, client_id=c["client_id"], client_secret=c.get("client_secret", ""),
            auth_url=c.get("auth_uri") or GOOGLE_AUTH_URL,
            token_url=c.get("token_uri") or GOOGLE_TOKEN_URL,
            scopes=[youtube_api.SCOPE], redirect_host="127.0.0.1", port=0, redirect_path="/",
            # select_account: always show the Google account chooser; consent: guarantees a refresh token
            extra_auth_params={"access_type": "offline", "prompt": "select_account consent"},
            label="YouTube")
    return Provider(
        key=PLATFORM_KICK, client_id=settings.kick_client_id,
        client_secret=token_store.get_secret("kick_client_secret"),
        auth_url=KICK_AUTH_URL, token_url=KICK_TOKEN_URL, scopes=kick_api.SCOPES,
        redirect_host="localhost", port=int(settings.kick_redirect_port), redirect_path="/callback",
        label="Kick")


def kick_redirect_uri(settings) -> str:
    return f"http://localhost:{int(settings.kick_redirect_port)}/callback"


def connect_account(platform: str, config, token_store, *,
                    open_url: Callable[[str], object] = webbrowser.open,
                    cancel: Optional[threading.Event] = None) -> tuple[Account, bool]:
    """Run browser OAuth and store the account. Returns (account, is_new). Blocking."""
    provider = provider_for(platform, config.settings, token_store)
    if provider is None:
        raise OAuthError(provider_problem(platform, config.settings, token_store))
    bundle = oauth.authorize(provider, open_url=open_url, cancel=cancel,
                             timeout=config.settings.oauth_timeout_sec)
    try:
        if platform == PLATFORM_YT:
            external_id, name = youtube_api.fetch_identity(bundle["access_token"])
        else:
            external_id, name = kick_api.fetch_identity(bundle["access_token"])
    except Exception as e:
        raise OAuthError(f"Signed in, but account information could not be read: {e}")

    existing = config.find_account(platform, external_id)
    if existing:                      # reconnect: same account, fresh tokens
        existing.display_name = name
        token_store.set_tokens(existing.id, bundle)
        config.save()
        return existing, False
    acct = Account(id=new_id(), platform=platform, display_name=name, external_id=external_id)
    token_store.set_tokens(acct.id, bundle)
    config.add_account(acct)
    return acct, True
