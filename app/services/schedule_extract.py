"""Extract schedule candidates from every text field of the basic-info table.

Rules only. Every date/time value must be present in the source text; missing
date/time is reported, never guessed. The UI shows the evidence before writing.
"""
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from ..db import get_document
from . import llm
from .schedule import SLOTS, SLOT_RANGES, parse_date_value, slot_for_time

REL_DAYS = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
WEEKDAY = {"日": 0, "天": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}

FULL_DATE = re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})[日号]?")
MD_DATE = re.compile(r"(\d{1,2}|[一二两三四五六七八九十]{1,3})月(\d{1,2}|[一二两三四五六七八九十]{1,3})[日号]?")
REL_DATE = re.compile(r"大后天|后天|明天|今天")
WEEK_DATE = re.compile(
    r"(?P<prefix>下下个|下下|下个|下一个|下|这个|这|本|上个|上一个|上)?\s*"
    r"(?P<unit>周|星期|礼拜)\s*(?P<weekday>[一二三四五六日天])"
)
MONTH_REL = re.compile(
    r"(?P<prefix>下下个|下个|这个|本|上个)\s*(?:个)?月\s*"
    r"(?P<day>\d{1,2}|[一二两三四五六七八九十]{1,3})[日号]?"
)
COLON_TIME = re.compile(r"(?<![\d])([01]?\d|2[0-3])[:：]([0-5]\d)(?![\d])")
POINT_TIME = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|夜里|睡前|睡觉前)?\s*"
    r"(?P<hour>\d{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:点|时)\s*"
    r"(?P<minute>半|一刻|三刻|\d{1,2}|[一二三四五六七八九十]{1,3})?"
)
FUZZY_PERIODS = [
    ("早饭", "上午"), ("早上", "上午"), ("清晨", "上午"), ("一早", "上午"), ("上午", "上午"),
    ("午饭", "中午"), ("中午", "中午"), ("午休", "中午"),
    ("下午茶", "下午"), ("下班前", "下午"), ("下午", "下午"),
    ("晚饭", "晚上"), ("下班后", "晚上"), ("睡前", "晚上"), ("睡觉前", "晚上"),
    ("傍晚", "晚上"), ("晚上", "晚上"), ("夜里", "晚上"),
]
UNCERTAIN = re.compile(r"看时间|约了时间|约时间|具体看|具体时间|时间待定|到时候|再说|待定|有空|找个时间|找时间|时间上")
NOISE = re.compile(r"约了时间|约时间|准时|约了|具体看|具体时间|看时间|时间待定|到时候|再说|待定|有空|找个时间|找时间")
GENERIC = {"拜访", "见面", "过去", "一趟", "一下", "办事", "有事", "联系", "客户"}
FUZZY_WORDS_RE = re.compile("|".join(sorted({word for word, _ in FUZZY_PERIODS}, key=len, reverse=True)))
PERSON_LABELS = {"人员", "姓名", "联系人"}
CONTACT_LABELS = {"联系方式", "手机", "电话", "邮箱", "微信", "email", "phone", "contact"}
ADDRESS_LABELS = {"地址", "地点", "拜访地点"}
SKIP_LABELS = {"序号", "编号", "ID", "id"}


def cn_number(value):
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        return (CN_NUM.get(left) or 1) * 10 + (CN_NUM.get(right) or 0)
    return CN_NUM.get(text, 0)


def _base_date(value=None):
    parsed = parse_date_value(value) if value else None
    return parsed or date.today()


def _date_from_week(prefix, weekday, base):
    target = WEEKDAY.get("日" if weekday == "天" else weekday, 0)
    week_start = base - timedelta(days=(base.weekday() + 1) % 7)
    offsets = {
        None: 0, "": 0, "这个": 0, "这": 0, "本": 0,
        "下个": 1, "下一个": 1, "下": 1,
        "下下": 2, "下下个": 2,
        "上个": -1, "上一个": -1, "上": -1,
    }
    if prefix in (None, ""):
        candidate = week_start + timedelta(days=target)
        if candidate < base:
            candidate += timedelta(days=7)
        return candidate
    return week_start + timedelta(days=offsets.get(prefix, 0) * 7 + target)


