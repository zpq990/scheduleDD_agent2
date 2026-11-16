import base64
import html
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import (
    OUTBOX_DIR,
    SLOT_RANGES,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_SSL,
    SMTP_USER,
    WEEKDAYS,
)
from ..db import get_conn, get_document, get_setting, now_iso, require_user, set_setting
from .ai import AIUnavailable
from .secret import decrypt, encrypt
from .documents import row_key
from .schedule import build_cards, build_ics, parse_date_value

FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
]


def _font(size, bold=False):
    candidates = [p for p in FONT_CANDIDATES if p.exists()]
    if not candidates:
        return ImageFont.load_default()
    path = candidates[0]
    if bold and len(candidates) > 1:
        path = candidates[0]
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, width):
    lines = []
    for paragraph in str(text or "").splitlines() or [""]:
        current = ""
        for char in paragraph:
            test = current + char
            if draw.textlength(test, font=font) <= width or not current:
                current = test
            else:
                lines.append(current)
                current = char
        lines.append(current)
    return lines


def _items_for_date(day):
    schedule = get_document("schedule")
    basic = get_document("basic")
    labels = {field["key"]: field["label"] for field in basic["fields"]}
    rows = {row["id"]: row for row in basic["rows"]}
    items = []
    for item in schedule["rows"]:
        if item.get("date") != day:
            continue
        row = rows.get(item.get("rowId", ""))
        if not row:
            for candidate in basic["rows"]:
                if any(
                    str(candidate.get(key, "")).strip()
                    and str(candidate.get(key, "")).strip() in item.get("text", "")
                    for key in candidate
                    if key != "id"
                ):
                    row = candidate
                    break
        text = item.get("text", "")
        location, activity = text.split("：", 1) if "：" in text else ("", text)
        items.append(
            {
                "id": item["id"],
                "slot": item.get("slot", ""),
                "text": text,
                "location": location.strip(),
                "activity": activity.strip(),
                "row": row,
            }
        )
    items.sort(key=lambda item: list(SLOT_RANGES).index(item["slot"]) if item["slot"] in SLOT_RANGES else 99)
    return items, labels


def day_brief(day):
    parsed = parse_date_value(day)
    if not parsed:
        raise ValueError("日期格式不正确")
    items, labels = _items_for_date(parsed.isoformat())
    return {
        "date": parsed.isoformat(),
        "weekday": WEEKDAYS[(parsed.weekday() + 1) % 7],
        "items": [
            {
                "id": item["id"],
                "slot": item["slot"],
                "text": item["text"],
                "location": item["location"],
                "activity": item["activity"],
                "row": item["row"],
            }
            for item in items
        ],
        "fields": [{"key": key, "label": label} for key, label in labels.items()],
    }


