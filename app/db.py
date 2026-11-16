import contextlib
import contextvars
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

from .config import DB_PATH, DEFAULT_FIELDS, default_email_settings, default_schedule_meta

_lock = threading.RLock()
_local = threading.local()
current_user = contextvars.ContextVar("current_user", default=None)


class AuthError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def get_conn():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def current_user_id():
    return current_user.get()


def require_user():
    user_id = current_user_id()
    if not user_id:
        raise AuthError("请先登录")
    return user_id


@contextlib.contextmanager
def user_context(user_id):
    token = current_user.set(user_id)
    try:
        yield
    finally:
        current_user.reset(token)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, kind)
);
CREATE TABLE IF NOT EXISTS document_data (
    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    fields TEXT NOT NULL DEFAULT '[]',
    rows TEXT NOT NULL DEFAULT '[]',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(document_id, version)
);
CREATE TABLE IF NOT EXISTS settings (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(user_id, key)
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    task TEXT NOT NULL,
    input TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'running',
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    date TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, date, recipient)
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    document_id UNINDEXED,
    row_id UNINDEXED,
    field_key UNINDEXED,
    content
);
"""


def _columns(conn, table):
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def _migrate(conn):
    cols = _columns(conn, "documents")
    if cols and "user_id" not in cols:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "CREATE TABLE documents_new (id TEXT PRIMARY KEY, user_id TEXT, kind TEXT NOT NULL, "
            "version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id, kind))"
        )
        conn.execute(
            "INSERT INTO documents_new(id,user_id,kind,version,created_at,updated_at) "
            "SELECT id,NULL,kind,version,created_at,updated_at FROM documents"
        )
        conn.execute("DROP TABLE documents")
        conn.execute("ALTER TABLE documents_new RENAME TO documents")
        conn.execute("PRAGMA foreign_keys=ON")

    settings_cols = _columns(conn, "settings")
    if settings_cols and "user_id" not in settings_cols:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE settings_new (user_id TEXT, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id,key))")
        conn.execute("INSERT INTO settings_new(user_id,key,value) SELECT NULL,key,value FROM settings")
        conn.execute("DROP TABLE settings")
        conn.execute("ALTER TABLE settings_new RENAME TO settings")
        conn.execute("PRAGMA foreign_keys=ON")

    email_cols = _columns(conn, "email_logs")
    if email_cols and "user_id" not in email_cols:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "CREATE TABLE email_logs_new (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, date TEXT NOT NULL, "
            "recipient TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
            "UNIQUE(user_id,date,recipient))"
        )
        conn.execute(
            "INSERT INTO email_logs_new(user_id,date,recipient,status,error,created_at) "
            "SELECT NULL,date,recipient,status,error,created_at FROM email_logs"
        )
        conn.execute("DROP TABLE email_logs")
        conn.execute("ALTER TABLE email_logs_new RENAME TO email_logs")
        conn.execute("PRAGMA foreign_keys=ON")

    for table, column in (
        ("agent_runs", "user_id"),
        ("assets", "user_id"),
    ):
        columns = _columns(conn, table)
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


def init_db():
    with _lock:
        conn = get_conn()
        _migrate(conn)
        conn.executescript(SCHEMA)
        conn.commit()


def _upcoming_sunday():
    today = date.today()
    return today - timedelta(days=(today.weekday() + 1) % 7)


def hash_password(password):
    salt = secrets.token_bytes(16)
    iterations = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
    return hmac.compare_digest(digest.hex(), digest_hex)


def _create_documents(conn, user_id):
    created = now_iso()
    for kind in ("basic", "schedule"):
        doc_id = f"{user_id}_{kind}"
        conn.execute(
            "INSERT OR IGNORE INTO documents(id,user_id,kind,version,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (doc_id, user_id, kind, 1, created, created),
        )
        existing = conn.execute("SELECT 1 FROM document_data WHERE document_id=?", (doc_id,)).fetchone()
        if existing:
            continue
        if kind == "basic":
            fields, rows, meta = DEFAULT_FIELDS, [], {}
        else:
            fields, rows = [], []
            meta = default_schedule_meta()
            meta["startDate"] = _upcoming_sunday().isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO document_data(document_id,fields,rows,meta) VALUES(?,?,?,?)",
            (doc_id, json.dumps(fields, ensure_ascii=False), json.dumps(rows, ensure_ascii=False), json.dumps(meta, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO versions(document_id,version,snapshot,reason,created_at) VALUES(?,?,?,?,?)",
            (
                doc_id,
                1,
                json.dumps({"fields": fields, "rows": rows, "meta": meta}, ensure_ascii=False),
                "初始版本",
                created,
            ),
        )


def create_user(username, password):
    username = (username or "").strip().lower()
    if not 3 <= len(username) <= 32:
        raise ValueError("账号长度需要 3–32 个字符")
    if len(password or "") < 8:
        raise ValueError("密码至少 8 位")
    with _lock:
        conn = get_conn()
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            conn.rollback()
            raise ValueError("账号已存在")
        first = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
        user_id = new_id("u")
        conn.execute(
            "INSERT INTO users(id,username,password_hash,created_at) VALUES(?,?,?,?)",
            (user_id, username, hash_password(password), now_iso()),
        )
        claimed = 0
        if first:
            for table in ("documents", "settings", "agent_runs", "email_logs", "assets"):
                columns = _columns(conn, table)
                if "user_id" in columns:
                    claimed += conn.execute(f"UPDATE {table} SET user_id=? WHERE user_id IS NULL", (user_id,)).rowcount
        _create_documents(conn, user_id)
        if not conn.execute("SELECT 1 FROM settings WHERE user_id=? AND key='email'", (user_id,)).fetchone():
            conn.execute(
                "INSERT INTO settings(user_id,key,value) VALUES(?,?,?)",
                (user_id, "email", json.dumps(default_email_settings(), ensure_ascii=False)),
            )
        conn.commit()
    return get_user(user_id)


def get_user(user_id):
    with _lock:
        row = get_conn().execute("SELECT id,username,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise KeyError("用户不存在")
    return dict(row)


def get_user_by_username(username):
    with _lock:
        row = get_conn().execute("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),)).fetchone()
    return dict(row) if row else None


def authenticate(username, password):
    user = get_user_by_username(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return {"id": user["id"], "username": user["username"]}


def _session_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id, days=30):
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
            (_session_hash(token), user_id, expires, now_iso()),
        )
        conn.commit()
    return token


def session_user(token):
    if not token:
        return None
    token_hash = _session_hash(token)
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT u.id,u.username,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        try:
            expires = datetime.fromisoformat(row["expires_at"])
        except ValueError:
            return None
        if expires <= datetime.now(timezone.utc):
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
            conn.commit()
            return None
    return {"id": row["id"], "username": row["username"]}


def revoke_session(token):
    if not token:
        return
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_session_hash(token),))
        conn.commit()


def list_users():
    with _lock:
        rows = get_conn().execute("SELECT id,username FROM users ORDER BY created_at").fetchall()
    return [dict(row) for row in rows]


def get_setting(key, default=None, user_id=None):
    uid = user_id or require_user()
    with _lock:
        row = get_conn().execute("SELECT value FROM settings WHERE user_id=? AND key=?", (uid, key)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key, value, user_id=None):
    uid = user_id or require_user()
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO settings(user_id,key,value) VALUES(?,?,?) "
            "ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value",
            (uid, key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()


def get_document(kind, user_id=None):
    uid = user_id or require_user()
    with _lock:
        conn = get_conn()
        doc = conn.execute("SELECT * FROM documents WHERE user_id=? AND kind=?", (uid, kind)).fetchone()
        if not doc:
            raise KeyError(kind)
        data = conn.execute("SELECT * FROM document_data WHERE document_id=?", (doc["id"],)).fetchone()
        if not data:
            raise KeyError(kind)
        return {
            "id": doc["id"],
            "kind": doc["kind"],
            "version": doc["version"],
            "updatedAt": doc["updated_at"],
            "fields": json.loads(data["fields"]),
            "rows": json.loads(data["rows"]),
            "meta": json.loads(data["meta"]),
        }


def rebuild_search(document_id, kind, fields, rows, commit=True):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM search_index WHERE document_id=?", (document_id,))
        if kind == "basic":
            for row in rows:
                for field in fields:
                    value = str(row.get(field["key"], "") or "")
                    if value:
                        conn.execute(
                            "INSERT INTO search_index(document_id,row_id,field_key,content) VALUES(?,?,?,?)",
                            (document_id, row["id"], field["key"], value),
                        )
        else:
            for item in rows:
                text = f"{item.get('date','')} {item.get('slot','')} {item.get('text','')}"
                conn.execute(
                    "INSERT INTO search_index(document_id,row_id,field_key,content) VALUES(?,?,?,?)",
                    (document_id, item["id"], item.get("slot", ""), text),
                )
        if commit:
            conn.commit()


def search_document(kind, query, field=None, limit=50, offset=0):
    q = (query or "").strip()
    if not q:
        return {"hits": [], "total": 0, "mode": "empty"}
    doc = get_document(kind)
    doc_id = doc["id"]
    with _lock:
        conn = get_conn()
        where = ["document_id=?"]
        params = [doc_id]
        if field:
            where.append("field_key=?")
            params.append(field)
        try:
            fts_query = '"' + q.replace('"', '""') + '"'
            sql = (
                "SELECT row_id, field_key, snippet(search_index, 3, '<mark>', '</mark>', '…', 18) AS snippet "
                "FROM search_index WHERE search_index MATCH ? AND " + " AND ".join(where) +
                " ORDER BY rank LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, [fts_query, *params, limit, offset]).fetchall()
            mode = "fts"
        except sqlite3.OperationalError:
            rows, mode = [], "fallback"
        if not rows:
            like = f"%{q}%"
            sql = (
                "SELECT row_id, field_key, content AS snippet FROM search_index "
                "WHERE content LIKE ? AND " + " AND ".join(where) + " LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, [like, *params, limit, offset]).fetchall()
            mode = "like"
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM search_index WHERE document_id=? AND content LIKE ?",
            (doc_id, f"%{q}%"),
        ).fetchone()["n"]
        return {
            "hits": [
                {"rowId": r["row_id"], "field": r["field_key"], "snippet": r["snippet"]}
                for r in rows
            ],
            "total": total,
            "mode": mode,
        }



def create_import(user_id, name, path):
    from pathlib import Path as _Path

    import_id = _Path(path).stem
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO imports(id,user_id,name,path,created_at) VALUES(?,?,?,?,?)",
            (import_id, user_id, name, path, now_iso()),
        )
        conn.commit()
    return import_id


def import_owner(import_id):
    with _lock:
        row = get_conn().execute("SELECT user_id FROM imports WHERE id=?", (import_id,)).fetchone()
    return row["user_id"] if row else None
