import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import database
from app.auth import admin_password, admin_username


def test_desktop_sample_file_can_be_sold_once(tmp_path: Path, monkeypatch) -> None:
    sample = Path(r"C:\Users\94015\Desktop\sub2api_automation.json")
    if not sample.exists():
        return

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "cards.db")

    import app.services as services

    monkeypatch.setattr(services, "get_connection", database.get_connection)
    monkeypatch.setattr(services, "transaction", database.transaction)
    database.init_db()

    from app.main import app

    client = TestClient(app)
    login = client.post(
        "/admin/login",
        data={"username": admin_username(), "password": admin_password()},
        follow_redirects=False,
    )
    assert login.status_code == 303

    response = client.post(
        "/admin/upload",
        files={"file": (sample.name, sample.read_bytes(), "application/json")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        "/admin/cards",
        data={"account_count": "1", "card_quantity": "1", "note": "sample"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "已生成 1 张卡密" in parse_qs(urlparse(response.headers["location"]).query)["ok"][0]

    import sqlite3

    conn = sqlite3.connect(tmp_path / "cards.db")
    code = conn.execute("SELECT code FROM cards ORDER BY id LIMIT 1").fetchone()[0]
    conn.close()

    response = client.post("/redeem", data={"code": code})
    assert response.status_code == 200
    assert "可下载账号数量" in response.text

    response = client.post("/redeem/download", data={"code": code, "export_format": "cpa"})
    assert response.status_code == 200
    payload = json.loads(response.content.decode("utf-8"))
    assert isinstance(payload, list)
    assert payload[0]["name"]
    assert payload[0]["type"] == "codex"
    assert "access_token" in payload[0]
    assert "refresh_token" in payload[0]
    assert "id_token" in payload[0]
