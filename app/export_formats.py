from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from .time_utils import shanghai_now_iso


def create_sub2api_export(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "exported_at": shanghai_now_iso(),
        "proxies": [],
        "accounts": [_create_sub2api_account(account) for account in accounts],
    }


def create_cpa_export(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_create_cpa_account(account) for account in accounts]


def _create_sub2api_account(account: dict[str, Any]) -> dict[str, Any]:
    credentials = _credentials(account)
    extra = _extra(account)
    access_token = _string(credentials.get("access_token"))
    email = _first_string(credentials.get("email"), extra.get("email"), account.get("name"), _extract_email(access_token))
    account_id = _first_string(
        credentials.get("account_id"),
        credentials.get("chatgpt_account_id"),
        _extract_account_id(access_token),
    )
    chatgpt_user_id = _first_string(credentials.get("chatgpt_user_id"), _extract_user_id(access_token))
    expires_at = _first_string(credentials.get("expires_at"), credentials.get("expired"), _extract_expiry_iso(access_token))
    plan_type = _normalize_plan_type(_first_string(credentials.get("plan_type"), account.get("plan_type")))

    account_credentials = _strip_empty(
        {
            "access_token": access_token,
            "chatgpt_account_id": account_id,
            "chatgpt_user_id": chatgpt_user_id,
            "email": email,
            "expires_at": expires_at,
            "expires_in": _expires_in_seconds(expires_at),
            "plan_type": plan_type,
        }
    )
    account_credentials["refresh_token"] = _string(credentials.get("refresh_token"))
    account_credentials["id_token"] = _string(credentials.get("id_token"))

    merged_extra = _strip_empty(
        {
            **extra,
            "email": email,
            "email_key": _email_key(email),
            "name": email or account.get("name") or "ChatGPT Account",
            "auth_provider": extra.get("auth_provider") or "oauth",
            "source": extra.get("source") or credentials.get("source") or "openai-plus-vxt-oauth",
            "last_refresh": extra.get("last_refresh") or credentials.get("last_refresh") or shanghai_now_iso(),
        }
    )

    result = {
        **account,
        "name": email or account.get("name") or "ChatGPT Account",
        "platform": account.get("platform") or "openai",
        "type": account.get("type") or "oauth",
        "concurrency": account.get("concurrency", 10),
        "priority": account.get("priority", 1),
        "credentials": account_credentials,
        "extra": merged_extra,
    }
    return result


def _create_cpa_account(account: dict[str, Any]) -> dict[str, Any]:
    credentials = _credentials(account)
    extra = _extra(account)
    access_token = _string(credentials.get("access_token"))
    email = _first_string(credentials.get("email"), extra.get("email"), account.get("name"), _extract_email(access_token))
    account_id = _first_string(
        credentials.get("account_id"),
        credentials.get("chatgpt_account_id"),
        _extract_account_id(access_token),
    )
    chatgpt_user_id = _first_string(credentials.get("chatgpt_user_id"), _extract_user_id(access_token))
    plan_type = _normalize_plan_type(_first_string(credentials.get("plan_type"), account.get("plan_type")))
    item = {
        "type": "codex",
        "account_id": account_id,
        "chatgpt_account_id": account_id,
        "email": email,
        "name": email,
        "plan_type": plan_type,
        "chatgpt_plan_type": plan_type,
        "id_token": _string(credentials.get("id_token")),
        "access_token": access_token,
        "refresh_token": _string(credentials.get("refresh_token")),
        "session_token": _string(credentials.get("session_token")),
        "last_refresh": _first_string(credentials.get("last_refresh"), extra.get("last_refresh"), shanghai_now_iso()),
        "expired": _first_string(credentials.get("expired"), credentials.get("expires_at"), _extract_expiry_iso(access_token)),
    }
    if credentials.get("id_token_synthetic"):
        item["id_token_synthetic"] = True
    if chatgpt_user_id:
        item["chatgpt_user_id"] = chatgpt_user_id
    return item


def _credentials(account: dict[str, Any]) -> dict[str, Any]:
    value = account.get("credentials")
    return value if isinstance(value, dict) else account


def _extra(account: dict[str, Any]) -> dict[str, Any]:
    value = account.get("extra")
    return value if isinstance(value, dict) else {}


def _strip_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "")}


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _first_string(*values: Any) -> str:
    for value in values:
        text = _string(value).strip()
        if text:
            return text
    return ""


def _normalize_plan_type(value: Any) -> str:
    plan_type = _string(value).strip().lower()
    return "plus" if not plan_type or plan_type == "free" else plan_type


def _email_key(email: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in email).strip("_")


def _extract_jwt_payload(token: str) -> dict[str, Any]:
    part = token.split(".")[1] if "." in token else ""
    if not part:
        return {}
    try:
        raw = base64.urlsafe_b64decode(part + ("=" * (-len(part) % 4)))
        value = json.loads(raw.decode())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _extract_account_id(access_token: str) -> str:
    auth = _extract_jwt_payload(access_token).get("https://api.openai.com/auth")
    return _string(auth.get("chatgpt_account_id")) if isinstance(auth, dict) else ""


def _extract_user_id(access_token: str) -> str:
    auth = _extract_jwt_payload(access_token).get("https://api.openai.com/auth")
    if not isinstance(auth, dict):
        return ""
    return _first_string(auth.get("chatgpt_account_user_id"), auth.get("chatgpt_user_id"), auth.get("user_id"))


def _extract_email(access_token: str) -> str:
    payload = _extract_jwt_payload(access_token)
    profile = payload.get("https://api.openai.com/profile")
    if isinstance(profile, dict) and profile.get("email"):
        return _string(profile.get("email"))
    return _string(payload.get("email"))


def _extract_expiry_iso(access_token: str) -> str:
    exp = _extract_jwt_payload(access_token).get("exp")
    try:
        timestamp = int(exp)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _expires_in_seconds(expires_at: str) -> int:
    if not expires_at:
        return 0
    try:
        normalized = expires_at.replace("Z", "+00:00")
        target = datetime.fromisoformat(normalized)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
    except ValueError:
        return 0
    return max(0, int((target - datetime.now(timezone.utc)).total_seconds()))
