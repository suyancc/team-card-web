from __future__ import annotations

import json
from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import SESSION_COOKIE, SESSION_MAX_AGE, create_session_cookie, current_admin, verify_admin_credentials
from .database import BASE_DIR, init_db
from .services import (
    CardError,
    UploadError,
    cards_by_batch,
    claim_card,
    create_cards,
    dashboard_data,
    download_card,
    import_accounts,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Team Card Web", lifespan=lifespan)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def redirect_with_message(path: str, message: str, level: str = "ok") -> RedirectResponse:
    return RedirectResponse(f"{path}?{urlencode({level: message})}", status_code=303)


def admin_redirect() -> RedirectResponse:
    return RedirectResponse("/admin/login", status_code=303)


def require_admin(request: Request) -> str:
    username = current_admin(request)
    if not username:
        return ""
    return username


@app.get("/", response_class=HTMLResponse)
def redeem_page(request: Request, error: str = "", ok: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "redeem.html",
        {"error": error, "ok": ok},
    )


@app.post("/redeem")
async def redeem(request: Request, code: str = Form(...)) -> HTMLResponse:
    try:
        claim = claim_card(code)
    except CardError as exc:
        return templates.TemplateResponse(
            request,
            "redeem.html",
            {"error": str(exc), "ok": "", "last_code": code},
            status_code=400,
        )

    return templates.TemplateResponse(
        request,
        "redeem.html",
        {"error": "", "ok": "", "last_code": claim["code"], "claim": claim},
    )


@app.post("/redeem/download")
async def redeem_download(request: Request, code: str = Form(...), export_format: str = Form(...)) -> Response:
    try:
        filename, package = download_card(code, export_format, request.client.host if request.client else "")
    except CardError as exc:
        return templates.TemplateResponse(
            request,
            "redeem.html",
            {"error": str(exc), "ok": "", "last_code": code},
            status_code=400,
        )
    content = json.dumps(package, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "") -> HTMLResponse:
    if current_admin(request):
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/admin/login")
def login(username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    if not verify_admin_credentials(username, password):
        return redirect_with_message("/admin/login", "账号或密码错误", "error")
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_cookie(username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/admin/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    error: str = "",
    ok: str = "",
    cards_page: int = 1,
    accounts_page: int = 1,
    redemptions_page: int = 1,
    generated_batch: str = "",
):
    username = require_admin(request)
    if not username:
        return admin_redirect()
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "error": error,
            "ok": ok,
            "admin_username": username,
            "generated_codes": cards_by_batch(generated_batch),
            **dashboard_data(cards_page, accounts_page, redemptions_page),
        },
    )


@app.post("/admin/upload")
async def upload_accounts(request: Request, file: UploadFile = File(...)) -> RedirectResponse:
    if not require_admin(request):
        return admin_redirect()
    raw = await file.read()
    try:
        result = import_accounts(raw, file.filename or "upload.json")
    except UploadError as exc:
        return redirect_with_message("/admin", str(exc), "error")
    return redirect_with_message("/admin", f"已导入 {result.imported} 个账号", "ok")


@app.post("/admin/cards")
def generate_card(
    request: Request,
    account_count: int = Form(...),
    card_quantity: int = Form(...),
    note: str = Form(""),
) -> RedirectResponse:
    if not require_admin(request):
        return admin_redirect()
    try:
        result = create_cards(account_count, card_quantity, note)
    except CardError as exc:
        return redirect_with_message("/admin", str(exc), "error")
    query = urlencode(
        {
            "ok": f"已生成 {len(result['cards'])} 张卡密",
            "generated_batch": result["batch_code"],
        }
    )
    return RedirectResponse(f"/admin?{query}", status_code=303)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
