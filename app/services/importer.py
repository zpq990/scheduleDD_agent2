import base64
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from ..config import IMPORT_DIR, SLOTS, WEEKDAYS
from ..db import create_import, get_document, import_owner, new_id, require_user
from .documents import commit, normalize_fields, normalize_basic_rows, row_key
from .schedule import parse_date_value, slot_for_time, parse_time_value

DATE_HINT = ("日期", "时间", "拜访", "跟进", "下次")


def save_upload(name, content):
    if not content or content[:2] != b"PK":
        raise ValueError("不是有效的 .xlsx 文件")
    user_id = require_user()
    import_id = new_id("imp")
    path = IMPORT_DIR / f"{import_id}.xlsx"
    path.write_bytes(content)
    create_import(user_id, name, str(path))
    return {"importId": import_id, "name": name, "size": len(content)}


def load_upload(import_id):
    safe = re.fullmatch(r"imp_[0-9a-f]{10}", import_id or "")
    if not safe:
        raise ValueError("导入任务不存在")
    user_id = require_user()
    if import_owner(import_id) != user_id:
        raise ValueError("导入任务不存在")
    path = IMPORT_DIR / f"{import_id}.xlsx"
    if not path.exists():
        raise ValueError("导入文件已过期，请重新上传")
    return path.read_bytes()


