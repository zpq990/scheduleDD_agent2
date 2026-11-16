import asyncio
import base64
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import APP_ACCESS_CODE, APP_PORT, DAILY_EMAIL_ENABLED, DAILY_EMAIL_TIME, SMTP_HOST
from .db import (
    AuthError,
    authenticate,
    create_session,
    create_user,
    current_user,
    get_conn,
    get_document,
    get_setting,
    get_user,
    init_db,
    list_users,
    now_iso,
    require_user,
    revoke_session,
    search_document,
    session_user,
    set_setting,
    user_context,
)
from .services import agent, brief, importer, schedule, schedule_extract
from .services.ai import (
    AIUnavailable,
    extract_basic,
    extract_contact,
    extract_schedule_item,
    local_ai_ready,
    ocr_image,
    transcribe_audio,
)
from .services.documents import (
    ConflictError,
    ValidationError,
    commit,
    diff_documents,
    get_version,
    list_versions,
    manual_save,
    restore_version,
)

BASE_DIR = __file__.rsplit("app", 1)[0]
STATIC_DIR = __file__.rsplit("app", 1)[0] + "app/static"


@asynccontextmanager
async def lifespan(app):
    init_db()
    asyncio.create_task(_email_scheduler())
    await _email_catchup()
    yield


app = FastAPI(title="行程咚咚 Agent", lifespan=lifespan)


SESSION_COOKIE = "dd_session"
PUBLIC_API = {"/api/health", "/api/auth/login", "/api/auth/register", "/api/auth/logout"}


def _set_session_cookie(response, request, token):
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        user = session_user(request.cookies.get(SESSION_COOKIE))
        if not user:
            return JSONResponse({"error": "请先登录", "code": "AUTH_REQUIRED"}, status_code=401)
        if APP_ACCESS_CODE:
            code = request.headers.get("x-access-code") or request.query_params.get("code")
            if code != APP_ACCESS_CODE:
                return JSONResponse({"error": "需要访问码", "code": "ACCESS_CODE"}, status_code=401)
        token = current_user.set(user["id"])
        try:
            return await call_next(request)
        finally:
            current_user.reset(token)
    return await call_next(request)


@app.post("/api/auth/register")
async def auth_register(request: Request, payload: dict = Body(...)):
    try:
        user = create_user(str(payload.get("username") or ""), str(payload.get("password") or ""))
    except ValueError as exc:
        return JSONResponse({"error": str(exc), "code": "REGISTER_FAILED"}, status_code=400)
    response = JSONResponse({"user": user})
    return _set_session_cookie(response, request, create_session(user["id"]))


@app.post("/api/auth/login")
async def auth_login(request: Request, payload: dict = Body(...)):
    user = authenticate(str(payload.get("username") or ""), str(payload.get("password") or ""))
    if not user:
        return JSONResponse({"error": "账号或密码不正确", "code": "INVALID_CREDENTIALS"}, status_code=401)
    response = JSONResponse({"user": user})
    return _set_session_cookie(response, request, create_session(user["id"]))


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    revoke_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me():
    return {"user": get_user(require_user())}


@app.exception_handler(ConflictError)
async def conflict_handler(_request, exc: ConflictError):
    return JSONResponse(
        {
            "error": "版本已变化，请对比后重试",
            "code": "VERSION_CONFLICT",
            "current": exc.current,
            "incoming": exc.incoming,
            "diff": diff_documents(exc.current, exc.incoming),
        },
        status_code=409,
    )


@app.exception_handler(ValidationError)
async def validation_handler(_request, exc: ValidationError):
    return JSONResponse({"error": str(exc), "code": "VALIDATION"}, status_code=400)


@app.exception_handler(AIUnavailable)
async def ai_handler(_request, exc: AIUnavailable):
    return JSONResponse({"error": str(exc), "code": "AI_UNAVAILABLE"}, status_code=503)


@app.exception_handler(AuthError)
async def auth_handler(_request, exc: AuthError):
    return JSONResponse({"error": str(exc), "code": "AUTH_REQUIRED"}, status_code=401)


@app.exception_handler(ValueError)
async def value_handler(_request, exc: ValueError):
    return JSONResponse({"error": str(exc), "code": "BAD_INPUT"}, status_code=400)


@app.exception_handler(KeyError)
async def key_handler(_request, exc: KeyError):
    return JSONResponse({"error": f"资源不存在：{exc.args[0] if exc.args else ''}", "code": "NOT_FOUND"}, status_code=404)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/demo/load")
