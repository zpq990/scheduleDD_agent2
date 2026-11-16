import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import BASE_DIR
from .schedule import parse_date_value, parse_time_value, slot_for_time

PHONE = re.compile(r"(?<!\d)(?:\+?86[\s-]*)?(1[3-9]\d[\s-]?\d{4}[\s-]?\d{4})(?!\d)")
LANDLINE = re.compile(r"(?<!\d)(0\d{2,3}[\s-]?\d{7,8}(?:[-转]\d{1,5})?)(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
URLISH = re.compile(r"(?:https?://|www\.)\S+", re.I)
ADDRESS_HINT = re.compile(r"(省|市|区|县|镇|乡|路|街|道|巷|弄|号|大厦|广场|园区|中心|楼|室|层|Road|Rd\.?|Street|St\.?|Ave)")
COMPANY_HINT = re.compile(
    r"(?:有限责任公司|股份有限公司|有限公司|公司|集团|Co\.?,?\s*Ltd\.?|Inc\.?|Corp\.?|LLC|Group|Technology|"
    r"(?:科技|信息|网络|咨询|传媒|控股|银行|医院|学校|大学|研究院|研究所|事务所|工作室|中心|实业|贸易|电子|软件|智能|传播|文化|教育|资本|投资))"
    r"\s*(?:[（(][^）)]{1,8}[）)])?\s*[。.]?$",
    re.I,
)
TITLE_HINT = re.compile(r"(董事长|总裁|总经理|副总经理|副总|总监|经理|主任|主管|专员|助理|工程师|设计师|顾问|合伙人|创始人|负责人|代表|院长|教授|医生|律师|会计师|会计|课长|部长|店长|讲师|导师)")
NAME = re.compile(r"^[一-龥·]{2,4}$")
NAME_STOP = re.compile(r"(代理|业务|部门|船务|物流|货运|营业|进口|出口|销售|市场|客服|人事|财务|技术|行政|采购|生产|研发|商务|贸易|科技|网络|咨询|中心|集团|公司|经理|总监|顾问|平台|联合|创始|商学|商學)")
LABEL = re.compile(r"^(电话|手机|座机|传真|邮箱|电邮|邮件|地址|网址|网站|公司|职位|姓名|Tel|Mobile|Phone|Fax|E-?mail|Add|Address|Web)\s*[:：]?\s*", re.I)
CN_NAME_AND_TITLE = re.compile(r"^([一-龥·]{2,4})[\s　]+(\S.*)$")


class AIUnavailable(Exception):
    pass


def _engine_paths():
    candidates = []
    env_engine = os.environ.get("LOCAL_AI_ENGINE")
    if env_engine:
        candidates.append(Path(env_engine))
    dirs = [
        os.environ.get("LOCAL_AI_DIR"),
        BASE_DIR / "local-ai",
        BASE_DIR.parent / "scheduleDD" / "local-ai",
    ]
    for raw in dirs:
        if not raw:
            continue
        directory = Path(raw)
        candidates.extend(
            [
                directory / "engine-dist" / "engine" / "engine.exe",
                directory / "engine-dist" / "engine" / "engine",
                directory / ".venv" / "Scripts" / "python.exe",
                directory / ".venv" / "bin" / "python",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate, candidates
    return None, candidates


def local_ai_ready():
    engine, _ = _engine_paths()
    return engine is not None


def _local_dir(engine, candidates):
    for candidate in candidates:
        if candidate == engine:
            if engine.name.lower().startswith("python"):
                return engine.parent.parent
            if engine.parent.name == "engine":
                return engine.parent.parent.parent
            return engine.parent
    return None


def _run_engine(mode, content, timeout):
    engine, candidates = _engine_paths()
    if not engine:
        raise AIUnavailable("本地识别引擎未安装；可以先手动输入，或在部署环境配置 OCR/ASR 服务")
    args = [str(engine), mode]
    local_dir = _local_dir(engine, candidates)
    if engine.name.lower().startswith("python"):
        script = next((p for p in [BASE_DIR.parent / "scheduleDD" / "local-ai" / "engine.py", BASE_DIR / "local-ai" / "engine.py"] if p.exists()), None)
        if not script:
            raise AIUnavailable("本地识别引擎缺少 engine.py")
        args = [str(engine), str(script), mode]
        local_dir = script.parent
    env = os.environ.copy()
    if local_dir:
        models = local_dir / "models"
        if models.is_dir() and not env.get("LOCAL_AI_MODELS"):
            env["LOCAL_AI_MODELS"] = str(models)
        ffmpeg = local_dir / "ffmpeg" / "ffmpeg.exe"
        if ffmpeg.exists() and not env.get("FFMPEG_BIN"):
            env["FFMPEG_BIN"] = str(ffmpeg)
    try:
        proc = subprocess.run(
            args,
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise AIUnavailable("本地识别超时") from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise AIUnavailable(message[-1] if message else "本地识别失败")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "ignore"))
    except json.JSONDecodeError as exc:
        raise AIUnavailable("本地识别返回格式不正确") from exc
    if data.get("error"):
        raise AIUnavailable(str(data["error"]))
    return data


def transcribe_audio(content):
    data = _run_engine("asr", content, 180)
    return {"text": str(data.get("text") or ""), "source": "local"}


def ocr_image(content):
    data = _run_engine("ocr", content, 60)
    lines = []
    for line in data.get("lines") or []:
        text = line.get("text") if isinstance(line, dict) else line
        text = str(text or "").strip()
        if text:
            lines.append(text)
    return {"text": "\n".join(lines), "lines": lines, "source": "local"}


def extract_contact(raw_lines):
    lines = []
    for line in raw_lines:
        text = line.get("text") if isinstance(line, dict) else line
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if text:
            lines.append(text)
    out = {"name": "", "company": "", "title": "", "phone": "", "email": "", "address": "", "lines": lines}
    used = set()

    def strip(value):
        return LABEL.sub("", value, count=1).strip()

    def digits(value):
        return re.sub(r"[\s-]", "", value)

    def is_name(value):
        return bool(NAME.fullmatch(value)) and not NAME_STOP.search(value) and not TITLE_HINT.search(value) and not COMPANY_HINT.search(value) and not ADDRESS_HINT.search(value)

    domains = []
    for line in lines:
        domains.extend(m.group(0).split("@")[1] for m in EMAIL.finditer(line))
        domains.extend(m.group(0).replace("http://", "").replace("https://", "").replace("www.", "").split("/")[0] for m in URLISH.finditer(line))
    domains = [d for d in domains if d]

    def fix_domain(email):
        domain = email.split("@")[-1]
        if sum(1 for d in domains if d.lower() == domain.lower()) >= 2:
            return email
        hit = next((d for d in domains if len(d) == len(domain) and sum(1 for a, b in zip(d.lower(), domain.lower()) if a != b) == 1), None)
        return email[: len(email) - len(domain)] + hit if hit else email

    for index, line in enumerate(lines):
        if not out["email"]:
            match = EMAIL.search(line)
            if match:
                out["email"] = fix_domain(match.group(0))
                used.add(index)
    for index, line in enumerate(lines):
        if not out["phone"]:
            match = PHONE.search(line)
            if match:
                out["phone"] = digits(match.group(1))
                used.add(index)
    if not out["phone"]:
        for index, line in enumerate(lines):
            match = LANDLINE.search(line)
            if match:
                out["phone"] = digits(match.group(1))
                used.add(index)
                break
    for index, line in enumerate(lines):
        if index not in used and URLISH.search(line) and not ADDRESS_HINT.search(URLISH.sub("", line)):
            used.add(index)
    for index, line in enumerate(lines):
        if index in used or out["address"]:
            continue
        text = strip(line)
        if ADDRESS_HINT.search(text) and len(text) >= 5 and re.search(r"\d|路|街|道|区|市", text) and not (COMPANY_HINT.search(text) and not re.search(r"[路街道号]", text)):
            out["address"] = text
            used.add(index)
    for index, line in enumerate(lines):
        if index in used or out["name"]:
            continue
        text = strip(line)
        match = CN_NAME_AND_TITLE.match(text)
        if match and is_name(match.group(1)):
            out["name"] = match.group(1)
            used.add(index)
            if not out["title"] and TITLE_HINT.search(match.group(2)) and len(match.group(2)) <= 24:
                out["title"] = match.group(2)
            continue
        if is_name(text):
            out["name"] = text
            used.add(index)
    for index, line in enumerate(lines):
        if index in used or out["company"]:
            continue
        text = strip(line)
        if COMPANY_HINT.search(text) and len(text) >= 4:
            out["company"] = text
            used.add(index)
    for index, line in enumerate(lines):
        if index in used or out["title"]:
            continue
        text = strip(line)
        if (TITLE_HINT.search(text) or text.endswith(("部", "课", "室"))) and len(text) <= 24 and not COMPANY_HINT.search(text):
            out["title"] = text
            used.add(index)
    return out


def extract_basic(text, fields):
    labels = {field["key"]: field["label"] for field in fields}
    person_key = next((k for k, v in labels.items() if v in ("人员", "姓名")), "person")
    contact_key = next((k for k, v in labels.items() if v in ("联系方式", "手机", "电话")), "contact")
    address_key = next((k for k, v in labels.items() if v == "地址"), "address")
    note_key = next((k for k, v in labels.items() if v == "备注"), "note")
    company_key = next((k for k, v in labels.items() if v in ("公司", "单位")), "")
    title_key = next((k for k, v in labels.items() if v == "职务"), "")
    raw = str(text or "").strip()
    out = {field["key"]: "" for field in fields}
    phone = PHONE.search(raw) or LANDLINE.search(raw)
    email = EMAIL.search(raw)
    if phone:
        out[contact_key] = re.sub(r"[\s-]", "", phone.group(1))
    elif email:
        out[contact_key] = email.group(0)
    address = re.search(r"(?:地址|地点|在)\s*[:：]?\s*([^，,。；;\n]+)", raw)
    if address:
        out[address_key] = address.group(1).strip()
    name = re.search(r"(?:姓名|叫)\s*[:：]?\s*([一-龥·]{2,4})", raw)
    if name:
        out[person_key] = name.group(1)
    else:
        head = re.match(r"^\s*([一-龥·]{2,4})(?=[，,、\s]|$)", raw)
        if head:
            out[person_key] = head.group(1)
    if company_key:
        company = re.search(r"(?:公司|单位)\s*[:：]?\s*([^，,。；;\n]+)", raw)
        if company:
            out[company_key] = company.group(1).strip()
    if title_key:
        title = re.search(r"(?:职务|职位)\s*[:：]?\s*([^，,。；;\n]+)", raw)
        if title:
            out[title_key] = title.group(1).strip()
    note_parts = []
    if email and not out[contact_key]:
        note_parts.append(email.group(0))
    if not out[person_key]:
        note_parts.append("未识别到人员，请补充")
    out[note_key] = out.get(note_key, "") or raw
    return {"fields": out, "raw": raw, "note": "；".join(note_parts)}


def extract_schedule_item(text, base_date=None):
    raw = str(text or "").strip()
    base = parse_date_value(base_date) or date.today()
    target = None
    for word, delta in (("今天", 0), ("明天", 1), ("后天", 2), ("大后天", 3)):
        if word in raw:
            target = base + timedelta(days=delta)
            raw = raw.replace(word, "", 1)
            break
    if not target:
        target = parse_date_value(raw)
    time_text, slot = parse_time_value(raw)
    if not slot:
        for word in ("上午", "中午", "下午", "晚上"):
            if word in raw:
                slot = word
                break
    location = ""
    match = re.search(r"(?:在|地点)\s*([^，,。；;\d]{2,30})", raw)
    if match:
        location = match.group(1).strip()
    activity = raw
    if match:
        activity = raw.replace(match.group(0), "").strip(" ，,。；;")
    if location and "：" not in activity:
        display = f"{location}：{activity or '拜访'}"
    else:
        display = activity or "拜访"
    return {
        "date": target.isoformat() if target else "",
        "slot": slot or "",
        "time": time_text or "",
        "location": location,
        "activity": activity or "拜访",
        "text": display,
    }