def _date_from_relative_month(prefix, day_text, base):
    day = cn_number(day_text)
    offsets = {"上个": -1, "上": -1, "这个": 0, "本": 0, "下个": 1, "下下个": 2}
    offset = offsets.get(prefix, 0)
    year = base.year
    month = base.month + offset
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text, base):
    found = []
    for match in FULL_DATE.finditer(text):
        try:
            found.append((date(int(match.group(1)), int(match.group(2)), int(match.group(3))), match.group(0)))
        except ValueError:
            pass
    for match in MD_DATE.finditer(text):
        try:
            year = base.year
            value = date(year, cn_number(match.group(1)), cn_number(match.group(2)))
            if value < base:
                value = date(year + 1, value.month, value.day)
            found.append((value, match.group(0)))
        except ValueError:
            pass
    for match in REL_DATE.finditer(text):
        found.append((base + timedelta(days=REL_DAYS[match.group(0)]), match.group(0)))
    for match in MONTH_REL.finditer(text):
        value = _date_from_relative_month(match.group("prefix"), match.group("day"), base)
        if value:
            found.append((value, match.group(0)))
    for match in WEEK_DATE.finditer(text):
        found.append((_date_from_week(match.group("prefix"), match.group("weekday"), base), match.group(0)))
    return found


def _normalize_time(period, hour, minute):
    """Return (HH:MM or '', slot or '', ambiguous)."""
    hour = int(hour)
    minute = int(minute)
    if minute > 59 or hour > 23:
        return "", "", True
    if period in ("下午", "傍晚", "晚上", "夜里", "睡前", "睡觉前") and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if period in ("晚上", "夜里", "睡前", "睡觉前") and hour == 12:
        hour = 0
    if not period:
        if 1 <= hour <= 11:
            return "", "", True
        if hour == 12:
            return "12:00", "中午", False
    value = f"{hour:02d}:{minute:02d}"
    return value, slot_for_time(hour * 60 + minute), False


def find_time(text):
    for match in COLON_TIME.finditer(text):
        hour, minute = int(match.group(1)), int(match.group(2))
        return {
            "time": f"{hour:02d}:{minute:02d}",
            "slot": slot_for_time(hour * 60 + minute),
            "ambiguous": False,
            "raw": match.group(0),
        }
    match = POINT_TIME.search(text)
    if match:
        hour = cn_number(match.group("hour"))
        minute = 30 if match.group("minute") == "半" else 15 if match.group("minute") == "一刻" else 45 if match.group("minute") == "三刻" else cn_number(match.group("minute")) if match.group("minute") else 0
        value, slot, ambiguous = _normalize_time(match.group("period"), hour, minute)
        return {
            "time": value,
            "slot": slot,
            "ambiguous": ambiguous,
            "raw": match.group(0),
            "hourRaw": match.group("hour"),
        }
    for word, slot in FUZZY_PERIODS:
        if word in text:
            return {"time": "", "slot": slot, "ambiguous": False, "raw": word, "fuzzy": True}
    uncertain = UNCERTAIN.search(text)
    if uncertain:
        return {"time": "", "slot": "需要配合", "ambiguous": False, "raw": uncertain.group(0), "uncertain": True}
    return {"time": "", "slot": "需要配合", "ambiguous": False, "raw": "", "uncertain": False}