async def api_demo_load():
    from .config import DEFAULT_FIELDS

    today = date.today()
    fields = [
        *DEFAULT_FIELDS,
        {"key": "company", "label": "公司", "type": "text", "required": False, "builtin": False, "hidden": False, "width": 22},
        {"key": "job", "label": "职务", "type": "text", "required": False, "builtin": False, "hidden": False, "width": 16},
        {"key": "follow", "label": "跟进日期", "type": "date", "required": False, "builtin": False, "hidden": False, "width": 18},
    ]
    rows = [
        {
            "id": "r_demo_wang",
            "person": "王建国",
            "contact": "13800138000",
            "address": "浦东新区世纪大道88号",
            "note": "季度补货，带样品",
            "company": "华信商贸有限公司",
            "job": "采购总监",
            "follow": (today + timedelta(days=1)).isoformat() + " 15:00",
        },
        {
            "id": "r_demo_li",
            "person": "李慧",
            "contact": "13900139000",
            "address": "徐汇区漕溪北路88号",
            "note": "行业沙龙后跟进",
            "company": "恒达电子科技",
            "job": "市场经理",
            "follow": (today + timedelta(days=2)).isoformat() + " 10:00",
        },
        {
            "id": "r_demo_chen",
            "person": "陈明",
            "contact": "13700137000",
            "address": "静安区南京西路100号",
            "note": "送样品",
            "company": "新禾食品",
            "job": "采购主管",
            "follow": (today + timedelta(days=1)).isoformat() + " 17:30",
        },
    ]
    current = get_document("basic")
    commit("basic", {"fields": fields, "rows": rows, "meta": {}}, current["version"], "载入示例")
    start = today - timedelta(days=(today.weekday() + 1) % 7)
    sched = get_document("schedule")
    generated = schedule.build_schedule(start.isoformat(), 2, "replace")
    commit(
        "schedule",
        {"rows": generated["items"], "meta": {**sched["meta"], "startDate": start.isoformat(), "weeks": 2}},
        sched["version"],
        "载入示例",
    )
    return {"ok": True, "basic": get_document("basic"), "schedule": get_document("schedule"), "pending": generated.get("pending", [])}


@app.get("/api/status")
async def status():
    email = brief.public_email_settings()
    return {
        "ok": True,
        "version": "0.1.0",
        "today": date.today().isoformat(),
        "localAI": local_ai_ready(),
        "smtpConfigured": bool(brief.smtp_config()),
        "email": email,
        "scheduleStart": get_document("schedule")["meta"].get("startDate", ""),
    }


@app.get("/api/documents/{kind}")
async def api_get_document(kind: str):
    return get_document(kind)


@app.patch("/api/documents/{kind}")
async def api_patch_document(kind: str, payload: dict = Body(...)):
    doc = get_document(kind)
    base_version = payload.get("baseVersion", doc["version"])
    return commit(
        kind,
        {
            "fields": payload.get("fields", doc["fields"]),
            "rows": payload.get("rows", doc["rows"]),
            "meta": payload.get("meta", doc["meta"]),
        },
        base_version=base_version,
        reason=payload.get("reason") or "自动保存",
    )


@app.post("/api/documents/{kind}/save")
async def api_manual_save(kind: str, payload: dict = Body(default={})):
    return manual_save(kind, payload.get("note") or "手动保存")


@app.get("/api/documents/{kind}/versions")
async def api_versions(kind: str):
    return {"versions": list_versions(kind)}


@app.get("/api/documents/{kind}/versions/{version}")
async def api_version(kind: str, version: int):
    return get_version(kind, version)


@app.post("/api/documents/{kind}/versions/{version}/restore")
async def api_restore(kind: str, version: int):
    return restore_version(kind, version)


@app.get("/api/documents/{kind}/search")
async def api_search(kind: str, q: str = "", field: str = "", limit: int = 50, offset: int = 0):
    return search_document(kind, q, field or None, min(200, max(1, limit)), max(0, offset))


@app.get("/api/documents/{kind}/export")
async def api_export(kind: str):
    if kind == "basic":
        doc = get_document("basic")
        content = schedule.export_basic_xlsx(doc["fields"], doc["rows"])
        filename = "基本信息表.xlsx"
    else:
        doc = get_document("schedule")
        content = schedule.export_schedule_xlsx(doc["meta"], doc["rows"])
        filename = "行程安排.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{_quote(filename)}"},
    )


