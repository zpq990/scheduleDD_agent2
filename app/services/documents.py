import json
from datetime import datetime

from ..config import DEFAULT_FIELDS, SLOTS
from ..db import (
    get_conn,
    get_document,
    new_id,
    now_iso,
    rebuild_search,
)


class ConflictError(Exception):
    def __init__(self, current, incoming, message="版本已变化"):
        super().__init__(message)
        self.current = current
        self.incoming = incoming


class ValidationError(Exception):
    pass


def _unique_field_keys(fields):
    seen = set()
    out = []
    for field in fields:
        key = str(field.get("key") or "").strip()
        if not key:
            key = new_id("f")
        while key in seen:
            key = new_id("f")
        seen.add(key)
        field["key"] = key
        out.append(field)
    return out


def normalize_fields(fields):
    if not isinstance(fields, list):
        raise ValidationError("字段定义格式不正确")
    normalized = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label") or "").strip()
        if not label:
            raise ValidationError("字段名不能为空")
        kind = str(raw.get("type") or "text")
        if kind not in ("text", "textarea", "date"):
            kind = "text"
        normalized.append(
            {
                "key": str(raw.get("key") or "").strip(),
                "label": label,
                "type": kind,
                "required": bool(raw.get("required")),
                "builtin": bool(raw.get("builtin")),
                "hidden": bool(raw.get("hidden")),
                "width": int(raw.get("width") or (14 if kind != "textarea" else 30)),
            }
        )
    normalized = _unique_field_keys(normalized)
    labels = [f["label"] for f in normalized]
    if len(labels) != len(set(labels)):
        raise ValidationError("字段名不能重复")
    if not any(f["key"] == "person" for f in normalized):
        person = next((f for f in normalized if f["label"] == "人员"), None)
        if person:
            person["key"] = "person"
            person["required"] = True
            person["builtin"] = True
        else:
            normalized.insert(
                0,
                {
                    "key": "person",
                    "label": "人员",
                    "type": "text",
                    "required": True,
                    "builtin": True,
                    "hidden": False,
                    "width": 14,
                },
            )
    for field in normalized:
        if field["key"] == "person":
            field["required"] = True
            field["builtin"] = True
            field["hidden"] = False
    return normalized


def normalize_basic_rows(rows, fields):
    if not isinstance(rows, list):
        raise ValidationError("行数据格式不正确")
    keys = [f["key"] for f in fields]
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = {"id": str(raw.get("id") or new_id("r"))}
        for key in keys:
            value = raw.get(key, "")
            row[key] = "" if value is None else str(value)
        out.append(row)
    return out


def normalize_schedule_rows(rows):
    if not isinstance(rows, list):
        raise ValidationError("行程数据格式不正确")
    out = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        slot = str(raw.get("slot") or "")
        if slot not in SLOTS:
            continue
        day = str(raw.get("date") or "")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        item = {
            "id": str(raw.get("id") or new_id("s")),
            "date": day,
            "slot": slot,
            "text": text,
        }
        if raw.get("rowId"):
            item["rowId"] = str(raw["rowId"])
        if raw.get("pending"):
            item["pending"] = True
        out.append(item)
    return out


def normalize_meta(kind, meta):
    if not isinstance(meta, dict):
        meta = {}
    if kind == "schedule":
        return {
            "startDate": str(meta.get("startDate") or ""),
            "weeks": int(meta.get("weeks") or 2),
            "owner": str(meta.get("owner") or ""),
            "location": str(meta.get("location") or ""),
            "holidays": meta.get("holidays") if isinstance(meta.get("holidays"), dict) else {},
            "travels": meta.get("travels") if isinstance(meta.get("travels"), dict) else {},
        }
    return meta if isinstance(meta, dict) else {}


def diff_documents(current, incoming):
    changes = []
    if current["fields"] != incoming["fields"]:
        changes.append({"type": "fields", "before": current["fields"], "after": incoming["fields"]})
    old_rows = {row["id"]: row for row in current["rows"]}
    new_rows = {row["id"]: row for row in incoming["rows"]}
    for row_id in sorted(set(old_rows) | set(new_rows)):
        if row_id not in old_rows:
            changes.append({"type": "row_added", "rowId": row_id, "after": new_rows[row_id]})
        elif row_id not in new_rows:
            changes.append({"type": "row_deleted", "rowId": row_id, "before": old_rows[row_id]})
        else:
            keys = set(old_rows[row_id]) | set(new_rows[row_id])
            fields = [
                key
                for key in keys
                if key != "id"
                and str(old_rows[row_id].get(key, "")) != str(new_rows[row_id].get(key, ""))
            ]
            if fields:
                changes.append(
                    {
                        "type": "row_updated",
                        "rowId": row_id,
                        "fields": fields,
                        "before": {k: old_rows[row_id].get(k, "") for k in fields},
                        "after": {k: new_rows[row_id].get(k, "") for k in fields},
                    }
                )
    if current["meta"] != incoming["meta"]:
        changes.append({"type": "meta", "before": current["meta"], "after": incoming["meta"]})
    return changes