def card_png(day, brief=None):
    brief = brief or day_brief(day)
    width, height = 1080, 1440
    image = Image.new("RGB", (width, height), "#f4efe5")
    draw = ImageDraw.Draw(image)
    title_font = _font(42, True)
    date_font = _font(86, True)
    small_font = _font(28)
    slot_font = _font(30, True)
    body_font = _font(34)
    meta_font = _font(26)
    margin = 80
    draw.text((margin, 72), "行程咚咚", font=title_font, fill="#1d1a16")
    draw.text((margin, 180), f"{brief['date'][5:7]}月{brief['date'][8:10]}日", font=date_font, fill="#1d1a16")
    draw.text((width - margin, 232), brief["weekday"], font=title_font, fill="#1d5fbf", anchor="ra")
    draw.line((margin, 330, width - margin, 330), fill="#d8cbb6", width=2)
    draw.text((margin, 348), f"当天 {len(brief['items'])} 项安排", font=small_font, fill="#706558")
    y = 410
    if not brief["items"]:
        draw.text((margin, y), "今天没有安排", font=body_font, fill="#706558")
    for item in brief["items"]:
        if y > height - 220:
            break
        draw.text((margin, y), item["slot"], font=slot_font, fill="#1d5fbf")
        activity = item["activity"] or item["text"]
        lines = _wrap(draw, activity, body_font, width - margin * 2 - 150)
        for line in lines:
            draw.text((margin + 130, y), line, font=body_font, fill="#1d1a16")
            y += 48
        if item["location"]:
            draw.text((margin + 130, y), item["location"], font=meta_font, fill="#706558")
            y += 40
        y += 26
    draw.text(
        (margin, height - 90),
        "本卡片不包含电话与邮箱 · 行程咚咚",
        font=small_font,
        fill="#9a8e7d",
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def email_settings():
    default = {
        "enabled": False,
        "recipients": [],
        "sendTime": "07:30",
        "includeContact": True,
        "smtpHost": "",
        "smtpPort": "465",
        "smtpUser": "",
        "smtpPass": "",
        "smtpFrom": "",
        "smtpSsl": True,
    }
    stored = get_setting("email", {}) or {}
    merged = {**default, **stored}
    merged["smtpPass"] = decrypt(stored.get("smtpPass", "")) if stored.get("smtpPass") else ""
    return merged


def public_email_settings(settings=None):
    settings = settings or email_settings()
    return {
        **{key: value for key, value in settings.items() if key != "smtpPass"},
        "smtpPassSet": bool(settings.get("smtpPass")),
    }


def update_email_settings(payload):
    current = email_settings()
    for key in ("smtpHost", "smtpUser", "smtpFrom"):
        if key in payload:
            current[key] = str(payload.get(key) or "").strip()
    if "smtpPort" in payload:
        current["smtpPort"] = str(payload.get("smtpPort") or "465").strip()
    if "smtpSsl" in payload:
        current["smtpSsl"] = bool(payload.get("smtpSsl"))
    if payload.get("smtpClear"):
        current["smtpPass"] = ""
    elif "smtpPass" in payload and str(payload.get("smtpPass") or "").strip():
        current["smtpPass"] = str(payload["smtpPass"]).strip()
    if "recipients" in payload:
        recipients = payload["recipients"]
        if isinstance(recipients, str):
            recipients = [item.strip() for item in __import__("re").split(r"[,，;；\s]+", recipients) if item.strip()]
        if isinstance(recipients, list):
            current["recipients"] = [str(item).strip() for item in recipients if "@" in str(item)]
    if "includeContact" in payload:
        current["includeContact"] = bool(payload["includeContact"])
    if "enabled" in payload:
        current["enabled"] = bool(payload["enabled"])
    if "sendTime" in payload:
        current["sendTime"] = str(payload.get("sendTime") or "07:30")
    stored = {**current, "smtpPass": encrypt(current.get("smtpPass", ""))}
    set_setting("email", stored)
    return public_email_settings(current)


def smtp_config(settings=None):
    settings = settings or email_settings()
    host = str(settings.get("smtpHost") or SMTP_HOST or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(settings.get("smtpPort") or SMTP_PORT),
        "user": str(settings.get("smtpUser") or SMTP_USER or "").strip(),
        "password": str(settings.get("smtpPass") or SMTP_PASS or ""),
        "from": str(settings.get("smtpFrom") or SMTP_FROM or settings.get("smtpUser") or SMTP_USER or "").strip(),
        "ssl": bool(settings.get("smtpSsl", SMTP_SSL)),
    }


def _email_subject(brief):
    return f"{brief['date'][5:7]}月{brief['date'][8:10]}日（{brief['weekday']}）行程 · {len(brief['items'])} 项"


def _render_rows_table(brief, include_contact):
    labels = {field["key"]: field["label"] for field in brief["fields"]}
    blocks = []
    seen = set()
    for item in brief["items"]:
        row = item.get("row")
        if not row:
            continue
        key = row.get("id") or row_key(row, brief["fields"])
        if key in seen:
            continue
        seen.add(key)
        cells = []
        for field in brief["fields"]:
            if not include_contact and field["label"] in ("联系方式", "手机", "电话"):
                continue
            value = str(row.get(field["key"], "") or "").strip()
            if value:
                cells.append((field["label"], value))
        if cells:
            blocks.append(cells)
    if not blocks:
        return ""
    out = ['<h3 style="font-size:16px;margin:24px 0 10px;color:#1d1a16;">涉及的基本信息</h3>']
    for cells in blocks:
        out.append('<table role="presentation" style="width:100%;border-collapse:collapse;margin:0 0 12px;font-size:13px;">')
        for label, value in cells:
            out.append(
                '<tr>'
                f'<td style="width:88px;color:#8b7e6b;padding:5px 0;vertical-align:top;">{html.escape(label)}</td>'
                f'<td style="color:#1d1a16;padding:5px 0;">{html.escape(value)}</td>'
                '</tr>'
            )
        out.append("</table>")
    return "".join(out)


def email_html(brief, include_contact=True, image_src="cid:card"):
    item_rows = []
    for item in brief["items"]:
        item_rows.append(
            '<tr>'
            f'<td style="width:64px;color:#1d5fbf;font-weight:600;padding:8px 0;vertical-align:top;">{html.escape(item["slot"])}</td>'
            f'<td style="padding:8px 0;color:#1d1a16;">{html.escape(item["text"])}</td>'
            '</tr>'
        )
    body = f"""
    <div style="max-width:680px;margin:0 auto;padding:28px 24px;background:#fbf7ef;color:#1d1a16;font-family:'PingFang SC','Microsoft YaHei',sans-serif;">
      <div style="font-size:13px;letter-spacing:.12em;color:#1d5fbf;">行程咚咚 · 每日简报</div>
      <h1 style="font-size:28px;margin:10px 0 4px;font-weight:600;">{brief['date'][5:7]}月{brief['date'][8:10]}日 <span style="font-size:16px;color:#706558;">{brief['weekday']}</span></h1>
      <p style="font-size:14px;color:#706558;margin:0 0 18px;">当天 {len(brief['items'])} 项安排</p>
      <table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px;border-top:1px solid #d8cbb6;">
        {''.join(item_rows) if item_rows else '<tr><td style="padding:16px 0;color:#706558;">今天没有安排</td></tr>'}
      </table>
      {_render_rows_table(brief, include_contact)}
      <h3 style="font-size:16px;margin:24px 0 10px;">当天卡片</h3>
      <img src="{image_src}" alt="当天行程卡片" style="width:100%;max-width:520px;border:1px solid #d8cbb6;border-radius:12px;" />
      <p style="font-size:12px;color:#9a8e7d;margin-top:24px;">附件含卡片 PNG 与当天 .ics。卡片与邮件不包含未匹配到的其他数据。</p>
    </div>
    """
    return body


def email_preview(day):
    brief = day_brief(day)
    png = card_png(day, brief)
    settings = email_settings()
    return {
        "subject": _email_subject(brief),
        "html": email_html(brief, settings.get("includeContact", True), "data:image/png;base64," + base64.b64encode(png).decode()),
        "cardPng": base64.b64encode(png).decode(),
        "itemCount": len(brief["items"]),
    }


def _day_ics(day):
    text = build_ics("")
    marker = day.replace("-", "")
    blocks = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        event = "BEGIN:VEVENT" + block.split("END:VEVENT")[0] + "END:VEVENT"
        if f"VALUE=DATE:{marker}" in event or f"DTSTART:{marker}" in event or marker in event:
            blocks.append(event)
    header = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//行程咚咚//Agent2//CN",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
        ]
    )
    return header + "\r\n" + "\r\n".join(blocks) + "\r\nEND:VCALENDAR\r\n"


