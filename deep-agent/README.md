# 智能项目管理 Agent 系统

> 老板只负责下达目标与决策，项目经理 Agent 负责任务拆解、排期与跟进，专家子 Agent 按标准软件研发流程执行。所有执行异步进行，控制台实时可视化。

Windows 轻量版实现，基于 FastAPI + SQLite + asyncio 单进程多协程架构。前端用 Vue 3 + Element Plus (CDN)，无需构建即可运行。

---

## 目录结构

```
deep-agent-zh/
├── main.py                 # uvicorn 入口
├── start.bat / start.sh    # 一键启动
├── requirements.txt
├── .env.example            # 环境变量示例
├── README.md
├── app/                    # 后端
│   ├── config.py           # 配置（pydantic-settings）
│   ├── logging.py
│   ├── db/                 # ORM + Repository
│   │   ├── models.py       # 5 张表
│   │   ├── session.py      # 异步引擎 / WAL
│   │   ├── repository.py
│   │   └── schemas.py      # API DTO
│   ├── infra/              # 基础设施
│   │   ├── queues.py       # TaskQueue
│   │   └── bus.py          # EventBus
│   ├── agents/             # 专家 Agent
│   │   ├── base.py
│   │   ├── pool.py
│   │   └── experts.py      # 默认 5 个专家
│   ├── pm/                 # 项目经理 Agent
│   │   ├── planner.py      # 任务拆解
│   │   └── conversational.py # 聊天 + 状态机
│   ├── engine/             # 引擎
│   │   ├── scheduler.py
│   │   ├── workflow_engine.py
│   │   └── heartbeat.py
│   └── api/                # FastAPI
│       ├── app.py
│       ├── routes.py
│       └── ws.py           # WebSocket Hub
└── frontend/               # 前端 (Vue 3 + Element Plus CDN)
    ├── index.html
    ├── styles.css
    └── app.js
```

---

## 快速开始

### Windows

```cmd
start.bat
```

### macOS / Linux

```bash
chmod +x start.sh
./start.sh
```

启动后访问：<http://127.0.0.1:8765/>

如需修改端口：`set DAGENT_PORT=9000`（Windows）或 `DAGENT_PORT=9000 ./start.sh`（Unix）。

### 手动启动

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
# source .venv/bin/activate    # macOS/Linux
pip install -r requirements.txt
python main.py
```

---

## 业务流程

1. 老板在左侧聊天框下达需求，例如：
   > "做一个能记录每日开支并按月统计的小程序"
2. 项目经理 Agent 自动创建父任务并生成初步拆解（多个工作项）。
3. 老板可以：
   - 回复「确认」/「开工」/「OK」→ 项目经理将父任务排入调度队列。
   - 回复「调整: …」/「拆分 …」→ 项目经理调整计划。
4. 调度器把每个子工作项投入 `workflow_queue`，流程引擎按 **需求 → 设计 → 设计评审 → 开发 → 代码评审 → 测试 → 测试评审** 的标准模板流转。
5. 每个阶段：
   - 由对应专家 Agent 执行（默认规则回退；可接 LLM）。
   - 心跳协程每 10s 扫描超时任务并自动重试。
   - 评审类阶段失败时进入「待人工评审」状态，等待老板决策。
6. 老板可在「评审中心」抽屉里手动通过/打回任意阶段。
7. **看板上支持拖拽**：把任务卡拖到不同状态的列，状态直接更新。
8. **详情页带甘特图**：自动渲染父任务 + 各工作项的时间线。

所有状态变更通过 `EventBus` 实时推送到 WebView 控制台。

## 前端组件（CDN 引入，无需构建）

| 模块 | 组件 | 用途 |
|---|---|---|
| 整体框架 | Vue 3 (CDN) + Element Plus (CDN) | 响应式布局、表格、表单、Tag、Progress |
| 看板拖拽 | [SortableJS](https://github.com/SortableJS/Sortable) | 任务卡拖到不同状态列 |
| 甘特图 | [frappe-gantt](https://github.com/frappe/gantt) | 父任务 + 工作项时间线 |
| 图标 | emoji + Unicode（✓ ✗ ⏳ ⚙️ ⚡ ＋） | 避免额外 icon 包依赖 |
| HTTP | 原生 fetch | 与 FastAPI 后端通信 |
| WebSocket | 原生 WebSocket | 实时事件推送 |

---

## API 速览

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/chat` | 发送消息（老板 → 项目经理） |
| POST | `/api/chat/confirm` | 老板直接确认计划 |
| GET  | `/api/parents` | 列出全部父任务 |
| GET  | `/api/parents/{id}` | 父任务详情（含工作项、阶段） |
| PATCH | `/api/parents/{id}/status` | 看板拖拽：改父任务状态 |
| GET  | `/api/workflows/{id}` | 工作项详情 |
| GET  | `/api/logs?parent_id=&workflow_id=` | 日志查询 |
| GET  | `/api/conversation/{parent_id}` | 对话历史 |
| POST | `/api/stages/{stage_id}/review` | 人工评审（approve/reject） |
| GET  | `/api/settings` | 读运行时配置（API key 脱敏） |
| PUT  | `/api/settings` | 批量更新运行时配置 |
| POST | `/api/settings/test` | 用当前/指定配置试调 LLM |
| GET  | `/api/health` | 健康检查（队列长度、专家列表） |
| WS   | `/api/ws` | 实时事件通道 |

