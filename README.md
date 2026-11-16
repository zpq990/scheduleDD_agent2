# 行程咚咚 Agent 2

ModelScope 优先的 Web 工作台：**基本信息表 → 两周行程安排 → 每日卡片 / 邮件简报**。

技术栈：Python 3.11 + FastAPI + SQLite（FTS5）+ openpyxl + Pillow；前端原生 JS，无构建步骤。

---

## 1. 快速开始

要求 Python **3.11**。

```bash
cd scheduleDD_agent2
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

打开 `http://127.0.0.1:9870`。

首次启动会创建 `data/app.db`，并生成：

- 当前基本信息表：默认字段 `人员 / 联系方式 / 地址 / 备注`
- 当前行程安排：两周，周日–周六，5 个时段

点右上角「示例」可载入演示数据。

Docker：

```bash
docker build -t schedule-agent2 .
docker run -p 9870:9870 -v $(pwd)/data:/data schedule-agent2
```

---

## 2. 账号与数据隔离

- 首次打开显示登录 / 注册页；注册后自动登录
- 账号 3–32 个字符，密码至少 8 位
- 密码用 PBKDF2-SHA256（260,000 次）加盐存储，不保存明文
- 登录后写入 HttpOnly Cookie 会话，30 天有效
- 每个账号独立拥有：基本信息表、行程安排、版本、搜索索引、邮件设置、Agent 运行记录
- 退出登录后回到登录页，不会看到上一个账号的数据
- 旧版本地数据迁移：数据库中第一个注册的账号会接管原有的无主文档；后续账号从空白开始
- `APP_ACCESS_CODE` 是可选的额外访问码，登录之外再加一层

认证接口：`POST /api/auth/register`、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。

## 3. 功能

### 基本信息表

- 默认 4 列，可自由加字段、改名、隐藏、删除（人员不可删）
- 在线表格编辑；自动保存（800ms 防抖）+ 手动保存
- 每次保存生成版本，可查看、对比、恢复
- 全字段搜索（FTS5，失败回退 LIKE），支持字段筛选、上一个/下一个、过滤行
- 下载 `基本信息表.xlsx`
- 选中单元格后，语音或 OCR 可直接填入该格

### 导入（三种模式）

上传 `.xlsx` 后选择：

| 模式 | 行为 |
|---|---|
| 表头导入 | 只读表头，新增/重命名字段，不导入数据 |
| 整表导入 | 所有行当数据，按当前表头列顺序导入，不改表头 |
| 整张表含表头导入 | 首行当表头，先改 schema 再导入数据 |

- 重复表头自动重命名为 `职务（2）` 等，不丢列
- `人员1` / `姓名` 可映射到「人员」，`手机` / `电话` 可映射到「联系方式」
- 查重：联系方式 → 人员 + 地址 → 人员
- 预览显示新增字段、缺失字段、行数、状态与样例
- 行程安排也可导入，按日期/时段识别

### 行程安排

- 一页一周，`本周 / 下周` 切换，`‹ ›` 翻到扩展周
- 7 天 × 5 时段：上午 / 中午 / 下午 / 晚上 / 需要配合
- **不要求独立日期列**：扫描全部文本字段，从备注/目的/备注2等任意位置抽取日期和时间
- 支持今天/明天/后天、周几/星期几/礼拜几、下周/下个礼拜/下下周、11月18日、下个月三号
- 支持精确时间（下午3点、晚上九点、15:30）与模糊时段（上午、睡觉前、早饭的时候）
- `3点` 没写上午/下午 → 暂停询问，不猜
- 没写时间 → 放 `需要配合`
- 日期超出当前两周 → 自动扩展行程表
- 生成前显示预览：日期、时段、时间、文本、来源、状态
- 选中格子后可清除该格行程；支持多选，清除前弹框确认格子数量
- 在线编辑单元格、节假日、起始周日
- 下载与模板一致的 `行程安排.xlsx`

### 卡片

- 一天一张，无安排不生成
- 显示日期、星期、时段、事项、地点
- 不包含电话/邮箱
- 在线浏览、下载 PNG（服务端 Pillow 渲染）


### 在设置页配置 QQ 邮箱 SMTP

1. QQ 邮箱网页版 → 设置 → 账号与安全 → 开启「IMAP/SMTP 服务」
2. 按提示获取 16 位「授权码」
3. 打开本应用「设置 → 每日邮件」，展开「SMTP 发送服务器」：

| 字段 | 填写 |
|---|---|
| 服务器 | `smtp.qq.com` |
| 端口 | `465` |
| SSL | 勾选 |
| 账号 | 你的 QQ 邮箱 |
| 授权码 / 密码 | QQ 邮箱授权码，不是 QQ 登录密码 |
| 发件人 | 与账号相同的 QQ 邮箱 |

4. 填收件人 → 保存设置
5. 点「发送测试邮件」确认可用
6. 点「预览今天」检查内容，再「立即发送今天」

SMTP 密码保存在当前账号的设置里，使用本机密钥加密，接口不会回传明文。若 465 被网络拦截，可改用端口 `587`、取消 SSL（STARTTLS）。`.env` 中的 `SMTP_*` 仍可作为未在页面配置时的兜底。

### 每日邮件