def _send_smtp(message, recipients, config):
    context = ssl.create_default_context()
    if config["ssl"]:
        server = smtplib.SMTP_SSL(config["host"], config["port"], timeout=20, context=context)
    else:
        server = smtplib.SMTP(config["host"], config["port"], timeout=20)
        server.starttls(context=context)
    try:
        if config["user"]:
            server.login(config["user"], config["password"])
        server.sendmail(config["from"], recipients, message.as_string())
    finally:
        server.quit()


def send_test_email(recipient=None):
    settings = email_settings()
    recipient = (recipient or (settings.get("recipients") or [""])[0]).strip()
    if not recipient:
        raise ValueError("请先填写收件人")
    config = smtp_config(settings)
    if not config:
        raise ValueError("SMTP 尚未配置")
    message = EmailMessage()
    message["Subject"] = "行程咚咚 · SMTP 测试"
    message["From"] = formataddr(("行程咚咚", config["from"] or config["user"] or "no-reply@localhost"))
    message["To"] = recipient
    message.set_content("这是一封来自行程咚咚的测试邮件。收到即表示 SMTP 配置可用。")
    _send_smtp(message, [recipient], config)
    return {"ok": True, "recipient": recipient}


def send_email(day, recipients=None):
    settings = email_settings()
    recipients = recipients or settings.get("recipients", [])
    if not recipients:
        raise ValueError("没有设置收件人")
    brief = day_brief(day)
    if not brief["items"]:
        return {"mode": "empty", "message": "当天没有安排，未发送"}
    config = smtp_config(settings)
    if not config:
        files = save_preview_files(day)
        return {
            "mode": "preview",
            "message": "SMTP 未配置，已生成邮件预览文件",
            "files": files,
            "itemCount": len(brief["items"]),
        }
    png = card_png(day, brief)
    html_body = email_html(brief, settings.get("includeContact", True), "cid:card")
    ics = _day_ics(day)
    results = []
    uid = require_user()
    for recipient in recipients:
        message = EmailMessage()
        message["Subject"] = _email_subject(brief)
        message["From"] = formataddr(("行程咚咚", config["from"] or config["user"] or "no-reply@localhost"))
        message["To"] = recipient
        message["Date"] = formatdate(localtime=True)
        message.set_content("请使用支持 HTML 的邮件客户端查看今日行程简报。")
        message.add_alternative(html_body, subtype="html")
        image = message.get_payload()[1]
        image.add_related(png, maintype="image", subtype="png", cid="<card>", filename=f"{day}-card.png")
        message.add_attachment(ics.encode("utf-8"), maintype="text", subtype="calendar", filename=f"{day}.ics")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM email_logs WHERE user_id=? AND date=? AND recipient=?",
                (uid, day, recipient),
            ).fetchone()
            if row and row["status"] == "sent":
                results.append({"recipient": recipient, "status": "skipped", "message": "今天已发送"})
                continue
        try:
            _send_smtp(message, [recipient], config)
            status, error = "sent", ""
        except Exception as exc:
            status, error = "failed", str(exc)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO email_logs(user_id, date, recipient, status, error, created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(user_id,date,recipient) DO UPDATE SET status=excluded.status, error=excluded.error, created_at=excluded.created_at",
                (uid, day, recipient, status, error, now_iso()),
            )
            conn.commit()
        results.append({"recipient": recipient, "status": status, "error": error})
    return {"mode": "smtp" if SMTP_HOST else "preview", "results": results, "itemCount": len(brief["items"])}


def save_preview_files(day):
    preview = email_preview(day)
    day_dir = OUTBOX_DIR / require_user() / day
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "email.html").write_text(preview["html"], encoding="utf-8")
    (day_dir / "card.png").write_bytes(base64.b64decode(preview["cardPng"]))
    (day_dir / f"{day}.ics").write_text(_day_ics(day), encoding="utf-8")
    return {
        "dir": str(day_dir),
        "files": ["email.html", "card.png", f"{day}.ics"],
    }
