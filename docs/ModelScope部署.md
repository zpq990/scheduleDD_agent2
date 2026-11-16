# ModelScope 部署手册

- 项目：`scheduleDD_agent2`
- 技术栈：Python 3.11 + FastAPI + SQLite + 静态前端
- 容器端口：`9870`
- 启动入口：`run.py`

---

## 1. 部署前先确认

### 1.1 能直接部署的部分

以下功能在 ModelScope 容器里可以正常运行：

- 注册 / 登录
- 基本信息表与动态字段
- Excel 导入 / 导出
- 行程抽取、周视图、清除格子
- 卡片生成 / 下载
- 每日邮件（SMTP 可用时）
- 账号数据隔离
- 版本、搜索

### 1.2 当前不能直接部署的部分

**本机语音转写 / 本机名片 OCR** 依赖：

- `local-ai/models/whisper-small`
- `local-ai/ffmpeg/ffmpeg.exe`
- Windows 打包引擎 `engine-dist/engine/engine.exe`

这些是 Windows 文件和路径，不能直接放进 Linux 容器。部署到 ModelScope 后：

- 语音按钮会返回“本地识别引擎未安装”
- 名片 OCR 同理
- 表格、行程、卡片、邮件不受影响

要恢复这两项，需要二选一：

1. 构建 Linux 版本地引擎（RapidOCR + faster-whisper + ffmpeg），把 `local-ai` 放进镜像
2. 接入远程 ASR / OCR 服务（当前版本未实现）

**建议首发部署先不启用本机语音/OCR**，把主链路跑稳。

---

## 2. 方式 A：Docker 创空间（推荐）

### 2.1 准备文件

需要上传：

```
app/
run.py
requirements.txt
Dockerfile
README.md
```

不要上传：

```
data/
config/token.txt
.env
test-results/
node_modules/
local-ai/.venv/
local-ai/models/
local-ai/ffmpeg/
```

`data/` 会在容器启动时自动创建。

### 2.2 本地先验证镜像

在项目目录执行：

```bash
docker build -t schedule-agent2 .
docker run --rm -p 9870:9870 \
  -e HOST=0.0.0.0 \
  -e PORT=9870 \
  -e DATA_DIR=/data \
  -v schedule-agent2-data:/data \
  schedule-agent2
```

浏览器打开 `http://127.0.0.1:9870`，确认能注册和打开基本信息表。

### 2.3 创建创空间

1. 登录 ModelScope
2. 进入「创空间 / Studio」
3. 新建创空间，选择 **Docker / 自定义容器** 运行方式
4. 公开范围建议先设为私有，验收后再公开
5. 记录创空间地址

如果控制台只提供 Gradio / Streamlit SDK，没有 Docker 选项，先不要选错；需要按平台当前支持的 Python 自定义应用方式启动 `python run.py`，或联系平台确认容器入口。当前项目是 FastAPI + 静态前端，不是 Gradio 页面。

### 2.4 上传代码

常见方式是 Git：

```bash
git clone https://www.modelscope.cn/studios/<用户名>/<空间名>.git
cd <空间名>
# 把 app/、run.py、requirements.txt、Dockerfile 复制进来
git add .
git commit -m "deploy schedule agent"
git push
```

也可以在创空间页面上传文件。

### 2.5 配置环境变量

在创空间的「环境变量 / Secrets」中设置：

| 变量 | 建议值 | 说明 |
|---|---|---|
| `HOST` | `0.0.0.0` | 必须，容器外才能访问 |
| `PORT` | `9870` | 与 Dockerfile 一致 |
| `DATA_DIR` | `/data` | SQLite、导出、邮件预览 |
| `APP_ACCESS_CODE` | 随机串 | 可选，额外访问码 |
| `APP_SECRET` | 随机长字符串 | 用于加密 SMTP 密码，**必须固定** |
| `SMTP_HOST` | 可选 | 页面配置 SMTP 时可不填 |
| `SMTP_PORT` | 可选 | 465/587 |
| `SMTP_USER` | 可选 | QQ 邮箱等 |
| `SMTP_PASS` | 可选 | 授权码 |
| `SMTP_SSL` | 可选 | true/false |
| `AGENT_LLM_ENABLED` | `false` | 模型兜底默认关闭 |

**`APP_SECRET` 不要每次重启都变**，否则已保存的 SMTP 密码无法解密。  
如果平台没有持久化 `/data`，重启后数据会丢，`APP_SECRET` 也必须重新保持一致。

### 2.6 持久化

- 如果能挂载持久卷：把 `/data` 挂上去
- 如果没有持久卷：SQLite 和上传文件会随容器重建丢失
- 无论是否持久化，建议定期用「下载」导出 Excel / 备份数据
- 邮箱设置里的 SMTP 密码依赖 `/data/app.secret` 或固定 `APP_SECRET`

### 2.7 首次登录

