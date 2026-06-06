from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import Request
from dotenv import load_dotenv

from .time_utils import shanghai_now_text


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SESSION_COOKIE = "team_card_admin"
SESSION_MAX_AGE = 60 * 60 * 12


def admin_username() -> str:
    return os.getenv("ADMIN_USERNAME", "admin")


def admin_password() -> str:
    return os.getenv("ADMIN_PASSWORD", "admin123456")


def secret_key() -> str:
    return os.getenv("ADMIN_SECRET_KEY", "team-card-web-dev-secret-change-me")


def verify_admin_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(username, admin_username()) and hmac.compare_digest(password, admin_password())


def create_session_cookie(username: str) -> str:
    payload = {
        "username": username,
        "login_at": shanghai_now_text(),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    signature = _sign(body)
    return f"{body}.{signature}"


def current_admin(request: Request) -> str:
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw or "." not in raw:
        return ""
    body, signature = raw.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), signature):
        return ""
    try:
        payload: Any = json.loads(base64.urlsafe_b64decode(_pad_base64(body)).decode())
    except Exception:
        return ""
    username = str(payload.get("username") or "")
    return username if username == admin_username() else ""


def _sign(body: str) -> str:
    digest = hmac.new(secret_key().encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _pad_base64(value: str) -> str:
    return value + ("=" * (-len(value) % 4))
