import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = DATA_DIR / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
OUTBOX_DIR = DATA_DIR / "outbox"
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_DIR = DATA_DIR / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "app.db"

DEFAULT_FIELDS = [
    {"key": "person", "label": "人员", "type": "text", "required": True, "builtin": True, "hidden": False, "width": 14},
    {"key": "contact", "label": "联系方式", "type": "text", "required": False, "builtin": True, "hidden": False, "width": 18},
    {"key": "address", "label": "地址", "type": "text", "required": False, "builtin": True, "hidden": False, "width": 28},
    {"key": "note", "label": "备注", "type": "textarea", "required": False, "builtin": True, "hidden": False, "width": 36},
]

SLOTS = ["上午", "中午", "下午", "晚上", "需要配合"]
SLOT_RANGES = {
    "上午": ("09:00", "12:00"),
    "中午": ("12:00", "13:30"),
    "下午": ("13:30", "18:00"),
    "晚上": ("18:00", "21:00"),
    "需要配合": None,
}

WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

APP_HOST = os.environ.get("HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("PORT", "9870"))
APP_ACCESS_CODE = os.environ.get("APP_ACCESS_CODE", "").strip()

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", SMTP_USER)
SMTP_SSL = os.environ.get("SMTP_SSL", "true").lower() != "false"
DAILY_EMAIL_ENABLED = os.environ.get("DAILY_EMAIL_ENABLED", "false").lower() == "true"
DAILY_EMAIL_TIME = os.environ.get("DAILY_EMAIL_TIME", "07:30")


def default_schedule_meta():
    return {
        "startDate": "",
        "weeks": 2,
        "owner": "",
        "location": "",
        "holidays": {},
        "travels": {},
    }


def default_email_settings():
    return {
        "enabled": False,
        "recipients": [],
        "sendTime": "07:30",
        "includeContact": True,
    }