事件类型（WebSocket 帧 `{"event": ..., "data": ...}`）：

- `parent.created` / `parent.scheduled` / `parent.status`
- `workflow.created` / `workflow.status` / `workflow.progress`
- `stage.status` / `stage.review_needed`
- `chat.message`

---

## 接 LLM

默认情况下，专家 Agent 和项目经理的「规划器」都使用**规则回退**，保证系统在没有 LLM 时也能跑通流程。

要启用 LLM：

1. 安装 `pydantic-ai`（已在 `requirements.txt`）。
2. 设置环境变量：
   ```env
   DAGENT_LLM_MODEL=ollama:qwen2.5:7b
   DAGENT_LLM_BASE_URL=http://127.0.0.1:11434/v1
   # 或
   DAGENT_LLM_MODEL=openai:gpt-4o-mini
   DAGENT_LLM_API_KEY=sk-...
   ```
3. 重启服务。`planner._try_llm_plan` 会自动尝试 LLM，失败时降级到规则。

> 现阶段项目里只有 `planner` 接了 LLM 调用（拆解需求）。后续可继续把专家 Agent 的 `_execute` 改为调用 LLM。

---

## 扩展

- **替换队列**：`app/infra/queues.py` 里的 `TaskQueue` 换成 Redis/RabbitMQ 适配器即可。
- **替换数据库**：`app/db/session.py` 改 engine URL 即可迁到 PostgreSQL。
- **专家横向扩展**：把 `expert_pool.register` 改为 HTTP/gRPC 调用即可。
- **多客户端 UI**：前端是纯静态文件，可用任意 WebView2/CEF/Electron/浏览器加载。

---


---
## 常见问题

### 启动后 `start.bat` 报错「`dp0` 不是内部或外部命令」

`start.bat` 用了 `cd /d "%~dp0"` 进入脚本所在目录，依赖 CRLF 行尾。
如果行尾被改成 LF（LF-only 文件）或者用了非 UTF-8 编码，`%~dp0` 会被截断报错。
项目里的 `start.bat` 已用二进制写为 CRLF + 纯英文 cmd 语法，应能直接运行。

### 日志里中文乱码

`start.bat` 里有 `chcp 65001` 切到 UTF-8，Python 进程在 `main.py` 入口会强制把 stdout/stderr 切到 UTF-8。
如果在 PowerShell ISE 或其他非 UTF-8 终端看到乱码，那只是终端显示问题，文件里是正常 UTF-8。

### 日志里「LLM 计划生成失败，降级到规则版」

说明没接 LLM（或者 LLM 调用失败）。点击顶栏「LLM: 未配置」按钮，选厂商、填 API key、保存即可。
不接 LLM 也能跑（规则回退模式），但智能度有限。

### 「LLM: 未配置」按钮点不动 / 点了对话框是空的

1. 浏览器**强制刷新**：Ctrl+Shift+R（跳过缓存）。
2. 如果还不行，按 F12 打开 DevTools，看 Console 是否有「Failed to resolve component」之类的警告。
3. 顶栏按钮是原生 `<el-button>` 渲染的，不是 icon span。

### 如何重置数据库

删除 `data/dagent.db`（以及 `dagent.db-wal`、`dagent.db-shm`），重启服务即可。

### 前端加载慢

首次访问会从 CDN 拉 Vue / Element Plus / SortableJS / frappe-gantt 等，~1-2s。后续会被浏览器缓存。

---
## 开发与测试

- 单元测试：待补；核心模块都可以 `pytest` 测。
- 重置数据库：删除 `data/dagent.db` 即可。
- 关闭服务：Ctrl+C。

---

## 协议

仅供学习与原型验证。