@app.post("/api/import/upload")
async def api_import_upload(payload: dict = Body(...)):
    raw = str(payload.get("bytes") or "")
    raw = raw.split(",", 1)[-1] if raw.startswith("data:") else raw
    try:
        content = base64.b64decode(raw)
    except Exception as exc:
        raise ValueError("文件内容不是有效的 base64") from exc
    if not content:
        raise ValueError("缺少文件内容")
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("Excel 不能超过 20 MB")
    saved = importer.save_upload(str(payload.get("name") or "导入.xlsx"), content)
    return {**saved, **importer.workbook_info(content)}


@app.post("/api/import/preview")
async def api_import_preview(payload: dict = Body(...)):
    import_id = payload.get("importId")
    options = payload.get("options") or {}
    if payload.get("kind") == "schedule":
        return importer.preview_schedule_import(import_id, options)
    return importer.preview_import(import_id, options)


@app.post("/api/import/commit")
async def api_import_commit(payload: dict = Body(...)):
    import_id = payload.get("importId")
    options = payload.get("options") or {}
    if payload.get("kind") == "schedule":
        return importer.commit_schedule_import(import_id, options)
    return importer.commit_import(import_id, options)


@app.post("/api/ai/transcribe")
async def api_transcribe(payload: dict = Body(...)):
    content = _decode_upload(payload)
    return transcribe_audio(content)


@app.post("/api/ai/ocr")
async def api_ocr(payload: dict = Body(...)):
    content = _decode_upload(payload)
    result = ocr_image(content)
    contact = extract_contact(result["lines"])
    doc = get_document("basic")
    values = _map_contact_to_fields(contact, doc["fields"])
    return {**result, "contact": contact, "values": values}


@app.post("/api/ai/extract-basic")
async def api_extract_basic(payload: dict = Body(...)):
    doc = get_document("basic")
    result = extract_basic(payload.get("text") or "", doc["fields"])
    return result


@app.post("/api/ai/extract-schedule")
async def api_extract_schedule(payload: dict = Body(...)):
    return extract_schedule_item(payload.get("text") or "", payload.get("baseDate") or date.today().isoformat())


@app.post("/api/schedule/build")
async def api_build_schedule(payload: dict = Body(default={})):
    return schedule.build_schedule(
        payload.get("startDate"),
        int(payload.get("weeks") or 2),
        payload.get("mode") or "merge",
    )


@app.post("/api/schedule/extract")
async def api_extract_schedule(payload: dict = Body(default={})):
    result = schedule_extract.extract_rows(payload.get("baseDate"))
    result["range"] = schedule_extract.expand_range(get_document("schedule")["meta"], result["items"])
    return result


@app.post("/api/schedule/apply")
async def api_apply_schedule(payload: dict = Body(default={})):
    return schedule_extract.apply_items(
        payload.get("items") or [],
        payload.get("baseVersion"),
    )


@app.get("/api/schedule/cards")
async def api_cards(from_date: str = "", to_date: str = ""):
    return schedule.build_cards(from_date or None, to_date or None)


