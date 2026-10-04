"""Minimal JSON/HTTP helper built on urllib (no external dependencies)."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode

USER_AGENT = "StreamActivityBot/4.0"

RATE_LIMIT_REASONS = {
    "quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded",
    "dailyLimitExceeded", "too_many_requests",
}
CHAT_UNAVAILABLE_REASONS = {"liveChatEnded", "liveChatDisabled", "liveChatNotFound"}


class ApiError(Exception):
    """Error from an HTTP API.

    kind: auth | rate_limit | network | not_found | chat_unavailable |
          forbidden | config | other
    """

    def __init__(self, message: str, status: int = 0, reason: str = "",
                 retry_after: Optional[float] = None, kind: str = "other"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason
        self.retry_after = retry_after
        self.kind = kind


def _extract_error(body: Any) -> tuple[str, str]:
    """Return (reason, message) from Google / Kick / OAuth style error bodies."""
    reason, message = "", ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            message = str(err.get("message", ""))
            errors = err.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = str(errors[0].get("reason", ""))
            if not reason:
                reason = str(err.get("status", ""))
        elif isinstance(err, str):
            reason = err
            message = str(body.get("error_description") or body.get("message") or err)
        else:
            message = str(body.get("message", ""))
    return reason, message


def _classify(status: int, reason: str) -> str:
    if reason in CHAT_UNAVAILABLE_REASONS:
        return "chat_unavailable"
    if status == 429 or reason in RATE_LIMIT_REASONS:
        return "rate_limit"
    if status == 401:
        return "auth"
    if status == 404:
        return "not_found"
    if status == 403:
        return "forbidden"
    if status >= 500:
        return "network"
    return "other"


def request(method: str, url: str, *, params: Optional[dict] = None,
            headers: Optional[dict] = None, json_body: Any = None,
            form: Optional[dict] = None, timeout: float = 20.0) -> Any:
    """Perform an HTTP request and return parsed JSON ({} for empty body).

    Raises ApiError on network problems and HTTP status >= 400.
    Authorization headers are never included in error messages.
    """
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params, doseq=True)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    resp_headers = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
        resp_headers = e.headers
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        raise ApiError(f"Network error: {getattr(e, 'reason', e)}", kind="network")

    body: Any = {}
    if raw:
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            body = {"message": raw.decode("utf-8", errors="replace")[:200]}

    if status >= 400:
        reason, message = _extract_error(body)
        retry_after = None
        if resp_headers is not None:
            ra = resp_headers.get("Retry-After")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    retry_after = None
        kind = _classify(status, reason)
        text = message or reason or f"HTTP {status}"
        raise ApiError(f"HTTP {status}: {text}"[:300], status=status, reason=reason,
                       retry_after=retry_after, kind=kind)
    return body
