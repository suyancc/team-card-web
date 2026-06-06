from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass
from math import ceil
from typing import Any

from .database import get_connection, transaction
from .export_formats import create_cpa_export, create_sub2api_export
from .time_utils import SQLITE_SHANGHAI_NOW, shanghai_now_text


CARD_ALPHABET = string.ascii_uppercase + string.digits


class CardError(Exception):
    pass


class UploadError(Exception):
    pass


@dataclass(frozen=True)
class ImportResult:
    batch: str
    imported: int
    names: list[str]


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    page: int
    page_size: int
    total: int
    pages: int
    param: str

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def now_text() -> str:
    return shanghai_now_text()


def parse_accounts_file(raw: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise UploadError("文件必须是 UTF-8 编码的 JSON") from exc
    except json.JSONDecodeError as exc:
        raise UploadError(f"JSON 格式错误：{exc}") from exc

    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        accounts = data["accounts"]
    elif isinstance(data, list):
        accounts = data
    elif isinstance(data, dict) and data.get("name"):
        accounts = [data]
    else:
        raise UploadError("没有找到账号列表：请上传包含 accounts 数组的 JSON 文件")

    parsed: list[dict[str, Any]] = []
    for index, account in enumerate(accounts, start=1):
        if not isinstance(account, dict):
            raise UploadError(f"第 {index} 个账号不是 JSON 对象")
        name = str(account.get("name") or "").strip()
        if not name:
            raise UploadError(f"第 {index} 个账号缺少 name 字段")
        copied = dict(account)
        copied["name"] = name
        parsed.append(copied)

    if not parsed:
        raise UploadError("账号列表为空")
    return parsed


def import_accounts(raw: bytes, filename: str) -> ImportResult:
    accounts = parse_accounts_file(raw)
    batch = f"{now_text()} {filename or 'upload.json'}"

    with transaction() as conn:
        conn.executemany(
            """
            INSERT INTO accounts (name, payload_json, upload_batch)
            VALUES (:name, :payload_json, :upload_batch)
            """,
            [
                {
                    "name": account["name"],
                    "payload_json": json.dumps(account, ensure_ascii=False, indent=2),
                    "upload_batch": batch,
                }
                for account in accounts
            ],
        )

    return ImportResult(batch=batch, imported=len(accounts), names=[a["name"] for a in accounts])


def make_code(length: int = 18) -> str:
    raw = "".join(secrets.choice(CARD_ALPHABET) for _ in range(length))
    return "-".join(raw[i : i + 6] for i in range(0, length, 6))


def create_cards(account_count: int, card_quantity: int, note: str = "") -> dict[str, Any]:
    if account_count <= 0:
        raise CardError("卡密账号数量必须大于 0")
    if card_quantity <= 0:
        raise CardError("生成卡密数量必须大于 0")
    if card_quantity > 1000:
        raise CardError("单次最多生成 1000 张卡密")

    with transaction() as conn:
        total_needed = account_count * card_quantity
        available = conn.execute(
            "SELECT id, name FROM accounts WHERE status = 'available' ORDER BY id LIMIT ?",
            (total_needed,),
        ).fetchall()
        if len(available) < total_needed:
            raise CardError(f"库存不足：需要 {total_needed} 个账号，当前可售 {len(available)} 个")

        batch_code = make_code(12)
        cards: list[dict[str, Any]] = []
        note_text = note.strip()
        for index in range(card_quantity):
            code = make_code()
            while conn.execute("SELECT 1 FROM cards WHERE code = ?", (code,)).fetchone():
                code = make_code()
            cursor = conn.execute(
                """
                INSERT INTO cards (code, account_count, batch_code, note)
                VALUES (?, ?, ?, ?)
                """,
                (code, account_count, batch_code, note_text),
            )
            card_id = cursor.lastrowid
            start = index * account_count
            chunk = available[start : start + account_count]
            conn.executemany(
                f"""
                UPDATE accounts
                SET status = 'reserved', card_id = ?, reserved_at = {SQLITE_SHANGHAI_NOW}
                WHERE id = ? AND status = 'available'
                """,
                [(card_id, row["id"]) for row in chunk],
            )
            cards.append(
                {
                    "id": card_id,
                    "code": code,
                    "account_count": account_count,
                    "accounts": chunk,
                    "note": note_text,
                }
            )

        return {"batch_code": batch_code, "cards": cards}


def create_card(account_count: int, note: str = "") -> dict[str, Any]:
    return create_cards(account_count, 1, note)["cards"][0]


def claim_card(code: str) -> dict[str, Any]:
    normalized = code.strip().upper()
    if not normalized:
        raise CardError("请输入卡密")

    with transaction() as conn:
        card = conn.execute("SELECT * FROM cards WHERE code = ?", (normalized,)).fetchone()
        if not card:
            raise CardError("卡密不存在")
        if card["status"] != "unused":
            raise CardError("卡密已经兑换下载过，不能重复兑换")

        accounts = conn.execute(
            """
            SELECT id, payload_json
            FROM accounts
            WHERE card_id = ? AND status = 'reserved'
            ORDER BY id
            """,
            (card["id"],),
        ).fetchall()
        if len(accounts) != card["account_count"]:
            raise CardError("卡密绑定账号数量异常，请联系管理员")

        conn.execute(
            f"UPDATE cards SET claimed_at = COALESCE(claimed_at, {SQLITE_SHANGHAI_NOW}) WHERE id = ?",
            (card["id"],),
        )
        return {
            "code": normalized,
            "account_count": len(accounts),
            "claimed_at": now_text(),
        }


def download_card(code: str, export_format: str, client_ip: str = "") -> tuple[str, dict[str, Any] | list[dict[str, Any]]]:
    normalized = code.strip().upper()
    format_name = export_format.strip().lower()
    if format_name not in {"sub2api", "cpa"}:
        raise CardError("下载格式不支持")
    if not normalized:
        raise CardError("请输入卡密")

    with transaction() as conn:
        card = conn.execute("SELECT * FROM cards WHERE code = ?", (normalized,)).fetchone()
        if not card:
            raise CardError("卡密不存在")
        if card["status"] != "unused":
            raise CardError("卡密已经兑换下载过，不能重复兑换")

        accounts = conn.execute(
            """
            SELECT id, payload_json
            FROM accounts
            WHERE card_id = ? AND status = 'reserved'
            ORDER BY id
            """,
            (card["id"],),
        ).fetchall()
        if len(accounts) != card["account_count"]:
            raise CardError("卡密绑定账号数量异常，请联系管理员")

        downloaded_at = now_text()
        account_payloads = [json.loads(row["payload_json"]) for row in accounts]
        package = create_sub2api_export(account_payloads) if format_name == "sub2api" else create_cpa_export(account_payloads)

        conn.execute(
            """
            UPDATE cards
            SET status = 'redeemed',
                redeemed_at = ?,
                downloaded_at = ?,
                download_count = download_count + 1
            WHERE id = ?
            """,
            (downloaded_at, downloaded_at, card["id"]),
        )
        conn.execute(
            """
            UPDATE accounts
            SET status = 'redeemed',
                redeemed_at = ?,
                downloaded_at = ?
            WHERE card_id = ? AND status = 'reserved'
            """,
            (downloaded_at, downloaded_at, card["id"]),
        )
        conn.execute(
            """
            INSERT INTO redemptions (card_id, code, format, account_count, client_ip)
            VALUES (?, ?, ?, ?, ?)
            """,
            (card["id"], normalized, format_name, len(account_payloads), client_ip),
        )

    filename = f"{format_name}-{normalized}.json"
    return filename, package


def redeem_card(code: str, client_ip: str = "") -> tuple[str, dict[str, Any]]:
    filename, package = download_card(code, "sub2api", client_ip)
    return filename, package if isinstance(package, dict) else {"accounts": package}


def dashboard_data(cards_page: int = 1, accounts_page: int = 1, redemptions_page: int = 1) -> dict[str, Any]:
    with get_connection() as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM accounts GROUP BY status"
        ).fetchall()
        card_rows = conn.execute("SELECT status, COUNT(*) AS count FROM cards GROUP BY status").fetchall()
        accounts = _page(
            conn,
            """
            SELECT id, name, status, upload_batch, created_at, reserved_at, downloaded_at, card_id
            FROM accounts
            ORDER BY id DESC
            """,
            "SELECT COUNT(*) AS count FROM accounts",
            accounts_page,
            "accounts_page",
        )
        cards = _page(
            conn,
            """
            SELECT c.*,
                   COUNT(a.id) AS bound_accounts
            FROM cards c
            LEFT JOIN accounts a ON a.card_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
            """,
            "SELECT COUNT(*) AS count FROM cards",
            cards_page,
            "cards_page",
        )
        redemptions = _page(
            conn,
            """
            SELECT *
            FROM redemptions
            ORDER BY id DESC
            """,
            "SELECT COUNT(*) AS count FROM redemptions",
            redemptions_page,
            "redemptions_page",
        )

    account_counts = {row["status"]: row["count"] for row in status_rows}
    card_counts = {row["status"]: row["count"] for row in card_rows}
    return {
        "stats": {
            "available_accounts": account_counts.get("available", 0),
            "reserved_accounts": account_counts.get("reserved", 0),
            "redeemed_accounts": account_counts.get("redeemed", 0),
            "unused_cards": card_counts.get("unused", 0),
            "redeemed_cards": card_counts.get("redeemed", 0),
        },
        "accounts_page": accounts,
        "cards_page": cards,
        "redemptions_page": redemptions,
    }


def cards_by_batch(batch_code: str) -> list[dict[str, Any]]:
    if not batch_code:
        return []
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT code, account_count, note, created_at
            FROM cards
            WHERE batch_code = ?
            ORDER BY id
            """,
            (batch_code,),
        ).fetchall()


def _page(conn, item_sql: str, count_sql: str, page: int, param: str, page_size: int = 20) -> Page:
    current_page = max(1, page)
    total = int(conn.execute(count_sql).fetchone()["count"])
    pages = max(1, ceil(total / page_size))
    current_page = min(current_page, pages)
    offset = (current_page - 1) * page_size
    items = conn.execute(f"{item_sql} LIMIT ? OFFSET ?", (page_size, offset)).fetchall()
    return Page(items=items, page=current_page, page_size=page_size, total=total, pages=pages, param=param)