def commit(kind, payload, base_version=None, reason="保存"):
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        doc = get_document(kind)
        if base_version is not None and int(base_version) != int(doc["version"]):
            conn.rollback()
            incoming = {
                "fields": payload.get("fields", doc["fields"]),
                "rows": payload.get("rows", doc["rows"]),
                "meta": payload.get("meta", doc["meta"]),
            }
            raise ConflictError(doc, incoming)
        if kind == "basic":
            fields = normalize_fields(payload.get("fields", doc["fields"]))
            rows = normalize_basic_rows(payload.get("rows", doc["rows"]), fields)
            meta = doc["meta"]
        else:
            fields = []
            rows = normalize_schedule_rows(payload.get("rows", doc["rows"]))
            meta = normalize_meta(kind, payload.get("meta", doc["meta"]))
        version = int(doc["version"]) + 1
        updated = now_iso()
        snapshot = {"fields": fields, "rows": rows, "meta": meta}
        conn.execute(
            "UPDATE document_data SET fields=?, rows=?, meta=? WHERE document_id=?",
            (
                json.dumps(fields, ensure_ascii=False),
                json.dumps(rows, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                doc["id"],
            ),
        )
        conn.execute(
            "UPDATE documents SET version=?, updated_at=? WHERE id=?",
            (version, updated, doc["id"]),
        )
        conn.execute(
            "INSERT INTO versions(document_id, version, snapshot, reason, created_at) VALUES(?,?,?,?,?)",
            (doc["id"], version, json.dumps(snapshot, ensure_ascii=False), reason, updated),
        )
        conn.execute(
            "DELETE FROM versions WHERE document_id=? AND id NOT IN "
            "(SELECT id FROM versions WHERE document_id=? ORDER BY version DESC LIMIT 100)",
            (doc["id"], doc["id"]),
        )
        rebuild_search(doc["id"], kind, fields, rows, commit=False)
        conn.commit()
    return get_document(kind)


def manual_save(kind, note=""):
    doc = get_document(kind)
    return commit(
        kind,
        {"fields": doc["fields"], "rows": doc["rows"], "meta": doc["meta"]},
        base_version=doc["version"],
        reason=note or "手动保存",
    )


def list_versions(kind):
    doc = get_document(kind)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT version, reason, created_at FROM versions WHERE document_id=? ORDER BY version DESC",
            (doc["id"],),
        ).fetchall()
    return [dict(row) for row in rows]


def get_version(kind, version):
    doc = get_document(kind)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT snapshot, reason, created_at FROM versions WHERE document_id=? AND version=?",
            (doc["id"], version),
        ).fetchone()
    if not row:
        raise KeyError("版本不存在")
    return {
        "version": version,
        "reason": row["reason"],
        "createdAt": row["created_at"],
        **json.loads(row["snapshot"]),
    }


def restore_version(kind, version):
    snapshot = get_version(kind, version)
    return commit(
        kind,
        {"fields": snapshot["fields"], "rows": snapshot["rows"], "meta": snapshot.get("meta", {})},
        base_version=None,
        reason=f"恢复到版本 {version}",
    )


def row_key(row, fields):
    labels = {f["key"]: f["label"] for f in fields}
    contact_key = next((k for k, v in labels.items() if v in ("联系方式", "手机", "电话")), "contact")
    person_key = next((k for k, v in labels.items() if v in ("人员", "姓名")), "person")
    address_key = next((k for k, v in labels.items() if v == "地址"), "address")
    contact = str(row.get(contact_key, "")).strip().lower()
    if contact:
        return f"c:{contact}"
    person = str(row.get(person_key, "")).strip().lower()
    address = str(row.get(address_key, "")).strip().lower()
    if person:
        return f"p:{person}|{address}"
    return f"id:{row.get('id')}"