- 在设置页填写 SMTP 服务器、账号、授权码、端口与 SSL
- 设置收件人、发送时间、是否包含联系方式、是否启用
- 邮件内容：当天行程 + 匹配到的基本信息字段 + 卡片 PNG + 当天 `.ics` 附件（仅供邮件导入，不再提供日历订阅）
- 只包含当天匹配到的行，不发整份 Excel
- 同一日期 + 同一收件人只发一次；失败重试 3 次
- 未配置 SMTP 时生成预览文件到 `data/outbox/<date>/`

### Agent

右侧 Agent 面板，任务卡触发：

| 任务 | 流程 |
|---|---|
| 生成行程 | 读取 → 生成两周 → 询问 → 提交 → 读回 |
| 生成卡片 | 读取 → 渲染 → 交付 |
| 发送简报 | 读取 → 生成邮件/卡片 → 询问 → 发送 |
| 调格 | 解析「周二下午改到周三上午」→ 定位 → 冲突 → 确认 → 提交 |
| 语音/名片 | 前端转写/OCR → 交给 Agent 预览 → 确认写入 |

执行记录来自真实工具事件，包含工具名、耗时、结果、用户回答。

---

## 4. 模型接入（当前默认关闭）

行程抽取先用本地规则；模型只作为兜底，位置在 `app/services/llm.py`，与业务逻辑隔离。

- 默认 `AGENT_LLM_ENABLED=false`，不会发起模型请求
- 需要单独配置 `AGENT_LLM_PROVIDER / BASE_URL / API_KEY / MODEL / PROTOCOL`
- 当前支持 Anthropic Messages 与 OpenAI Chat Completions 两种协议
- 以后可参考 OpenRouter 的方式，增加模型列表、统一路由、成本统计和自动故障转移，而不改业务代码
- 模型只允许补日期/时间，必须返回原文证据；没有证据不采纳

## 5. 配置

复制 `.env.example` 为 `.env`，或使用环境变量。

| 变量 | 作用 |
|---|---|
| `HOST` / `PORT` | 监听地址与端口，默认 `127.0.0.1:9870`；容器内用 `0.0.0.0` |
| `DATA_DIR` | SQLite、导入文件、导出、邮件预览目录 |
| `APP_ACCESS_CODE` | 设置后，API 需要 `X-Access-Code`；前端会提示输入 |
| `SMTP_HOST/PORT/USER/PASS/FROM/SMTP_SSL` | 邮件发送；不配置则预览模式 |
| `DAILY_EMAIL_ENABLED` / `DAILY_EMAIL_TIME` | 每日发送兜底开关/时间 |
| `LOCAL_AI_DIR` / `LOCAL_AI_ENGINE` / `LOCAL_AI_PYTHON` | 本机 OCR/ASR 引擎路径 |

本机 OCR/ASR 默认查找 `../scheduleDD/local-ai` 的引擎；没有时语音/OCR 返回 503，但可手动输入。

本地 ASR 需要 `local-ai/models/whisper-small` 和 `local-ai/ffmpeg/ffmpeg.exe`；服务会自动把这两个路径传给引擎。缺少时会出现「本地语音模型未下载」或「录音无法解码」。

---

## 6. ModelScope 部署

1. 新建 Python/Docker 创空间
2. 上传本目录（或 Docker 镜像）
3. 设置 `HOST=0.0.0.0`、`PORT=9870`、`DATA_DIR=/data`
4. 设置 `APP_ACCESS_CODE`（公网演示必须）
5. 按需设置 SMTP 与模型服务
6. 启动命令：`python run.py`

> 平台若只接受特定 SDK，可用 Gradio 壳挂载同一 FastAPI 应用；业务 API 不变。

---

## 7. 验证

```bash
python -m pytest -q
python -m compileall -q app
```

当前测试覆盖（15 条）：

- 默认 4 字段
- 示例数据、搜索、编辑保存
- 版本冲突 409
- 表头导入 / 整表导入 / 含表头导入
- 行程生成与导出
- 卡片 PNG、ICS、邮件预览
- 版本恢复
- 任意字段抽取日期/时间、精确时间/模糊时段/无时间、自动扩展周
- 任务助手生成行程 + 用户确认

浏览器验证脚本：`scripts/ui_check.mjs`（需要本机 Chrome/Edge）。

---

## 8. 目录

```
app/
  main.py              FastAPI 路由、访问码、定时邮件
  config.py            路径、默认字段、时段、SMTP
  db.py                SQLite schema、FTS5、版本/搜索基础
  services/
    documents.py       字段/行校验、版本、冲突、恢复
    importer.py        三种导入模式、差异、行程导入
    schedule.py        两周行程、导出、ICS、卡片数据
    brief.py           日简报、Pillow 卡片、邮件、SMTP
    ai.py              本机 OCR/ASR、名片/基本信息/行程抽取
    agent.py           任务计划、工具事件、SSE、询问
  static/
    index.html / styles.css / app.js
skills/schedule-extraction/   行程时间抽取 skill（规则、词表、样例）
skills/schedule-extraction/   行程时间抽取 skill（规则、词表、样例）
tests/test_api.py
data/                  运行数据（gitignore）
Dockerfile
run.py
```