def clean_text(text):
    value = str(text or "")
    for pattern in (FULL_DATE, MONTH_REL, MD_DATE, REL_DATE, WEEK_DATE, COLON_TIME, POINT_TIME):
        value = pattern.sub("", value)
    value = FUZZY_WORDS_RE.sub("", value)
    value = NOISE.sub("", value)
    value = re.sub(r"^[\s，,。；;、:：]+|[\s，,。；;、:：]+$", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _excluded(field):
    label = str(field.get("label") or "")
    key = str(field.get("key") or "")
    return (
        key in ("id", "person", "contact")
        or label in PERSON_LABELS
        or label in CONTACT_LABELS
        or label in SKIP_LABELS
        or "联系方式" in label
        or "手机" in label
        or "电话" in label
        or "邮箱" in label
        or "微信" in label
    )


def extract_rows(base_date=None):
    base = _base_date(base_date)
    doc = get_document("basic")
    fields = doc["fields"]
    person_key = next((f["key"] for f in fields if f["label"] in PERSON_LABELS), "person")
    address_key = next((f["key"] for f in fields if f["label"] in ADDRESS_LABELS), "")
    items, issues = [], []
    for row in doc["rows"]:
        field_texts = {
            field["label"]: str(row.get(field["key"], "") or "").strip()
            for field in fields
            if str(row.get(field["key"], "") or "").strip() and not _excluded(field)
        }
        row_text = " ".join(field_texts.values())
        dates, time_hits, evidences = [], [], []
        parts = []
        address = ""
        for field in fields:
            key, label = field["key"], field["label"]
            value = str(row.get(key, "") or "").strip()
            if not value or key == person_key:
                continue
            date_hits = find_dates(value, base)
            time_hit = find_time(value)
            if date_hits:
                dates.extend((d, raw, label) for d, raw in date_hits)
                if not any(e["field"] == key and e["raw"] == value for e in evidences):
                    evidences.append({"field": key, "label": label, "raw": value})
            if time_hit["raw"]:
                time_hits.append({**time_hit, "field": key, "label": label})
                if not any(e["field"] == key and e["raw"] == value for e in evidences):
                    evidences.append({"field": key, "label": label, "raw": value})
            if label in ADDRESS_LABELS:
                cleaned = clean_text(value)
                if cleaned:
                    address = cleaned
                continue
            if _excluded(field):
                continue
            cleaned = clean_text(value)
            if not cleaned:
                continue
            if cleaned in GENERIC and parts:
                continue
            if any(cleaned in part or part in cleaned for part in parts):
                continue
            parts.append(cleaned)
        person = str(row.get(person_key, "") or "").strip()
        unique_dates = []
        for value, _raw, _label in dates:
            if value not in unique_dates:
                unique_dates.append(value)
        chosen_date = unique_dates[0] if len(unique_dates) == 1 else None
        conflict_date = len(unique_dates) > 1
        exact = next((hit for hit in time_hits if hit.get("time") and not hit.get("ambiguous")), None)
        ambiguous = next((hit for hit in time_hits if hit.get("ambiguous")), None)
        fuzzy = next((hit for hit in time_hits if hit.get("slot") and not hit.get("time") and not hit.get("ambiguous")), None)
        if exact:
            slot = exact["slot"]
            time_value = exact["time"]
        elif ambiguous:
            slot = ""
            time_value = ""
        elif fuzzy:
            slot = fuzzy["slot"]
            time_value = ""
        else:
            slot = "需要配合"
            time_value = ""
        if not chosen_date:
            initial_status = "needs_date" if not conflict_date else "conflict_date"
        elif conflict_date:
            initial_status = "conflict_date"
        elif ambiguous:
            initial_status = "ambiguous_time"
        elif not exact and not fuzzy:
            initial_status = "needs_time"
        else:
            initial_status = "ready"
        if (
            initial_status in ("needs_date", "needs_time")
            and row_text
            and llm.ready()
            and (llm.DATE_EVIDENCE.search(row_text) or llm.TIME_EVIDENCE.search(row_text))
        ):
            suggestion = llm.suggest_schedule(row_text, base.isoformat(), field_texts)
            if suggestion.get("date") and not chosen_date:
                suggested = parse_date_value(suggestion["date"])
                if suggested:
                    chosen_date = suggested
                    unique_dates = [suggested]
                    conflict_date = False
            if suggestion.get("time") and not time_value:
                time_value = suggestion["time"]
                slot = suggestion.get("slot") or slot_for_time(int(time_value[:2]) * 60 + int(time_value[3:]))
            elif suggestion.get("slot") and (not slot or slot == "需要配合"):
                slot = suggestion["slot"]
            if suggestion.get("content") and not parts:
                parts = [suggestion["content"]]
            for raw in suggestion.get("evidence", []):
                if not any(e["raw"] == raw for e in evidences):
                    evidences.append({"field": "llm", "label": "模型证据", "raw": raw})
            if suggestion:
                evidences.append({"field": "llm", "label": "兜底模型", "raw": suggestion.get("note") or "已校验原文证据"})
        content = " · ".join(parts) if parts else (f"拜访{person}" if person else "待办")
        text = f"{address}：{content}" if address else content
        if time_value:
            text = f"{time_value} {text}"
        if not chosen_date:
            status = "needs_date" if not conflict_date else "conflict_date"
        elif conflict_date:
            status = "conflict_date"
        elif ambiguous:
            status = "ambiguous_time"
        elif time_value or (slot and slot != "需要配合"):
            status = "ready"
        else:
            status = "needs_time"
        item = {
            "rowId": row["id"],
            "person": person,
            "date": chosen_date.isoformat() if chosen_date else "",
            "dates": [d.isoformat() for d in unique_dates],
            "slot": slot,
            "time": time_value,
            "text": text,
            "content": content,
            "address": address,
            "status": status,
            "writable": status in ("ready", "needs_time"),
            "rawTime": ambiguous.get("raw") if ambiguous else "",
            "evidence": evidences,
            "raw": row,
        }
        items.append(item)
        if status != "ready" and status != "needs_time":
            issues.append(
                {
                    "rowId": row["id"],
                    "person": person,
                    "status": status,
                    "message": {
                        "needs_date": "没有找到明确日期",
                        "conflict_date": "同一行有多个日期，需要选择",
                        "ambiguous_time": "时间没有说明上午/下午，需要选择",
                    }.get(status, "需要确认"),
                    "dates": item["dates"],
                    "rawTime": ambiguous.get("raw") if ambiguous else "",
                    "text": text,
                }
            )
    return {"baseDate": base.isoformat(), "items": items, "issues": issues}


def week_start(value):
    day = parse_date_value(value)
    if not day:
        return None
    return day - timedelta(days=(day.weekday() + 1) % 7)


def expand_range(current_meta, items):
    current_start = week_start(current_meta.get("startDate")) or (date.today() - timedelta(days=(date.today().weekday() + 1) % 7))
    current_weeks = int(current_meta.get("weeks") or 2)
    current_end = current_start + timedelta(days=current_weeks * 7 - 1)
    starts, ends = [current_start], [current_end]
    for item in items:
        if not item.get("date"):
            continue
        value = parse_date_value(item["date"])
        if not value:
            continue
        start = value - timedelta(days=(value.weekday() + 1) % 7)
        starts.append(start)
        ends.append(start + timedelta(days=6))
    start = min(starts)
    end = max(ends)
    weeks = max(2, ((end - start).days + 1 + 6) // 7)
    return {"startDate": start.isoformat(), "weeks": weeks, "expanded": start != current_start or weeks != current_weeks}


def apply_items(items, base_version=None):
    """Append confirmed candidates to the current schedule and expand its range."""
    from .documents import commit

    current = get_document("schedule")
    rows = [dict(item) for item in current["rows"]]
    existing = {(item.get("date"), item.get("slot"), item.get("text")) for item in rows}
    added = 0
    for item in items or []:
        day = parse_date_value(item.get("date"))
        slot = str(item.get("slot") or "")
        text = str(item.get("text") or "").strip()
        if not day or slot not in SLOTS or not text:
            continue
        if item.get("time") and not text.startswith(str(item["time"])):
            text = f"{item['time']} {text}"
        key = (day.isoformat(), slot, text)
        if key in existing:
            continue
        row = {"id": str(item.get("id") or f"s_{day.isoformat()}_{slot}_{added}"), "date": day.isoformat(), "slot": slot, "text": text}
        if item.get("rowId"):
            row["rowId"] = str(item["rowId"])
        rows.append(row)
        existing.add(key)
        added += 1
    if not added:
        return {"document": current, "added": 0, "range": current["meta"]}
    meta = expand_range(current["meta"], rows)
    doc = commit(
        "schedule",
        {"rows": rows, "meta": meta},
        base_version=base_version if base_version is not None else current["version"],
        reason="生成行程",
    )
    return {"document": doc, "added": added, "range": meta}