def workbook_info(content):
    wb = load_workbook(BytesIO(content), data_only=True)
    sheets = []
    for ws in wb.worksheets:
        nonempty = 0
        for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 200), values_only=True):
            if any(value not in (None, "") for value in row):
                nonempty += 1
        sheets.append(
            {
                "name": ws.title,
                "rows": ws.max_row,
                "cols": ws.max_column,
                "nonemptyRows": nonempty,
            }
        )
    return {"sheets": sheets}


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, float) and 0 <= value < 1:
        minutes = round(value * 24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    return str(value).strip()


def _sheet(wb, name=None):
    if name:
        return wb[name]
    for ws in wb.worksheets:
        if ws.max_row > 0 and ws.max_column > 0:
            return ws
    return wb.worksheets[0]


def _headers(ws, row):
    headers = []
    seen = {}
    warnings = []
    for col in range(1, ws.max_column + 1):
        label = _cell_text(ws.cell(row=row, column=col).value)
        if not label:
            continue
        base = label
        if label in seen:
            seen[base] += 1
            label = f"{base}（{seen[base]}）"
            warnings.append(f"表头「{base}」重复，已重命名为「{label}」")
        else:
            seen[label] = 1
        headers.append({"label": label, "col": col, "sourceLabel": base})
    return headers, warnings


def _first_nonempty_row(ws):
    for row in range(1, min(ws.max_row, 50) + 1):
        values = [_cell_text(ws.cell(row=row, column=col).value) for col in range(1, ws.max_column + 1)]
        if sum(1 for value in values if value) >= 2:
            return row
    return 1


def _incoming_rows(ws, headers, start_row):
    rows = []
    for row in range(start_row, ws.max_row + 1):
        values = {}
        raw_values = []
        for header in headers:
            value = _cell_text(ws.cell(row=row, column=header["col"]).value)
            values[header["label"]] = value
            raw_values.append(value)
        if not any(raw_values):
            continue
        rows.append({"row": row, "values": values})
    return rows


def _proposal_fields(current_fields, headers):
    current_by_label = {field["label"]: field for field in current_fields}
    current_by_key = {field["key"]: field for field in current_fields}
    fields = [dict(field) for field in current_fields]
    mapping = {}
    added = []
    matched = []
    aliases = []
    used_keys = set()
    for header in headers:
        label = header["label"]
        if label in current_by_label:
            field = current_by_label[label]
            mapping[label] = field["key"]
            used_keys.add(field["key"])
            matched.append({"label": label, "key": field["key"]})
            continue
        alias_key = ""
        if re.match(r"^人员\d*$", label) and "person" in current_by_key and "person" not in used_keys:
            alias_key = "person"
        elif label in ("姓名", "联系人") and "person" in current_by_key and "person" not in used_keys:
            alias_key = "person"
        elif label in ("手机", "电话", "联系方式") and "contact" in current_by_key and "contact" not in used_keys:
            alias_key = "contact"
        elif label == "地址" and "address" in current_by_key and "address" not in used_keys:
            alias_key = "address"
        if alias_key:
            mapping[label] = alias_key
            used_keys.add(alias_key)
            aliases.append({"label": label, "key": alias_key})
            continue
        field = {
            "key": new_id("f"),
            "label": label,
            "type": "text",
            "required": False,
            "builtin": False,
            "hidden": False,
            "width": 18,
        }
        fields.append(field)
        mapping[label] = field["key"]
        added.append({"label": label, "key": field["key"]})
    missing = [
        {"label": field["label"], "key": field["key"]}
        for field in current_fields
        if field["key"] not in used_keys
    ]
    return fields, mapping, added, matched, missing, aliases


def _map_rows(rows, mapping, fields):
    person_key = next((f["key"] for f in fields if f["key"] == "person"), "person")
    contact_key = next((f["key"] for f in fields if f["label"] in ("联系方式", "手机", "电话")), "contact")
    address_key = next((f["key"] for f in fields if f["label"] == "地址"), "address")
    out = []
    invalid = 0
    for item in rows:
        mapped = {}
        for label, key in mapping.items():
            mapped[key] = item["values"].get(label, "")
        if not str(mapped.get(person_key, "")).strip():
            for field in fields:
                if field["key"] != person_key and field["label"].startswith("人员"):
                    value = str(mapped.get(field["key"], "")).strip()
                    if value:
                        mapped[person_key] = value
                        break
        if not str(mapped.get(contact_key, "")).strip():
            for field in fields:
                if field["key"] != contact_key and field["label"] in ("手机", "电话", "联系方式"):
                    value = str(mapped.get(field["key"], "")).strip()
                    if value:
                        mapped[contact_key] = value
                        break
        if not str(mapped.get(address_key, "")).strip():
            for field in fields:
                if field["key"] != address_key and field["label"] == "地址":
                    value = str(mapped.get(field["key"], "")).strip()
                    if value:
                        mapped[address_key] = value
                        break
        if not str(mapped.get(person_key, "")).strip():
            invalid += 1
            continue
        out.append({"sourceRow": item["row"], "values": mapped})
    return out, invalid


def _classify(current_rows, incoming, fields):
    current_by_key = {}
    for row in current_rows:
        current_by_key.setdefault(row_key(row, fields), []).append(row)
    counts = {"new": 0, "update": 0, "duplicate": 0, "invalid": 0}
    sample = []
    for item in incoming:
        values = item["values"]
        key = row_key(values, fields)
        existing = current_by_key.get(key)
        if not existing:
            status = "new"
            changes = {}
        else:
            target = existing[0]
            changes = {
                key_: [str(target.get(key_, "")), value]
                for key_, value in values.items()
                if value and str(target.get(key_, "")) != value
            }
            status = "duplicate" if not changes else "update"
        counts[status] += 1
        if len(sample) < 20:
            sample.append(
                {
                    "sourceRow": item["sourceRow"],
                    "key": key,
                    "status": status,
                    "values": values,
                    "changes": changes,
                }
            )
    return counts, sample


def preview_import(import_id, options):
    content = load_upload(import_id)
    wb = load_workbook(BytesIO(content), data_only=True)
    ws = _sheet(wb, options.get("sheet"))
    mode = options.get("mode") or "full"
    if mode not in ("header", "data", "full"):
        raise ValueError("导入模式不正确")
    current = get_document("basic")
    aliases = []
    if mode == "data":
        fields = current["fields"]
        headers = [{"label": field["label"], "col": i + 1} for i, field in enumerate(fields)]
        mapping = {field["label"]: field["key"] for field in fields}
        added, matched, missing = [], [{"label": field["label"], "key": field["key"]} for field in fields], []
        header_row = None
        data_start = int(options.get("dataStartRow") or 1)
        rows = _incoming_rows(ws, headers, data_start)
        proposal_fields = fields
        warnings = []
        if ws.max_column > len(fields):
            warnings.append(f"文件有 {ws.max_column} 列，当前表只有 {len(fields)} 列；多出的列会被忽略")
    else:
        header_row = int(options.get("headerRow") or _first_nonempty_row(ws))
        headers, warnings = _headers(ws, header_row)
        if not headers:
            raise ValueError("没有读到表头")
        proposal_fields, mapping, added, matched, missing, aliases = _proposal_fields(current["fields"], headers)
        data_start = header_row + 1
        rows = _incoming_rows(ws, headers, data_start) if mode == "full" else []
    incoming, invalid = _map_rows(rows, mapping, proposal_fields)
    counts, sample = _classify(current["rows"], incoming, proposal_fields)
    counts["invalid"] = invalid
    return {
        "mode": mode,
        "sheet": ws.title,
        "sheets": [sheet.title for sheet in wb.worksheets],
        "headerRow": header_row,
        "dataStartRow": data_start,
        "headers": [header["label"] for header in headers],
        "schema": {"added": added, "matched": matched, "missing": missing, "aliases": aliases},
        "proposal": {"fields": proposal_fields},
        "rows": {
            "total": len(rows),
            "valid": len(incoming),
            "invalid": invalid,
            "counts": counts,
            "sample": sample,
        },
        "warnings": warnings,
    }


def _apply_rows(current_rows, incoming, fields, strategy):
    rows = [dict(row) for row in current_rows]
    by_key = {}
    for row in rows:
        by_key.setdefault(row_key(row, fields), []).append(row)
    stats = {"added": 0, "updated": 0, "skipped": 0, "replaced": 0}
    for item in incoming:
        values = item["values"]
        key = row_key(values, fields)
        existing = by_key.get(key)
        if not existing:
            row = {"id": new_id("r"), **{field["key"]: values.get(field["key"], "") for field in fields}}
            rows.append(row)
            by_key[key] = [row]
            stats["added"] += 1
            continue
        if strategy == "append":
            row = {"id": new_id("r"), **{field["key"]: values.get(field["key"], "") for field in fields}}
            rows.append(row)
            by_key[key].append(row)
            stats["added"] += 1
            continue
        if strategy == "skip":
            stats["skipped"] += 1
            continue
        target = existing[0]
        changed = False
        for field in fields:
            value = values.get(field["key"], "")
            if value and str(target.get(field["key"], "")) != value:
                target[field["key"]] = value
                changed = True
        stats["updated" if changed else "skipped"] += 1
    return rows, stats


def commit_import(import_id, options):
    preview = preview_import(import_id, options)
    mode = preview["mode"]
    current = get_document("basic")
    if mode == "header":
        fields = normalize_fields(preview["proposal"]["fields"])
        rows = current["rows"]
        stats = {"added": 0, "updated": 0, "skipped": 0, "replaced": 0}
        reason = "表头导入"
    else:
        fields = normalize_fields(preview["proposal"]["fields"]) if mode == "full" else current["fields"]
        content = load_upload(import_id)
        wb = load_workbook(BytesIO(content), data_only=True)
        ws = _sheet(wb, options.get("sheet"))
        if mode == "data":
            headers = [{"label": field["label"], "col": i + 1} for i, field in enumerate(fields)]
            mapping = {field["label"]: field["key"] for field in fields}
            start = int(options.get("dataStartRow") or 1)
        else:
            header_row = int(options.get("headerRow") or _first_nonempty_row(ws))
            headers, _ = _headers(ws, header_row)
            __, mapping, *_ = _proposal_fields(fields, headers)
            start = header_row + 1
        raw_rows = _incoming_rows(ws, headers, start)
        incoming, _ = _map_rows(raw_rows, mapping, fields)
        rows, stats = _apply_rows(current["rows"], incoming, fields, options.get("strategy") or "update")
        reason = f"{'整表导入' if mode == 'data' else '含表头导入'} · {options.get('strategy') or 'update'}"
    doc = commit("basic", {"fields": fields, "rows": rows, "meta": current["meta"]}, None, reason)
    return {"document": doc, "stats": stats, "preview": preview}


def _is_schedule_sheet(ws):
    labels = []
    for col in (3, 5, 7, 9, 11, 13, 15):
        if col <= ws.max_column:
            labels.append(_cell_text(ws.cell(row=1, column=col).value))
    return sum(1 for label in labels if label in WEEKDAYS) >= 3


def _parse_schedule_sheet(ws):
    items, holidays = [], {}
    date_rows = []
    for row in range(1, ws.max_row + 1):
        dates = []
        for col in (3, 5, 7, 9, 11, 13, 15):
            value = ws.cell(row=row, column=col).value
            parsed = parse_date_value(_cell_text(value))
            if parsed:
                dates.append((col, parsed))
        if len(dates) >= 3:
            date_rows.append((row, dates))
    all_dates = []
    for date_row, dates in date_rows:
        for col, day in dates:
            all_dates.append(day)
            holiday = _cell_text(ws.cell(row=date_row, column=col - 1).value)
            if holiday:
                holidays[day.isoformat()] = holiday
        for slot_index, slot in enumerate(SLOTS):
            slot_row = date_row + 2 + slot_index
            if slot_row > ws.max_row:
                continue
            for col, day in dates:
                text = _cell_text(ws.cell(row=slot_row, column=col).value)
                if text:
                    items.append(
                        {
                            "id": new_id("s"),
                            "date": day.isoformat(),
                            "slot": slot,
                            "text": text,
                        }
                    )
    if not all_dates:
        raise ValueError("没有识别到行程日期")
    start = min(all_dates)
    end = max(all_dates)
    weeks = max(2, (end - start).days // 7 + 1)
    meta = {
        "startDate": start.isoformat(),
        "weeks": weeks,
        "owner": _cell_text(ws.cell(row=date_rows[0][0] + 1, column=1).value) if date_rows else "",
        "location": "",
        "holidays": holidays,
        "travels": {},
    }
    return {"items": items, "meta": meta, "sheets": [ws.title]}


def preview_schedule_import(import_id, options):
    content = load_upload(import_id)
    wb = load_workbook(BytesIO(content), data_only=True)
    for ws in wb.worksheets:
        if _is_schedule_sheet(ws):
            parsed = _parse_schedule_sheet(ws)
            return {
                "kind": "schedule",
                "sheet": ws.title,
                "items": parsed["items"],
                "meta": parsed["meta"],
                "count": len(parsed["items"]),
                "sample": parsed["items"][:20],
            }
    raise ValueError("没有识别到行程安排模板（首行应有周日–周六）")


def commit_schedule_import(import_id, options):
    preview = preview_schedule_import(import_id, options)
    current = get_document("schedule")
    if options.get("replace"):
        items = preview["items"]
    else:
        existing_keys = {(item["date"], item["slot"], item["text"]) for item in current["rows"]}
        keep = [item for item in current["rows"] if (item["date"], item["slot"], item["text"]) not in existing_keys]
        items = current["rows"] + [
            item for item in preview["items"] if (item["date"], item["slot"], item["text"]) not in existing_keys
        ]
    doc = commit(
        "schedule",
        {"rows": items, "meta": preview["meta"]},
        None,
        "行程安排导入",
    )
    return {"document": doc, "count": len(preview["items"])}
