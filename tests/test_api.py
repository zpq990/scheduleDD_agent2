import base64
import os
import tempfile
import time
from io import BytesIO

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="dd-agent2-test-")

from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app

TEST_USER = 'tester'
TEST_PASS = 'testerpass'


def login(client):
    response = client.post('/api/auth/login', json={'username': TEST_USER, 'password': TEST_PASS})
    if response.status_code != 200:
        response = client.post('/api/auth/register', json={'username': TEST_USER, 'password': TEST_PASS})
    assert response.status_code == 200, response.text


def make_basic_xlsx(rows, headers=("人员", "联系方式", "地址", "备注", "公司")):
    wb = Workbook()
    ws = wb.active
    ws.append(list(headers))
    for row in rows:
        ws.append(row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def upload(client, content, name="test.xlsx"):
    return client.post(
        "/api/import/upload",
        json={"name": name, "bytes": base64.b64encode(content).decode()},
    ).json()


def test_default_fields_are_minimal():
    with TestClient(app) as client:
        login(client)
        doc = client.get("/api/documents/basic").json()
        assert [field["label"] for field in doc["fields"]] == ["人员", "联系方式", "地址", "备注"]


def test_demo_search_and_edit_roundtrip():
    with TestClient(app) as client:
        login(client)
        assert client.post("/api/demo/load").status_code == 200
        doc = client.get("/api/documents/basic").json()
        assert len(doc["rows"]) == 3
        found = client.get("/api/documents/basic/search", params={"q": "王建国"}).json()
        assert found["total"] >= 1
        rows = doc["rows"]
        rows[0]["note"] = "改过的备注"
        updated = client.patch(
            "/api/documents/basic",
            json={"baseVersion": doc["version"], "fields": doc["fields"], "rows": rows},
        )
        assert updated.status_code == 200
        assert updated.json()["rows"][0]["note"] == "改过的备注"


def test_stale_version_returns_conflict():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        doc = client.get("/api/documents/basic").json()
        assert client.patch(
            "/api/documents/basic",
            json={"baseVersion": doc["version"], "fields": doc["fields"], "rows": doc["rows"]},
        ).status_code == 200
        stale = client.patch(
            "/api/documents/basic",
            json={"baseVersion": doc["version"], "fields": doc["fields"], "rows": doc["rows"]},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "VERSION_CONFLICT"


def test_header_import_only_changes_schema():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        content = make_basic_xlsx([["赵总", "13600000000", "上海", "备注", "远山制造"]])
        upload_info = upload(client, content)
        preview = client.post(
            "/api/import/preview",
            json={"importId": upload_info["importId"], "options": {"mode": "header", "headerRow": 1}},
        ).json()
        assert "公司" in preview["headers"]
        commit = client.post(
            "/api/import/commit",
            json={"importId": upload_info["importId"], "options": {"mode": "header", "headerRow": 1}},
        ).json()
        labels = [field["label"] for field in commit["document"]["fields"]]
        assert "公司" in labels
        assert len(commit["document"]["rows"]) == 3


def test_data_import_uses_current_headers():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        content = make_basic_xlsx([["赵总", "13600000000", "上海", "新客户"]], headers=("人员", "联系方式", "地址", "备注"))
        upload_info = upload(client, content)
        preview = client.post(
            "/api/import/preview",
            json={"importId": upload_info["importId"], "options": {"mode": "data", "dataStartRow": 2}},
        ).json()
        assert preview["rows"]["valid"] == 1
        commit = client.post(
            "/api/import/commit",
            json={"importId": upload_info["importId"], "options": {"mode": "data", "dataStartRow": 2, "strategy": "update"}},
        ).json()
        assert any(row["person"] == "赵总" for row in commit["document"]["rows"])


def test_full_import_adds_fields_and_rows():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        content = make_basic_xlsx([["赵总", "13600000000", "上海", "新客户", "远山制造"]])
        upload_info = upload(client, content)
        commit = client.post(
            "/api/import/commit",
            json={"importId": upload_info["importId"], "options": {"mode": "full", "headerRow": 1, "strategy": "update"}},
        ).json()
        labels = [field["label"] for field in commit["document"]["fields"]]
        assert "公司" in labels
        assert any(row["person"] == "赵总" for row in commit["document"]["rows"])


def test_duplicate_headers_are_kept_and_aliased():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        content = make_basic_xlsx(
            [["远山制造", "长宁区", "赵总", "采购总监", "13600000000", "副总", "13600000001"]],
            headers=("公司", "地址", "人员1", "职务", "手机", "职务", "手机"),
        )
        upload_info = upload(client, content)
        preview = client.post(
            "/api/import/preview",
            json={"importId": upload_info["importId"], "options": {"mode": "full", "headerRow": 1}},
        ).json()
        assert "职务（2）" in preview["headers"]
        assert "手机（2）" in preview["headers"]
        aliases = {item["label"]: item["key"] for item in preview["schema"]["aliases"]}
        assert aliases["人员1"] == "person"
        assert aliases["手机"] == "contact"
        commit = client.post(
            "/api/import/commit",
            json={"importId": upload_info["importId"], "options": {"mode": "full", "headerRow": 1, "strategy": "update"}},
        ).json()
        assert any(row["person"] == "赵总" and row["contact"] == "13600000000" for row in commit["document"]["rows"])


def test_schedule_build_and_export():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        schedule = client.get("/api/documents/schedule").json()
        built = client.post(
            "/api/schedule/build",
            json={"startDate": schedule["meta"]["startDate"], "weeks": 2},
        ).json()
        assert built["items"]
        assert any(item["rowId"] == "r_demo_wang" for item in built["items"])
        export = client.get("/api/documents/schedule/export")
        assert export.status_code == 200
        assert export.content[:2] == b"PK"


def test_cards_ics_and_email_preview():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        day = client.get("/api/documents/schedule").json()["rows"][0]["date"]
        cards = client.get("/api/schedule/cards").json()["cards"]
        assert cards and cards[0]["items"]
        png = client.get(f"/api/cards/{day}.png")
        assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
        preview = client.get("/api/email/preview", params={"day": day}).json()
        assert preview["itemCount"] >= 1
        assert "行程咚咚" in preview["html"]


def test_version_restore():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        doc = client.get("/api/documents/basic").json()
        rows = doc["rows"]
        rows[0]["person"] = "改名人"
        updated = client.patch(
            "/api/documents/basic",
            json={"baseVersion": doc["version"], "fields": doc["fields"], "rows": rows},
        ).json()
        versions = client.get("/api/documents/basic/versions").json()["versions"]
        old = next(item for item in versions if item["version"] < updated["version"])
        restored = client.post(f"/api/documents/basic/versions/{old['version']}/restore")
        assert restored.status_code == 200
        assert restored.json()["version"] > updated["version"]


def test_agent_build_schedule_with_confirmation():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        schedule = client.get("/api/documents/schedule").json()
        run = client.post(
            "/api/agent/run",
            json={"task": "build_schedule", "input": {"startDate": schedule["meta"]["startDate"], "weeks": 2}},
        ).json()
        answered = False
        for _ in range(80):
            state = client.get(f"/api/agent/runs/{run['runId']}").json()
            for event in state["events"]:
                if event["type"] == "question" and not answered:
                    client.post(
                        "/api/agent/answer",
                        json={"runId": run["runId"], "questionId": event["question"]["id"], "value": "yes"},
                    )
                    answered = True
            if state["status"] != "running":
                break
            time.sleep(0.15)
        assert state["status"] == "done"
        assert "count" in state["summary"]


def test_agent_reschedule_moves_item_with_confirmation():
    with TestClient(app) as client:
        login(client)
        client.post("/api/demo/load")
        run = client.post(
            "/api/agent/run",
            json={"task": "reschedule", "input": {"instruction": "把周二下午的拜访改到周四上午"}},
        ).json()
        answered = set()
        state = None
        for _ in range(100):
            state = client.get(f"/api/agent/runs/{run['runId']}").json()
            for event in state["events"]:
                if event["type"] != "question" or event["seq"] in answered:
                    continue
                answered.add(event["seq"])
                question = event["question"]
                values = [option["value"] for option in question["options"]]
                value = "yes" if "yes" in values else values[0]
                client.post(
                    "/api/agent/answer",
                    json={"runId": run["runId"], "questionId": question["id"], "value": value},
                )
            if state["status"] != "running":
                break
            time.sleep(0.12)
        assert state["status"] == "done"
        assert state["summary"]["completed"] is True



def test_accounts_are_isolated():
    with TestClient(app) as client:
        user_a = f"alice{time.time_ns()}"
        user_b = f"bob{time.time_ns()}"
        assert client.post('/api/auth/register', json={'username': user_a, 'password': 'passwordA1'}).status_code == 200
        doc = client.get('/api/documents/basic').json()
        doc['rows'] = [{'id': 'r_a', 'person': 'A客户', 'contact': '111', 'address': '', 'note': ''}]
        assert client.patch('/api/documents/basic', json={
            'baseVersion': doc['version'], 'fields': doc['fields'], 'rows': doc['rows'],
        }).status_code == 200
        client.post('/api/auth/logout')
        assert client.post('/api/auth/register', json={'username': user_b, 'password': 'passwordB1'}).status_code == 200
        other = client.get('/api/documents/basic').json()
        assert other['rows'] == []
        assert client.get('/api/documents/basic/search', params={'q': 'A客户'}).json()['total'] == 0
        client.post('/api/auth/logout')
        assert client.get('/api/documents/basic').status_code == 401


def test_email_settings_are_isolated():
    with TestClient(app) as client:
        user_a = f"mail_a_{time.time_ns()}"
        user_b = f"mail_b_{time.time_ns()}"
        client.post('/api/auth/register', json={'username': user_a, 'password': 'mailpassA1'})
        client.patch('/api/email/settings', json={'recipients': ['a@example.com']})
        client.post('/api/auth/logout')
        client.post('/api/auth/register', json={'username': user_b, 'password': 'mailpassB1'})
        settings_b = client.get('/api/email/settings').json()
        assert settings_b['recipients'] == []


def test_extract_schedule_from_arbitrary_fields_and_expand():
    with TestClient(app) as client:
        login(client)
        doc = client.get('/api/documents/basic').json()
        fields = [
            {'key': 'person', 'label': '人员', 'type': 'text', 'required': True, 'builtin': True, 'hidden': False, 'width': 14},
            {'key': 'contact', 'label': '联系方式', 'type': 'text', 'required': False, 'builtin': True, 'hidden': False, 'width': 18},
            {'key': 'address', 'label': '地址', 'type': 'text', 'required': False, 'builtin': True, 'hidden': False, 'width': 28},
            {'key': 'note', 'label': '备注', 'type': 'textarea', 'required': False, 'builtin': True, 'hidden': False, 'width': 36},
            {'key': 'purpose', 'label': '目的', 'type': 'text', 'required': False, 'builtin': False, 'hidden': False, 'width': 18},
            {'key': 'note2', 'label': '备注2', 'type': 'textarea', 'required': False, 'builtin': False, 'hidden': False, 'width': 36},
        ]
        rows = [
            {'id': 'r_extract_1', 'person': 'A君', 'note': '打篮球', 'purpose': '预备客户', 'note2': '后天上午约了时间，具体看'},
            {'id': 'r_extract_2', 'person': '宋子乔', 'address': '上海南站地铁站', 'note': '闲鱼二手交易', 'purpose': '拿到照相机', 'note2': '后天下午3点准时交易'},
            {'id': 'r_extract_3', 'person': '张建华', 'address': '徐汇区仁爱大厦', 'note': '跟进客户', 'purpose': '促成交易，要带礼物', 'note2': '明天早上看时间拜访'},
            {'id': 'r_extract_4', 'person': '跨周', 'address': '虹桥', 'note': '会议', 'note2': '12月3日上午10点开会'},
        ]
        for row in rows:
            for field in fields:
                row.setdefault(field['key'], '')
        assert client.patch('/api/documents/basic', json={
            'baseVersion': doc['version'], 'fields': fields, 'rows': rows,
        }).status_code == 200
        extracted = client.post('/api/schedule/extract', json={'baseDate': '2026-11-16'}).json()
        by_person = {item['person']: item for item in extracted['items']}
        assert by_person['宋子乔']['time'] == '15:00'
        assert by_person['宋子乔']['text'] == '15:00 上海南站地铁站：闲鱼二手交易 · 拿到照相机'
        assert by_person['A君']['slot'] == '上午'
        assert by_person['张建华']['slot'] == '上午'
        assert by_person['跨周']['date'] == '2026-12-03'
        assert extracted['range']['weeks'] >= 3
        applied = client.post('/api/schedule/apply', json={
            'items': extracted['items'], 'baseVersion': client.get('/api/documents/schedule').json()['version'],
        }).json()
        assert applied['added'] >= 4
        assert applied['document']['meta']['weeks'] >= 3


def test_common_chinese_week_variants():
    from datetime import date
    from app.services.schedule_extract import find_dates, find_time, clean_text

    base = date(2026, 11, 16)
    for text in ("下个礼拜四中午", "下礼拜四中午", "下个星期四中午", "下礼拜四中午"):
        values = [value for value, _raw in find_dates(text, base)]
        assert date(2026, 11, 26) in values, text
        assert find_time(text)["slot"] == "中午"
    for text in ("这礼拜四中午", "礼拜四中午"):
        values = [value for value, _raw in find_dates(text, base)]
        assert date(2026, 11, 19) in values, text
    values = [value for value, _raw in find_dates("下下个礼拜四上午十点", base)]
    assert date(2026, 12, 3) in values
    values = [value for value, _raw in find_dates("下个月三号中午", base)]
    assert date(2026, 12, 3) in values
    assert clean_text("下个礼拜四中午") == ""


def test_smtp_settings_are_saved_without_returning_password():
    with TestClient(app) as client:
        login(client)
        response = client.patch('/api/email/settings', json={
            'smtpHost': 'smtp.qq.com',
            'smtpPort': '465',
            'smtpSsl': True,
            'smtpUser': 'demo@qq.com',
            'smtpFrom': 'demo@qq.com',
            'smtpPass': 'authorization-code',
            'recipients': ['demo@example.com'],
        })
        assert response.status_code == 200
        settings = client.get('/api/email/settings').json()
        assert settings['smtpHost'] == 'smtp.qq.com'
        assert settings['smtpPassSet'] is True
        assert 'smtpPass' not in settings
        # Clearing the host makes the test endpoint explain that SMTP is missing
        client.patch('/api/email/settings', json={'smtpHost': '', 'smtpClear': True})
        missing = client.post('/api/email/test', json={})
        assert missing.status_code == 400
        assert 'SMTP' in missing.json()['error']
