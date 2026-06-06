import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import database
from app.auth import admin_password, admin_username
from app.main import app


def test_upload_generate_and_redeem(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cards.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    import app.services as services

    monkeypatch.setattr(services, "get_connection", database.get_connection)
    monkeypatch.setattr(services, "transaction", database.transaction)
    database.init_db()

    client = TestClient(app)
    login = client.post(
        "/admin/login",
        data={"username": admin_username(), "password": admin_password()},
        follow_redirects=False,
    )
    assert login.status_code == 303
    payload = {
        "accounts": [
            {"name": "a@example.com", "credentials": {"token": "one"}},
            {"name": "b@example.com", "credentials": {"token": "two"}},
            {"name": "c@example.com", "credentials": {"token": "three"}},
            {"name": "d@example.com", "credentials": {"token": "four"}},
        ]
    }

    response = client.post(
        "/admin/upload",
        files={"file": ("accounts.json", json.dumps(payload).encode("utf-8"), "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        "/admin/cards",
        data={"account_count": "2", "card_quantity": "2", "note": "test"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    message = parse_qs(urlparse(location).query)["ok"][0]
    assert "已生成 2 张卡密" in message

    admin = client.get(location)
    assert admin.status_code == 200
    assert "复制全部" in admin.text

    import sqlite3

    conn = sqlite3.connect(db_path)
    code = conn.execute("SELECT code FROM cards ORDER BY id LIMIT 1").fetchone()[0]
    conn.close()

    response = client.post("/redeem", data={"code": code})
    assert response.status_code == 200
    assert "可下载账号数量" in response.text
    assert ">2<" in response.text

    response = client.post("/redeem/download", data={"code": code, "export_format": "sub2api"})
    assert response.status_code == 200
    downloaded = response.json()
    assert set(downloaded.keys()) == {"exported_at", "proxies", "accounts"}
    assert len(downloaded["accounts"]) == 2
    assert [account["name"] for account in downloaded["accounts"]] == ["a@example.com", "b@example.com"]
    assert "refresh_token" in downloaded["accounts"][0]["credentials"]
    assert "id_token" in downloaded["accounts"][0]["credentials"]

    second = client.post("/redeem/download", data={"code": code, "export_format": "cpa"})
    assert second.status_code == 400
    assert "不能重复兑换" in second.text

    admin_after_download = client.get("/admin")
    assert admin_after_download.status_code == 200
    assert code in admin_after_download.text
    assert "sub2api" in admin_after_download.text


def test_admin_requires_login(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "cards.db")
    import app.services as services

    monkeypatch.setattr(services, "get_connection", database.get_connection)
    monkeypatch.setattr(services, "transaction", database.transaction)
    database.init_db()

    client = TestClient(app)
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"