@app.get("/api/cards/{day}.png")
async def api_card_png(day: str):
    content = brief.card_png(day)
    return Response(content=content, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/brief/{day}")
async def api_brief(day: str):
    return brief.day_brief(day)


@app.get("/api/email/settings")
async def api_email_settings():
    return brief.public_email_settings()


@app.patch("/api/email/settings")
async def api_email_settings_patch(payload: dict = Body(...)):
    return brief.update_email_settings(payload)


@app.post("/api/email/test")
async def api_email_test(payload: dict = Body(default={})):
    return brief.send_test_email(payload.get("recipient"))


@app.get("/api/email/preview")
async def api_email_preview(day: str = ""):
    return brief.email_preview(day or date.today().isoformat())


@app.post("/api/email/send")
async def api_email_send(payload: dict = Body(default={})):
    recipients = payload.get("recipients")
    if isinstance(recipients, str):
        recipients = [item.strip() for item in re.split(r"[,，;；\s]+", recipients) if item.strip()]
    return brief.send_email(payload.get("date") or date.today().isoformat(), recipients)


@app.get("/api/email/logs")
async def api_email_logs():
    uid = require_user()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM email_logs WHERE user_id=? ORDER BY id DESC LIMIT 100", (uid,)).fetchall()
    return {"logs": [dict(row) for row in rows]}


@app.post("/api/agent/run")
async def api_agent_run(payload: dict = Body(...)):
    return agent.start_run(payload.get("task") or "", payload.get("input") or {})


@app.get("/api/agent/runs/{run_id}")
async def api_agent_run_status(run_id: str):
    return agent.run_status(run_id)


@app.get("/api/agent/runs/{run_id}/events")
async def api_agent_events(run_id: str, since: int = 0):
    from fastapi.responses import StreamingResponse

    uid = require_user()
    return StreamingResponse(agent.event_stream(run_id, since, uid), media_type="text/event-stream")


@app.post("/api/agent/answer")
async def api_agent_answer(payload: dict = Body(...)):
    return agent.answer(payload.get("runId"), payload.get("questionId"), payload.get("value"), payload.get("text"))


def _decode_upload(payload):
    raw = str(payload.get("bytes") or payload.get("base64") or "")
    raw = raw.split(",", 1)[-1] if raw.startswith("data:") else raw
    if not raw:
        raise ValueError("缺少文件内容")
    content = base64.b64decode(raw)
    limit = 25 * 1024 * 1024 if payload.get("kind") == "audio" else 10 * 1024 * 1024
    if len(content) > limit:
        raise ValueError("文件过大")
    return content


def _map_contact_to_fields(contact, fields):
    labels = {field["key"]: field["label"] for field in fields}
    values = {field["key"]: "" for field in fields}
    person_key = next((k for k, v in labels.items() if v in ("人员", "姓名")), "person")
    contact_key = next((k for k, v in labels.items() if v in ("联系方式", "手机", "电话")), "contact")
    address_key = next((k for k, v in labels.items() if v == "地址"), "address")
    note_key = next((k for k, v in labels.items() if v == "备注"), "note")
    company_key = next((k for k, v in labels.items() if v in ("公司", "单位")), "")
    title_key = next((k for k, v in labels.items() if v == "职务"), "")
    values[person_key] = contact.get("name", "")
    values[contact_key] = contact.get("phone") or contact.get("email") or ""
    values[address_key] = contact.get("address", "")
    if company_key:
        values[company_key] = contact.get("company", "")
    if title_key:
        values[title_key] = contact.get("title", "")
    extras = " / ".join(item for item in (contact.get("company"), contact.get("title")) if item)
    if extras and not company_key and not title_key:
        values[note_key] = extras
    if not values.get(note_key):
        values[note_key] = ""
    return values


def _active_calendar_token():
    return active_calendar_token()


def _quote(value):
    from urllib.parse import quote

    return quote(value)


async def _email_catchup():
    for user in list_users():
        try:
            with user_context(user["id"]):
                settings = brief.email_settings()
                if not settings.get("enabled") or not settings.get("recipients"):
                    continue
                target = settings.get("sendTime") or DAILY_EMAIL_TIME
                if datetime.now().strftime("%H:%M") < target:
                    continue
                day = date.today().isoformat()
                with get_conn() as conn:
                    sent = conn.execute(
                        "SELECT 1 FROM email_logs WHERE user_id=? AND date=? AND status='sent' LIMIT 1",
                        (user["id"], day),
                    ).fetchone()
                if not sent:
                    await asyncio.to_thread(brief.send_email, day)
        except Exception:
            continue


async def _email_scheduler():
    while True:
        try:
            now = datetime.now().strftime("%H:%M")
            day = date.today().isoformat()
            for user in list_users():
                try:
                    with user_context(user["id"]):
                        settings = brief.email_settings()
                        if not settings.get("enabled") or not settings.get("recipients"):
                            continue
                        target = settings.get("sendTime") or DAILY_EMAIL_TIME
                        if now != target:
                            continue
                        with get_conn() as conn:
                            sent = conn.execute(
                                "SELECT 1 FROM email_logs WHERE user_id=? AND date=? AND status='sent' LIMIT 1",
                                (user["id"], day),
                            ).fetchone()
                        if not sent:
                            await asyncio.to_thread(brief.send_email, day)
                except Exception:
                    continue
        except Exception:
            pass
        await asyncio.sleep(45)


@app.get("/")
async def index():
    with open(STATIC_DIR + "/index.html", "r", encoding="utf-8") as handle:
        return HTMLResponse(handle.read())


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
