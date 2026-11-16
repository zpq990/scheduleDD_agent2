import base64
import contextvars
import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from ..db import get_conn, get_document, new_id, now_iso, require_user
from . import brief, schedule, schedule_extract
from .ai import extract_basic, extract_contact, ocr_image
from .documents import commit

RUNS = {}
ANSWERS = {}
_LOCK = threading.RLock()
TASKS = ("build_schedule", "make_cards", "daily_brief", "reschedule", "voice_row", "ocr_row")


def _emit(run_id, type_, payload=None):
    with _LOCK:
        conn = get_conn()
        row = conn.execute("SELECT COALESCE(MAX(seq),0) AS seq FROM agent_events WHERE run_id=?", (run_id,)).fetchone()
        seq = int(row["seq"]) + 1
        conn.execute(
            "INSERT INTO agent_events(run_id, seq, type, payload, created_at) VALUES(?,?,?,?,?)",
            (run_id, seq, type_, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
        )
        conn.execute("UPDATE agent_runs SET updated_at=? WHERE id=?", (now_iso(), run_id))
        conn.commit()
    return seq


def _plan(run_id, steps):
    plan = [{"key": key, "label": label, "status": "pending", "note": ""} for key, label in steps]
    _emit(run_id, "plan", {"plan": plan})
    return plan


def _step(run_id, key, status, note=""):
    _emit(run_id, "plan_step", {"key": key, "status": status, "note": note})


def _tool(run_id, name, fn):
    started = time.time()
    _emit(run_id, "tool_start", {"tool": name})
    try:
        result = fn()
        _emit(
            run_id,
            "tool_end",
            {
                "tool": name,
                "status": "ok",
                "summary": result[0] if isinstance(result, tuple) else str(result)[:120],
                "durationMs": int((time.time() - started) * 1000),
            },
        )
        return result[1] if isinstance(result, tuple) else result
    except Exception as exc:
        _emit(
            run_id,
            "tool_end",
            {
                "tool": name,
                "status": "error",
                "summary": str(exc),
                "durationMs": int((time.time() - started) * 1000),
            },
        )
        raise


def _ask(run_id, title, detail="", options=None, allow_text=False, placeholder=""):
    question = {
        "id": uuid.uuid4().hex,
        "title": title,
        "detail": detail,
        "options": options or [],
        "allowText": allow_text,
        "placeholder": placeholder,
    }
    _emit(run_id, "question", {"question": question})
    waiter = threading.Event()
    ANSWERS[run_id] = {"questionId": question["id"], "waiter": waiter, "answer": None}
    if not waiter.wait(300):
        raise TimeoutError("等待回答超时")
    answer = ANSWERS.get(run_id, {}).get("answer")
    ANSWERS.pop(run_id, None)
    if not answer:
        raise TimeoutError("没有收到回答")
    _emit(run_id, "answer", {"summary": answer.get("text") or answer.get("value") or "", "question": question})
    return answer


def _match_date_for_weekday(start, weekday_cn, not_before=None):
    mapping = {"日": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
    target = mapping.get("日" if weekday_cn == "天" else weekday_cn, 0)
    current = (start.weekday() + 1) % 7
    delta = (target - current) % 7
    candidate = start + timedelta(days=delta)
    if not_before and candidate < not_before:
        candidate += timedelta(days=7)
    return candidate


def _reschedule(run_id, input_):
    instruction = str(input_.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("请输入调格要求，例如：把周三下午的拜访改到周四上午")
    weekdays = re.findall(r"(?:周|星期)([一二三四五六日天])", instruction)
    slots = re.findall(r"上午|中午|下午|晚上", instruction)
    if not weekdays or not slots:
        raise ValueError("需要同时说明星期和时段，例如：把周三下午的拜访改到周四上午")
    source_weekday = weekdays[0]
    target_weekday = weekdays[1] if len(weekdays) > 1 else weekdays[0]
    source_slot = slots[0]
    target_slot = slots[1] if len(slots) > 1 else slots[0]
    doc = schedule.get_document("schedule")
    meta = doc["meta"]
    start = schedule.parse_date_value(meta.get("startDate")) or date.today()
    today = date.today()
    source_day = _match_date_for_weekday(start, source_weekday, today)
    target_day = _match_date_for_weekday(start, target_weekday, today)
    candidates = [
        item
        for item in doc["rows"]
        if item.get("date") == source_day.isoformat() and item.get("slot") == source_slot
    ]
    if not candidates:
        raise ValueError(f"{source_day.isoformat()} {source_slot} 没有找到可调整的行程")
    item = candidates[0]
    if len(candidates) > 1:
        answer = _ask(
            run_id,
            "要调整哪一条？",
            f"{source_day.isoformat()} {source_slot} 有多条行程",
            [{"value": c["id"], "label": c["text"], "description": c.get("rowId", "")} for c in candidates],
        )
        item = next((c for c in candidates if c["id"] == answer.get("value")), candidates[0])
    target_items = [
        other
        for other in doc["rows"]
        if other.get("date") == target_day.isoformat() and other.get("slot") == target_slot
    ]
    decision = "replace"
    if target_items:
        answer = _ask(
            run_id,
            f"{target_day.isoformat()} {target_slot} 已有安排",
            "；".join(other["text"] for other in target_items),
            [
                {"value": "replace", "label": "替换", "description": "移走原有安排"},
                {"value": "append", "label": "追加", "description": "两条都保留"},
                {"value": "cancel", "label": "取消"},
            ],
        )
        decision = answer.get("value") or "cancel"
        if decision == "cancel":
            return {"cancelled": True, "message": "已取消，没有写回"}
    answer = _ask(
        run_id,
        "确认调整这条行程？",
        f"{source_day.isoformat()} {source_slot} → {target_day.isoformat()} {target_slot}\n{item['text']}",
        [{"value": "yes", "label": "确认调整"}, {"value": "no", "label": "取消"}],
    )
    if answer.get("value") != "yes":
        return {"cancelled": True, "message": "已取消，没有写回"}
    updated = dict(item)
    updated["date"] = target_day.isoformat()
    updated["slot"] = target_slot
    rows = [row for row in doc["rows"] if row["id"] != item["id"]]
    if decision == "replace":
        rows = [row for row in rows if not (row.get("date") == target_day.isoformat() and row.get("slot") == target_slot)]
    rows.append(updated)
    _tool(run_id, "write_schedule", lambda: ("提交行程版本", commit("schedule", {"rows": rows, "meta": meta}, doc["version"], "Agent 调格")))
    return {
        "completed": True,
        "from": {"date": source_day.isoformat(), "slot": source_slot},
        "to": {"date": target_day.isoformat(), "slot": target_slot},
        "text": item["text"],
    }


def _build_schedule(run_id, input_):
    _step(run_id, 'read', 'done', '读取基本信息表')
    _step(run_id, 'build', 'active')
    extracted = _tool(run_id, "extract_schedule", lambda: ("扫描全部字段", schedule_extract.extract_rows(input_.get("baseDate"))))
    writable = [item for item in extracted["items"] if item.get("writable")]
    issues = extracted.get("issues") or []
    _step(run_id, 'build', 'done', f"可写入 {len(writable)} 条，需确认 {len(issues)} 条")
    if not writable:
        raise ValueError("没有找到可写入的行程；请检查日期或时间，或使用生成预览逐条确认")
    detail = f"可写入 {len(writable)} 条行程"
    if issues:
        detail += f"；另有 {len(issues)} 条日期或时间需要确认，本次先跳过"
    _step(run_id, 'confirm', 'active')
    answer = _ask(
        run_id,
        "确认写入行程安排？",
        detail,
        [{"value": "yes", "label": "写入可确认的行程"}, {"value": "no", "label": "取消"}],
    )
    if answer.get("value") != "yes":
        return {"cancelled": True, "message": "已取消，没有写回"}
    _step(run_id, 'confirm', 'done', '用户已确认')
    _step(run_id, 'write', 'active')
    doc = get_document("schedule")
    result = _tool(
        run_id,
        "write_schedule",
        lambda: ("提交行程版本", schedule_extract.apply_items(writable, doc["version"])),
    )
    _step(run_id, 'write', 'done', f"已写入 {result['added']} 条")
    return {
        "completed": True,
        "count": result["added"],
        "issues": issues,
        "startDate": result["range"].get("startDate"),
        "weeks": result["range"].get("weeks"),
    }


def _make_cards(run_id, input_):
    _step(run_id, 'read', 'done', '读取行程')
    _step(run_id, 'cards', 'active')
    cards = _tool(
        run_id,
        "render_cards",
        lambda: ("读取卡片", schedule.build_cards(input_.get("from"), input_.get("to"))),
    )
    _step(run_id, 'cards', 'done', f"生成 {len(cards['cards'])} 天卡片")
    return {"completed": True, "cards": cards["cards"], "count": len(cards["cards"])}


def _daily_brief(run_id, input_):
    day = input_.get("date") or date.today().isoformat()
    _step(run_id, 'read', 'done', '读取当天数据')
    _step(run_id, 'compose', 'active')
    preview = _tool(run_id, "build_day_brief", lambda: ("生成当天简报", brief.email_preview(day)))
    _step(run_id, 'compose', 'done', '邮件与卡片已生成')
    _step(run_id, 'confirm', 'active')
    answer = _ask(
        run_id,
        "发送今天的简报？",
        f"{day} · {preview['itemCount']} 项安排；邮件含行程、基本信息与卡片",
        [{"value": "send", "label": "发送邮件"}, {"value": "cancel", "label": "取消"}],
    )
    if answer.get("value") != "send":
        return {"cancelled": True, "message": "已取消，没有发送"}
    _step(run_id, 'confirm', 'done', '用户已确认')
    _step(run_id, 'send', 'active')
    result = _tool(run_id, "send_email", lambda: ("发送简报", brief.send_email(day)))
    _step(run_id, 'send', 'done', result.get('mode', '已处理'))
    return {"completed": True, "send": result, "subject": preview["subject"]}


def _insert_row(run_id, values, reason):
    doc = get_document("basic")
    row = {"id": new_id("r"), **{field["key"]: values.get(field["key"], "") for field in doc["fields"]}}
    rows = [row, *doc["rows"]]
    _tool(run_id, "write_basic", lambda: ("提交基本信息版本", commit("basic", {"fields": doc["fields"], "rows": rows, "meta": doc["meta"]}, doc["version"], reason)))
    return {"completed": True, "row": row}


def _voice_row(run_id, input_):
    _step(run_id, 'extract', 'active')
    text = str(input_.get("text") or "").strip()
    if not text:
        raise ValueError("没有转写文字")
    doc = get_document("basic")
    result = _tool(run_id, "extract_basic", lambda: ("抽取基本信息", extract_basic(text, doc["fields"])))
    _step(run_id, 'extract', 'done', '已抽取字段')
    _step(run_id, 'confirm', 'active')
    values = result["fields"]
    detail = "\n".join(f"{field['label']}：{values.get(field['key'], '') or '空'}" for field in doc["fields"])
    answer = _ask(
        run_id,
        "写入这条基本信息？",
        detail,
        [{"value": "yes", "label": "写入"}, {"value": "no", "label": "取消"}],
    )
    if answer.get("value") != "yes":
        return {"cancelled": True, "message": "已取消，没有写回"}
    _step(run_id, 'confirm', 'done', '用户已确认')
    _step(run_id, 'write', 'active')
    result = _insert_row(run_id, values, "Agent 语音录入")
    _step(run_id, 'write', 'done', '已提交并读回')
    return result


def _ocr_row(run_id, input_):
    _step(run_id, 'ocr', 'active')
    content = base64.b64decode(str(input_.get("bytes") or "").split(",", 1)[-1])
    result = _tool(run_id, "ocr_card", lambda: ("识别名片", ocr_image(content)))
    _step(run_id, 'ocr', 'done', f"识别 {len(result.get('lines', []))} 行")
    _step(run_id, 'confirm', 'active')
    contact = extract_contact(result["lines"])
    doc = get_document("basic")
    values = _map_contact(contact, doc["fields"])
    detail = "\n".join(f"{field['label']}：{values.get(field['key'], '') or '空'}" for field in doc["fields"])
    answer = _ask(
        run_id,
        "写入这张名片？",
        detail,
        [{"value": "yes", "label": "写入"}, {"value": "no", "label": "取消"}],
    )
    if answer.get("value") != "yes":
        return {"cancelled": True, "message": "已取消，没有写回"}
    _step(run_id, 'confirm', 'done', '用户已确认')
    _step(run_id, 'write', 'active')
    result = _insert_row(run_id, values, "Agent 名片录入")
    _step(run_id, 'write', 'done', '已提交并读回')
    return result


def _map_contact(contact, fields):
    from ..main import _map_contact_to_fields

    return _map_contact_to_fields(contact, fields)


HANDLERS = {
    "build_schedule": _build_schedule,
    "make_cards": _make_cards,
    "daily_brief": _daily_brief,
    "reschedule": _reschedule,
    "voice_row": _voice_row,
    "ocr_row": _ocr_row,
}

PLANS = {
    "build_schedule": [("read", "读取基本信息与行程"), ("build", "生成两周行程"), ("confirm", "用户确认"), ("write", "提交并读回"), ("summary", "交付摘要")],
    "make_cards": [("read", "读取行程"), ("cards", "生成每日卡片"), ("summary", "交付摘要")],
    "daily_brief": [("read", "读取当天行程与基本信息"), ("compose", "生成邮件与卡片"), ("confirm", "用户确认"), ("send", "发送"), ("summary", "交付摘要")],
    "reschedule": [("read", "读取行程"), ("locate", "定位行程格"), ("confirm", "用户确认"), ("write", "提交并读回"), ("summary", "交付摘要")],
    "voice_row": [("extract", "抽取基本信息"), ("confirm", "用户确认"), ("write", "提交并读回"), ("summary", "交付摘要")],
    "ocr_row": [("ocr", "识别名片"), ("confirm", "用户确认"), ("write", "提交并读回"), ("summary", "交付摘要")],
}


def _run(run_id, task, input_):
    plan = _plan(run_id, PLANS[task])
    _emit(run_id, "run_start", {"task": task, "plan": plan})
    try:
        result = HANDLERS[task](run_id, input_)
        _step(run_id, "summary", "done", "已交付")
        with get_conn() as conn:
            conn.execute(
                "UPDATE agent_runs SET status='done', summary=?, updated_at=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), now_iso(), run_id),
            )
            conn.commit()
        _emit(run_id, "done", {"summary": result})
    except Exception as exc:
        _step(run_id, "summary", "failed", str(exc))
        with get_conn() as conn:
            conn.execute(
                "UPDATE agent_runs SET status='failed', summary=?, updated_at=? WHERE id=?",
                (str(exc), now_iso(), run_id),
            )
            conn.commit()
        _emit(run_id, "error", {"message": str(exc)})


def start_run(task, input_):
    if task not in TASKS:
        raise ValueError("未知任务")
    uid = require_user()
    run_id = "run_" + uuid.uuid4().hex[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO agent_runs(id, user_id, task, input, status, summary, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (run_id, uid, task, json.dumps(input_, ensure_ascii=False), "running", "", now_iso(), now_iso()),
        )
        conn.commit()
    context = contextvars.copy_context()
    thread = threading.Thread(target=context.run, args=(_run, run_id, task, input_), daemon=True)
    thread.start()
    return {"runId": run_id, "task": task}


def run_status(run_id):
    uid = require_user()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_runs WHERE id=? AND user_id=?", (run_id, uid)).fetchone()
        events = conn.execute(
            "SELECT seq, type, payload, created_at FROM agent_events WHERE run_id=? ORDER BY seq",
            (run_id,),
        ).fetchall()
    if not row:
        raise KeyError("任务不存在")
    summary = row["summary"]
    try:
        summary = json.loads(summary) if summary else summary
    except (TypeError, json.JSONDecodeError):
        pass
    return {
        "runId": run_id,
        "task": row["task"],
        "status": row["status"],
        "summary": summary,
        "events": [
            {"seq": event["seq"], "type": event["type"], **json.loads(event["payload"]), "at": event["created_at"]}
            for event in events
        ],
    }


def answer(run_id, question_id, value, text):
    uid = require_user()
    with get_conn() as conn:
        owner = conn.execute("SELECT 1 FROM agent_runs WHERE id=? AND user_id=?", (run_id, uid)).fetchone()
    if not owner:
        raise KeyError("任务不存在")
    with _LOCK:
        pending = ANSWERS.get(run_id)
        if not pending:
            raise ValueError("当前没有等待回答的问题")
        if question_id and pending["questionId"] != question_id:
            raise ValueError("问题已变化，请刷新后重试")
        pending["answer"] = {"value": value or "", "text": text or ""}
        pending["waiter"].set()
    return {"ok": True}


async def event_stream(run_id, since=0, user_id=None):
    import asyncio

    uid = user_id or require_user()
    last = int(since or 0)
    quiet = 0
    while True:
        with get_conn() as conn:
            row = conn.execute("SELECT status FROM agent_runs WHERE id=? AND user_id=?", (run_id, uid)).fetchone()
            if not row:
                return
            events = conn.execute(
                "SELECT seq, type, payload, created_at FROM agent_events WHERE run_id=? AND seq>? ORDER BY seq",
                (run_id, last),
            ).fetchall()
        for event in events:
            last = event["seq"]
            payload = {"seq": event["seq"], "type": event["type"], **json.loads(event["payload"]), "at": event["created_at"]}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        if row and row["status"] != "running" and not events:
            quiet += 1
            if quiet >= 2:
                break
        else:
            quiet = 0
        await asyncio.sleep(0.25)
