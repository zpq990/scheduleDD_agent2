import re
from datetime import date, datetime, time, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..config import SLOTS, SLOT_RANGES, WEEKDAYS
from ..db import get_document
from .documents import normalize_schedule_rows

TZ = ZoneInfo("Asia/Shanghai")
DATE_LABEL_HINTS = ("日期", "拜访日期", "跟进时间", "下次跟进", "时间", "date")
CONTACT_LABELS = ("联系方式", "手机", "电话", "contact")
PERSON_LABELS = ("人员", "姓名", "person")
ADDRESS_LABELS = ("地址", "address")
NOTE_LABELS = ("备注", "note")
COMPANY_LABELS = ("公司", "单位", "company")


def _label_map(fields):
    return {field["key"]: field["label"] for field in fields}


def _find_key(fields, hints, fallback):
    for field in fields:
        if field["label"] in hints or field["key"] == fallback:
            return field["key"]
    for field in fields:
        if any(hint in field["label"] for hint in hints):
            return field["key"]
    return fallback


def parse_date_value(value):
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?", text)
    if match:
        today = date.today()
        try:
            return date(today.year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        serial = float(text)
        if 30000 < serial < 80000:
            return date(1899, 12, 30) + timedelta(days=serial)
    return None


def parse_time_value(value):
    text = str(value or "").strip()
    if not text:
        return None, None
    match = re.search(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        return f"{hour:02d}:{minute:02d}", slot_for_time(hour * 60 + minute)
    match = re.search(r"(上午|中午|下午|晚上|傍晚|早上)?\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*[点时](半|一刻|三刻|[0-9]{1,2})?", text)
    if match:
        cn = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        def num(s):
            if s.isdigit():
                return int(s)
            if s == "十":
                return 10
            if "十" in s:
                a, b = s.split("十", 1)
                return (cn.get(a) or 1) * 10 + (cn.get(b) or 0)
            return cn.get(s, 0)
        hour = num(match.group(2))
        minute = 30 if match.group(3) == "半" else 15 if match.group(3) == "一刻" else 45 if match.group(3) == "三刻" else num(match.group(3)) if match.group(3) else 0
        if match.group(1) in ("下午", "晚上", "傍晚") and hour < 12:
            hour += 12
        if match.group(1) == "中午" and hour < 11:
            hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}", slot_for_time(hour * 60 + minute)
    for word, slot in (("上午", "上午"), ("中午", "中午"), ("下午", "下午"), ("晚上", "晚上"), ("需要配合", "需要配合")):
        if word in text:
            return None, slot
    return None, None


def slot_for_time(minutes):
    if minutes < 11 * 60:
        return "上午"
    if minutes < 13 * 60:
        return "中午"
    if minutes < 18 * 60:
        return "下午"
    return "晚上"


def _date_field(fields):
    for field in fields:
        label = field["label"]
        if any(hint in label for hint in DATE_LABEL_HINTS) and "联系" not in label:
            return field["key"]
    return None


def build_schedule(start_date=None, weeks=2, mode="merge"):
    doc = get_document("basic")
    fields = doc["fields"]
    rows = doc["rows"]
    labels = _label_map(fields)
    person_key = _find_key(fields, PERSON_LABELS, "person")
    address_key = _find_key(fields, ADDRESS_LABELS, "address")
    note_key = _find_key(fields, NOTE_LABELS, "note")
    company_key = _find_key(fields, COMPANY_LABELS, "company")
    date_key = _date_field(fields)
    if not date_key:
        return {
            "items": [],
            "pending": [],
            "message": "当前基本信息表没有日期/时间字段；请先加一个日期字段，或手动/语音录入行程。",
        }
    start = parse_date_value(start_date) if start_date else date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
    if not start:
        start = date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
    end = start + timedelta(days=int(weeks) * 7 - 1)
    items, pending = [], []
    for row in rows:
        day = parse_date_value(row.get(date_key, ""))
        if not day or day < start or day > end:
            continue
        time_text, slot = parse_time_value(row.get(date_key, ""))
        # The date field may only contain a date; look for a separate time-ish field.
        if not slot:
            for field in fields:
                if any(hint in field["label"] for hint in ("时间", "时段")) and field["key"] != date_key:
                    _, hint = parse_time_value(row.get(field["key"], ""))
                    if hint:
                        slot = hint
                        break
        person = str(row.get(person_key, "")).strip()
        company = str(row.get(company_key, "")).strip() if company_key else ""
        address = str(row.get(address_key, "")).strip() if address_key else ""
        note = str(row.get(note_key, "")).strip() if note_key else ""
        activity = note or f"拜访{person}" if person else note or "拜访"
        if company:
            activity = f"{activity}（{company}）"
        text = f"{address}：{activity}" if address else activity
        if not slot:
            pending.append(
                {
                    "rowId": row["id"],
                    "date": day.isoformat(),
                    "person": person,
                    "reason": "只有日期，没有明确时段",
                    "text": text,
                }
            )
            continue
        items.append(
            {
                "id": f"s_{row['id']}_{day.isoformat()}_{slot}",
                "date": day.isoformat(),
                "slot": slot,
                "text": text,
                "rowId": row["id"],
            }
        )
    items.sort(key=lambda item: (item["date"], SLOTS.index(item["slot"])))
    if mode == "merge":
        existing = get_document("schedule")["rows"]
        generated = {(item["date"], item["slot"], item["text"]) for item in items}
        keep = [
            item
            for item in existing
            if not (start.isoformat() <= item["date"] <= end.isoformat())
            or (item["date"], item["slot"], item["text"]) not in generated
        ]
        items = keep + items
        items.sort(key=lambda item: (item["date"], SLOTS.index(item["slot"])))
    else:
        existing = get_document("schedule")["rows"]
        keep = [
            item
            for item in existing
            if not (start.isoformat() <= item["date"] <= end.isoformat())
        ]
        items = keep + items
        items.sort(key=lambda item: (item["date"], SLOTS.index(item["slot"])))
    return {
        "items": items,
        "pending": pending,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "weeks": int(weeks),
        "message": "" if items else "没有可生成的行程；请补充日期/时段后重试。",
    }


def item_map(items):
    out = {}
    for item in items:
        out.setdefault(item["date"], {}).setdefault(item["slot"], []).append(item)
    return out


def public_schedule(doc):
    return {
        "kind": "schedule",
        "version": doc["version"],
        "updatedAt": doc["updatedAt"],
        "meta": doc["meta"],
        "items": doc["rows"],
    }


def schedule_dates(meta):
    start = parse_date_value(meta.get("startDate")) or date.today()
    weeks = int(meta.get("weeks") or 2)
    return [start + timedelta(days=i) for i in range(weeks * 7)]


def build_cards(from_date=None, to_date=None):
    doc = get_document("schedule")
    basic = get_document("basic")
    fields = basic["fields"]
    rows = {row["id"]: row for row in basic["rows"]}
    start = parse_date_value(from_date) if from_date else None
    end = parse_date_value(to_date) if to_date else None
    by_date = {}
    for item in doc["rows"]:
        day = parse_date_value(item["date"])
        if not day or (start and day < start) or (end and day > end):
            continue
        by_date.setdefault(item["date"], []).append(item)
    cards = []
    for day in sorted(by_date):
        items = sorted(by_date[day], key=lambda item: SLOTS.index(item.get("slot", "上午")))
        enriched = []
        parsed_day = parse_date_value(day)
        for item in items:
            row = rows.get(item.get("rowId", ""))
            if not row:
                row = _match_row(item, basic["rows"], fields)
            text = item.get("text", "")
            location, activity = (text.split("：", 1) + [""])[:2] if "：" in text else ("", text)
            enriched.append(
                {
                    "id": item["id"],
                    "slot": item["slot"],
                    "location": location.strip(),
                    "activity": activity.strip(),
                    "person": row.get(_find_key(fields, PERSON_LABELS, "person"), "") if row else "",
                    "row": {key: row.get(key, "") for key in row if key != "id"} if row else {},
                }
            )
        cards.append(
            {
                "date": day,
                "weekday": WEEKDAYS[(parsed_day.weekday() + 1) % 7] if parsed_day else "",
                "items": enriched,
            }
        )
    return {"cards": cards, "fields": [{"key": f["key"], "label": f["label"]} for f in fields]}


def _match_row(item, rows, fields):
    person_key = _find_key(fields, PERSON_LABELS, "person")
    contact_key = _find_key(fields, CONTACT_LABELS, "contact")
    address_key = _find_key(fields, ADDRESS_LABELS, "address")
    text = item.get("text", "")
    for row in rows:
        for key in (person_key, contact_key, address_key):
            value = str(row.get(key, "")).strip()
            if value and value in text:
                return row
    return None


def build_ics(token, now=None):
    doc = get_document("schedule")
    basic = get_document("basic")
    fields = basic["fields"]
    rows = {row["id"]: row for row in basic["rows"]}
    now = now or datetime.now(TZ)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//行程咚咚//Agent2//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:行程咚咚",
    ]
    for item in doc["rows"]:
        day = parse_date_value(item["date"])
        if not day or item.get("slot") not in SLOT_RANGES:
            continue
        row = rows.get(item.get("rowId", ""))
        if not row:
            row = _match_row(item, basic["rows"], fields)
        person = row.get(_find_key(fields, PERSON_LABELS, "person"), "") if row else ""
        address = row.get(_find_key(fields, ADDRESS_LABELS, "address"), "") if row else ""
        title = f"{person} · {item['text']}" if person else item["text"]
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{item['id']}@xingcheng-agent2",
                f"DTSTAMP:{_stamp(now)}",
                f"SEQUENCE:{doc['version']}",
                f"SUMMARY:{_esc(title)}",
            ]
        )
        slot_range = SLOT_RANGES.get(item["slot"])
        if slot_range:
            start = datetime.combine(day, time.fromisoformat(slot_range[0]), TZ)
            end = datetime.combine(day, time.fromisoformat(slot_range[1]), TZ)
            lines.append(f"DTSTART:{_stamp(start)}")
            lines.append(f"DTEND:{_stamp(end)}")
        else:
            lines.append(f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}")
            lines.append(f"DTEND;VALUE=DATE:{(day + timedelta(days=1)).strftime('%Y%m%d')}")
        if address:
            lines.append(f"LOCATION:{_esc(address)}")
        description = []
        if person:
            description.append(f"人员：{person}")
        if item.get("slot"):
            description.append(f"时段：{item['slot']}")
        note = row.get(_find_key(fields, NOTE_LABELS, "note"), "") if row else ""
        if note:
            description.append(note)
        if description:
            lines.append(f"DESCRIPTION:{_esc(chr(10).join(description))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _stamp(dt):
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")


def _esc(value):
    return str(value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line):
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    chunks = []
    while data:
        cut = 73 if chunks else 75
        while cut > 0 and (data[cut:cut + 1] and (data[cut] & 0xC0) == 0x80):
            cut -= 1
        chunks.append(data[:cut].decode("utf-8"))
        data = data[cut:]
    return "\r\n ".join(chunks)


def export_basic_xlsx(fields, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "基本信息表"
    thin = Side(style="thin", color="D9D2C5")
    for ci, field in enumerate(fields, 1):
        cell = ws.cell(row=1, column=ci, value=field["label"])
        cell.font = Font(bold=True, color="193B66")
        cell.fill = PatternFill("solid", fgColor="F2ECDF")
        cell.border = Border(bottom=thin)
        ws.column_dimensions[get_column_letter(ci)].width = field.get("width", 16)
    for ri, row in enumerate(rows, 2):
        for ci, field in enumerate(fields, 1):
            cell = ws.cell(row=ri, column=ci, value=row.get(field["key"], ""))
            cell.alignment = Alignment(vertical="top", wrap_text=field["type"] == "textarea")
            cell.border = Border(bottom=thin)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max(1, len(fields)))}{max(2, len(rows) + 1)}"
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def export_schedule_xlsx(meta, items):
    start = parse_date_value(meta.get("startDate")) or date.today()
    weeks = int(meta.get("weeks") or 2)
    wb = Workbook()
    ws = wb.active
    ws.title = "行程安排"
    widths = [10, 10, 24, 10, 24, 10, 24, 10, 24, 10, 24, 10, 24, 10, 24]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    thin = Side(style="thin", color="C9C1B3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill("solid", fgColor="FADADE")

    for week in range(weeks):
        date_row = 2 if week == 0 else 9
        owner_row = 3 if week == 0 else 10
        slot_start = 4 if week == 0 else 11
        ws.cell(row=date_row, column=1, value="周计划")
        if meta.get("owner"):
            ws.cell(row=owner_row, column=1, value=meta["owner"])
        for d in range(7):
            day = start + timedelta(days=week * 7 + d)
            label_col = 2 + d * 2
            value_col = label_col + 1
            if week == 0:
                ws.cell(row=1, column=value_col, value=WEEKDAYS[(day.weekday() + 1) % 7]).alignment = Alignment(horizontal="center")
            date_cell = ws.cell(row=date_row, column=value_col, value=day)
            date_cell.number_format = 'm"月"d"日"'
            date_cell.alignment = Alignment(horizontal="center")
            holiday = (meta.get("holidays") or {}).get(day.isoformat(), "")
            if holiday:
                ws.cell(row=date_row, column=label_col, value=holiday)
            travel = (meta.get("travels") or {}).get(day.isoformat(), "")
            ws.cell(row=owner_row, column=value_col, value=travel or meta.get("owner", ""))
        for si, slot in enumerate(SLOTS):
            row = slot_start + si
            for d in range(7):
                ws.cell(row=row, column=2 + d * 2, value=slot)

    for item in items:
        day = parse_date_value(item.get("date"))
        if not day:
            continue
        delta = (day - start).days
        if delta < 0 or delta >= weeks * 7:
            continue
        week = delta // 7
        d = delta % 7
        slot_index = SLOTS.index(item["slot"])
        row = (4 if week == 0 else 11) + slot_index
        col = 3 + d * 2
        cell = ws.cell(row=row, column=col)
        cell.value = item["text"] if not cell.value else f"{cell.value}；{item['text']}"
        cell.alignment = Alignment(wrap_text=True, vertical="center")

    for row in ws.iter_rows(min_row=1, max_row=15, max_col=15):
        for cell in row:
            cell.border = border
            if cell.column in (3, 5, 7, 9, 11, 13, 15):
                cell.fill = fill
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