1. 打开创空间 URL
2. 注册第一个账号
3. 第一个账号会接管旧版本遗留的无主文档（新部署没有旧文档则从空白开始）
4. 后续账号各自独立，互相看不到数据

### 2.8 配置 SMTP

在页面「设置 → 每日邮件 → SMTP 发送服务器」填写：

```
服务器：smtp.qq.com
端口：465
SSL：勾选
账号：你的QQ邮箱@qq.com
授权码/密码：QQ邮箱授权码
发件人：你的QQ邮箱@qq.com
```

然后：

1. 保存设置
2. 发送测试邮件
3. 预览今天
4. 立即发送今天

注意：云平台经常封 25 端口；使用 465/587。若 465 超时，改 587 + 关闭 SSL（STARTTLS）。如果平台整体禁止出站 SMTP，邮件只能生成预览文件。

### 2.9 健康检查

部署后访问：

```
https://<创空间地址>/api/health
```

应返回：

```json
{"ok":true}
```

登录后访问 `/api/status` 可看到 `localAI: false`、`smtpConfigured` 等状态。

---

## 3. 方式 B：不支持 Docker 时

如果 ModelScope 当前只支持 Gradio/Streamlit：

1. 不要直接改业务代码
2. 用平台支持的自定义 Python 启动方式运行 `python run.py`
3. 确保平台允许监听 `9870`
4. 确认静态资源 `/static/*` 可访问
5. 如果平台强制以 Gradio 入口启动，需要额外写一个壳来挂载/反向代理 FastAPI；当前项目没有这层壳

---

## 4. 可选：启用模型兜底

默认关闭。需要时在环境变量设置：

```env
AGENT_LLM_ENABLED=true
AGENT_LLM_PROVIDER=anthropic
AGENT_LLM_BASE_URL=https://opencode.ai/zen/go
AGENT_LLM_API_KEY=...
AGENT_LLM_MODEL=deepseek-v4.1-flash
AGENT_LLM_PROTOCOL=anthropic
```

- 只用于规则无法识别时的日期/时间补位
- 模型必须返回原文证据，否则不采纳
- 不要把密钥写进仓库
- 如果平台访问该域名失败，保持关闭，主链路不受影响

---

## 5. 可选：恢复语音 / OCR

### 方案 1：Linux 本机引擎

在 Docker 镜像里安装：

```bash
python -m venv /app/local-ai/.venv
/app/local-ai/.venv/bin/pip install rapidocr_onnxruntime faster-whisper
```

下载 `whisper-small` 到：

```
/app/local-ai/models/whisper-small
```

安装 ffmpeg，或把 Linux ffmpeg 放到：

```
/app/local-ai/ffmpeg/ffmpeg
```

然后设置：

```env
LOCAL_AI_DIR=/app/local-ai
LOCAL_AI_MODELS=/app/local-ai/models
FFMPEG_BIN=/app/local-ai/ffmpeg/ffmpeg
```

### 方案 2：远程 ASR / OCR

当前版本没有远程 ASR/OCR provider；需要新增服务适配层后再开放入口。

---

## 6. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 容器启动但页面打不开 | 只监听 127.0.0.1 | 设置 `HOST=0.0.0.0` |
| 502 / 应用未就绪 | 启动命令或端口不对 | 确认 `python run.py` 和 `9870` |
| 登录后数据丢失 | `/data` 没有持久化 | 挂载持久卷或导出备份 |
| SMTP 密码失效 | `APP_SECRET` 变化 | 固定 `APP_SECRET`，重新填 SMTP 授权码 |
| 语音/OCR 不可用 | Windows 本地引擎未进入 Linux 镜像 | 按第 5 节处理，或先关闭入口 |
| 邮件发送超时 | 平台封 SMTP / 端口不通 | 465/587 切换，或改用预览 |
| 静态样式 404 | 只上传了后端 | 确认 `app/static/` 完整上传 |
| 数据串号 | 多账号共用同一浏览器 | 退出后重新登录；数据按账号隔离 |

---

## 7. 部署验收清单

- [ ] `/api/health` 返回 `ok`
- [ ] 注册第一个账号成功
- [ ] 基本表可以新增字段、编辑、自动保存
- [ ] Excel 导入三种模式可用
- [ ] 行程能生成、预览、写入、清除
- [ ] 卡片能打开和下载
- [ ] 第二个账号看不到第一个账号的数据
- [ ] SMTP 测试邮件能收到
- [ ] 重启后数据仍在（或明确接受临时存储）
- [ ] 手机宽度无横向页面溢出

---

## 8. 安全建议

- 公网创空间必须设置 `APP_ACCESS_CODE`
- `APP_SECRET` 使用平台 Secret，不要提交仓库
- 不要把 `config/token.txt`、`.env`、真实客户数据上传
- 演示账号不要使用真实客户电话/邮箱
- 公开前先确认 SMTP 密码加密和 `/data` 权限
